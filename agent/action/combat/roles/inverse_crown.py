# Copyright (c) 2024-2025 MAA_Punish
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""逆冕战斗程序。

普通大招先等待 ready，再单次长按并观察 empty；确认后才进入 finish，
观察预算耗尽则拒绝本轮。固定长按时长不证明游戏内蓄力已满。

输出循环（2026-11-10 依用户手录 60Hz 视频实证升级；阈值与时长为临时
先验，待真机验收）：普攻积球 → 红环 ≈12/16 → 长按第1槽特殊球 →
长按普攻蓄力横扫 → 大招分派（cage=长按大招 / 其余=红环即点循环）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, fields, replace
from enum import Enum
import json
import math

import numpy as np

from action.combat.core.role import BaseRole
from action.combat.core.provider import HpObservation
from action.combat.config.inverse_crown_profile import InverseCrownUnavailable
from action.combat.input_control import CombatInputError
from action.combat.input_control import primary_input_position
from action.basics.InterfaceZone import roi_zone_controller as roi_zones
from action.basics.InterfaceZone.hud_roi_calibration import hud_overrides_from_cache

_ENERGY_FULL_NODE = "检查逆冕能量即将满"
_ENERGY_EMPTY_NODE = "检查逆冕能量空"
_SPECIAL_BALL_NODE = "检查逆冕特殊球"
_SKILL_CHARGE_TIMEOUT = 10.0
_SKILL_CAST_MAX = 20
_FARM_MAX_TICKS = 20
_SPECIAL_BALL_MIN = 7
# 红环就绪阈值：手录视频实证特殊球 ≈12/16 时蓝圈亮红环（非 16 满）。
# SSS 起斩切球由 8 增至 12（方案 3.2 阶级表）；未识别阶级不继承账号上限。
_RED_RING_READY_BASE = 8
_RED_RING_READY_SSS = 12
# 以下时长为临时先验，未经真机标定；只作输入预算，不证明游戏内状态。
_BURST_HOLD_MS = 3000
_CHARGED_SLASH_MS = 4000
_TAP_CAST_MAX = 6
_SINGLE_PREPARE_TIMEOUT = 30.0
_SINGLE_FINISH_TIMEOUT = 15.0
_SINGLE_SKILL_HOLD_SECONDS = 2.0

# Budgets for the experimental signal-driven consumer. The live single-target
# input cycle uses its own preparation/consumption limits and bounded holds.
_STATIONARY_BUDGETS = {
    "prepare": 5.0,
    "special_hold": 5.0,
    "attack_settlement": 5.0,
    "ultimate_charge": 10.0,
    "slash_attack": 10.0,
    "ultimate_finish": 10.0,
}
_SNAPSHOT_MAX_AGE = 1.0  # Host acquisition-completion age, not device frame age.


def _stationary_special_color(image, node) -> str:
    """Read conservative BGR color evidence, never activation or skill phase.

    The inner-circle proportions and channel margins were checked against the
    labelled 720p red/blue frames. Other layouts, gray slots and real occlusion
    still need device examples; unsupported or conflicting evidence is UNKNOWN.
    """
    if (
        not isinstance(image, np.ndarray) or image.dtype != np.uint8
        or image.ndim != 3 or image.shape[2] != 3 or not image.size
        or not isinstance(node, dict)
    ):
        return "unknown"
    recognition = node.get("recognition")
    param = recognition.get("param") if isinstance(recognition, dict) else None
    if not isinstance(param, dict):
        return "unknown"
    roi, offset = param.get("roi"), param.get("roi_offset")
    if any(
        not isinstance(rect, list) or len(rect) != 4
        or any(type(value) is not int for value in rect)
        for rect in (roi, offset)
    ):
        return "unknown"
    x, y, width, height = (value + shift for value, shift in zip(roi, offset))
    if (
        roi[2] <= 0 or roi[3] <= 0 or offset[2:] != [0, 0]
        or min(width, height) < 20
        or x < 0 or y < 0 or x + width > image.shape[1]
        or y + height > image.shape[0]
    ):
        return "unknown"

    # Stay inside the colored center: the outer ring can itself contain red
    # while the center is blue. int16 prevents uint8 subtraction wrapping.
    pixels = image[y:y + height, x:x + width].astype(np.int16)
    blue, green, red = (pixels[..., channel] for channel in range(3))
    blue_support = (blue >= 90) & (blue - red >= 25) & (blue - green >= 8)
    red_support = (red >= 90) & (red - blue >= 25) & (red - green >= 25)
    yy, xx = np.ogrid[:height, :width]
    cx, cy = (width - 1) / 2, (height - 1) / 2
    inner = (xx - cx) ** 2 + (yy - cy) ** 2 <= (min(width, height) * 0.22) ** 2
    quadrants = (
        inner & (xx < cx) & (yy < cy), inner & (xx >= cx) & (yy < cy),
        inner & (xx < cx) & (yy >= cy), inner & (xx >= cx) & (yy >= cy),
    )
    for color, support, opposite in (
        ("blue", blue_support, red_support), ("red", red_support, blue_support),
    ):
        if (
            np.mean(support[inner]) >= 0.60
            and np.mean(opposite[inner]) <= 0.02
            and all(np.mean(support[quadrant]) >= 0.40 for quadrant in quadrants)
        ):
            return color
    return "unknown"


class _Signal(Enum):
    UNKNOWN = "unknown"
    NO = "no"
    YES = "yes"


@dataclass(frozen=True, slots=True)
class _StationaryVisualGeometry:
    screen_size: tuple[int, int]
    resource_roi: tuple[int, int, int, int]
    skill_roi: tuple[int, int, int, int]
    skill_center: tuple[int, int]
    core_roi: tuple[int, int, int, int] | None = None


def _supported_visual_geometry(image, geometry) -> bool:
    """Only the manually compared 720p dimensions are supported initially."""
    if (
        not isinstance(image, np.ndarray) or image.dtype != np.uint8
        or image.shape != (720, 1280, 3)
        or not isinstance(geometry, _StationaryVisualGeometry)
        or type(geometry.screen_size) is not tuple
        or any(type(value) is not int for value in geometry.screen_size)
        or geometry.screen_size != (1280, 720)
    ):
        return False
    for rect in (geometry.resource_roi, geometry.skill_roi):
        if (type(rect) is not tuple or len(rect) != 4
                or any(type(value) is not int for value in rect)):
            return False
        x, y, width, height = rect
        if x < 0 or y < 0 or x + width > 1280 or y + height > 720:
            return False
    x, _, width, height = geometry.resource_roi
    if (width, height) != (213, 3) or x <= 0 or x + width >= 1280:
        return False  # Both adjacent columns must be observable for EMPTY.
    x, y, width, height = geometry.skill_roi
    return (
        (width, height) == (124, 126)
        and type(geometry.skill_center) is tuple and len(geometry.skill_center) == 2
        and all(type(value) is int for value in geometry.skill_center)
        and geometry.skill_center == (x + width // 2, y + height // 2)
    )


def _stationary_resource_appearance(image, geometry) -> str:
    """Describe the complete calibrated bar; never infer a resource value."""
    if not _supported_visual_geometry(image, geometry):
        return "unknown"
    x, y, width, height = geometry.resource_roi
    pixels = image[y:y + height, x:x + width].astype(np.int16)

    def warm(bgr):
        blue, green, red = (bgr[..., channel] for channel in range(3))
        return (red >= 120) & (red - blue >= 50) & (red - green >= 20)

    blue, green, red = (pixels[..., channel] for channel in range(3))
    filled = warm(pixels)
    # A full bar's endpoint blooms toward warm white. The observed bloom is
    # confined to the last five columns. Dark gaps and white elsewhere still
    # cannot establish a full bar.
    endpoint_glow = (blue >= 200) & (green >= 210) & (red >= 245) & (red > green) & (green > blue)
    if np.all(filled[:, :-5]) and np.all(filled[:, -5:] | endpoint_glow[:, -5:]):
        return "full"
    dark = (
        (blue >= 35) & (blue <= 110) & (green >= 35) & (green <= 115)
        & (red >= 60) & (red <= 125) & (red - blue >= 8)
        & (red - blue <= 40) & (red - green >= 5) & (red - green <= 40)
    )
    if np.all(dark):
        boundaries = image[y:y + height, [x - 1, x + width]].astype(np.int16)
        if np.all(np.count_nonzero(warm(boundaries), axis=0) >= 2):
            return "empty"
    return "unknown"


def _stationary_skill_blue_ring(image, geometry) -> _Signal:
    """Positive distributed blue visibility only, never skill readiness/NO."""
    if not _supported_visual_geometry(image, geometry):
        return _Signal.UNKNOWN
    x, y, width, height = geometry.skill_roi
    cx, cy = geometry.skill_center
    yy, xx = np.ogrid[y:y + height, x:x + width]
    dx, dy = xx - cx, yy - cy
    radius = np.sqrt(dx ** 2 + dy ** 2)
    size = min(width, height)
    ring = (radius >= 0.41 * size) & (radius <= 0.50 * size)
    pixels = image[y:y + height, x:x + width].astype(np.int16)
    blue, green, red = (pixels[..., channel] for channel in range(3))
    cyan = (blue >= 150) & (green >= 90) & (blue - red >= 25) & (blue - green >= 5)
    sectors = (np.arctan2(dy, dx) % (2 * np.pi) / (np.pi / 4)).astype(np.int8)
    supported = sum(np.mean(cyan[ring & (sectors == sector)]) >= 0.50
                    for sector in range(8))
    center = radius <= 0.28 * size
    white = np.all(pixels >= 200, axis=2) & (np.max(pixels, axis=2) - np.min(pixels, axis=2) <= 35)
    if np.mean(cyan[ring]) >= 0.50 and supported >= 6 and np.mean(white[center]) >= 0.03:
        return _Signal.YES
    return _Signal.UNKNOWN


def _stationary_abyss_shape(image, geometry) -> _Signal:
    """Recognize only the sampled smooth hilt/blade root, never absence/NO.

    The core display is shared by charging/other moves. Only the caller's
    special-hold/necessary-attack context may give this shape phase meaning.
    These landmarks are specific to the 720p layout in the real ROI fixtures;
    no scaling, free search, elapsed time or previous-frame result is used.
    """
    if (
        not _supported_visual_geometry(image, geometry)
        or geometry.resource_roi != (76, 62, 213, 3)
        or geometry.skill_roi != (942, 594, 124, 126)
        or geometry.core_roi != (493, 602, 294, 50)
    ):
        return _Signal.UNKNOWN
    # Absolute x539..604/y619..637 includes the segmented left hilt, short
    # blade root and clean bands above/below. The remaining blade may be grey
    # as the countdown shrinks; its length must not become a phase timer.
    pixels = image[619:638, 539:605].astype(np.int16)
    blue, green, red = (pixels[..., channel] for channel in range(3))
    pink = (red >= 180) & (red - green >= 50) & (red - blue >= 25)
    hilt_and_root = (pink[7:12, 4:19], pink[7:12, 23:40], pink[7:12, 43:58])
    lower_tips = (pink[13, 9:13], pink[13, 51:56])
    if (
        all(np.mean(part) >= 0.95 for part in hilt_and_root)
        and all(np.mean(part) >= 0.75 for part in lower_tips)
        # The notch and two shallow shoulders distinguish the smooth hilt
        # from a solid stripe and the wider, serrated charge appearance.
        and np.mean(pink[9, 19:23]) <= 0.25
        and np.mean(pink[6, 8:48]) <= 0.05
        and np.mean(pink[12, 21:42]) <= 0.15
        and np.mean(pink[:5]) <= 0.01
        and np.mean(pink[14:]) <= 0.01
    ):
        return _Signal.YES
    return _Signal.UNKNOWN


@dataclass(frozen=True, slots=True)
class _StationarySignals:
    generation: int
    sequence: int
    captured_at: float
    hp: HpObservation | None = None
    special_color: str = "unknown"
    resource_appearance: str = "unknown"
    skill_blue_ring: _Signal = _Signal.UNKNOWN
    special_activated: _Signal = _Signal.UNKNOWN
    abyss_phase: _Signal = _Signal.UNKNOWN
    settlement_required: _Signal = _Signal.UNKNOWN
    long_attack_complete: _Signal = _Signal.UNKNOWN
    ultimate_ready: _Signal = _Signal.UNKNOWN
    ultimate_charge_complete: _Signal = _Signal.UNKNOWN
    ultimate_finished: _Signal = _Signal.UNKNOWN
    slash_phase: _Signal = _Signal.UNKNOWN
    initial_basis: str = "unknown"

    def __post_init__(self):
        if self.special_color not in {"red", "blue", "unknown"}:
            raise ValueError("invalid special-ball color")
        if type(self.resource_appearance) is not str or self.resource_appearance not in {"empty", "full", "unknown"}:
            raise ValueError("invalid resource appearance")
        if type(self.skill_blue_ring) is not _Signal or self.skill_blue_ring not in {_Signal.YES, _Signal.UNKNOWN}:
            raise ValueError("blue-ring visibility requires positive or unknown evidence")
        if type(self.initial_basis) is not str or self.initial_basis not in {"unknown", "same_call_entry_rule_inference"}:
            raise ValueError("invalid initial signal source")
        if self.initial_basis == "same_call_entry_rule_inference" and (
            self.settlement_required is not _Signal.NO or self.special_activated is not _Signal.YES
        ):
            raise ValueError("initial inference must carry both first-opportunity facts")
        for item in fields(self):
            if item.name not in {"generation", "sequence", "captured_at", "hp", "special_color", "resource_appearance", "initial_basis"}:
                if not isinstance(getattr(self, item.name), _Signal):
                    raise ValueError("stationary observations must use three-valued signals")


class InverseCrown(BaseRole):
    """逆冕普通轮切与显式单体站场循环。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._skill_charge_deadline = 0.0
        self._skill_cast_ticks = 0
        self._skill_sub = ""
        self._skill_switch_after = False
        self._farm_ticks = 0
        self._post_ult_qte_used = False
        self._stationary_deadline = None
        self._stationary_last_sequence = 0
        self._settlement_pending = False
        # 长期资源记账（方案 3.5：阶段资源/补资源动作/耗时；循环复位只清统计）。
        self._resource_stats = {"rebuild_taps": 0, "started_at": None}
        # 恢复当拍抑制盲开大（方案 3.5 恢复/退出行）。
        self._recovery_resume = False

    def reset_state(self) -> None:
        entry = getattr(self.combat, "fresh_entry", None)
        if entry is not None and entry.role is self:
            entry.retire()
        if self.inverse_crown_profile.enabled and (
            self.phase not in {"idle", "prepare"}
            or (self.phase == "prepare" and self._stationary_deadline is not None)
        ):
            # A cycle reset cannot erase an unfinished hold/settlement or failure.
            return
        stats = self._resource_stats
        if stats["rebuild_taps"]:
            elapsed = time.monotonic() - stats["started_at"] if stats["started_at"] else 0.0
            self.action.logger.info(
                "inverse_crown 资源统计: rebuild_taps=%d elapsed=%.1fs",
                stats["rebuild_taps"], elapsed,
            )
        self._resource_stats = {"rebuild_taps": 0, "started_at": None}
        # 方案3.4：循环复位只清辅助统计，真实冷却与待确认请求保留。
        self.action.assist_tracker.cycle_reset()
        super().reset_state()
        self._skill_charge_deadline = 0.0
        self._skill_cast_ticks = 0
        self._skill_sub = ""
        self._skill_switch_after = False
        self._farm_ticks = 0
        self._post_ult_qte_used = False
        self._stationary_deadline = None

    def is_input_protected(self) -> bool:
        # Special-ball holding, its required attack settlement, and ultimate
        # release keep protection. Ordinary charge only attacks while waiting
        # for readiness, so shared warning reactions must remain available.
        return self.inverse_crown_profile.enabled or self.phase in {
            "special_press", "special_attack", "burst_hold", "burst_slash",
        } or (
            self.phase == "skill" and self._skill_sub in {
                "cast", "cage", "finish", "tap_cast",
            }
        )

    def do_perform(self) -> None:
        if self.inverse_crown_profile.enabled:
            if self.inverse_crown_profile.scene == "single":
                self._perform_single()
            else:
                self._perform_stationary()
            return
        if self.combat.context.tasker.stopping:
            return

        if self.phase == "idle":
            self._phase_idle()
        elif self.phase == "ball_dump":
            self._phase_ball_dump()
        elif self.phase == "special_press":
            self._phase_special_press()
        elif self.phase == "special_attack":
            self._phase_special_attack()
        elif self.phase == "burst_hold":
            self._phase_burst_hold()
        elif self.phase == "burst_slash":
            self._phase_burst_slash()
        elif self.phase == "skill":
            self._phase_skill()
        elif self.phase == "farm":
            self._phase_farm()
        else:
            self.action.logger.info(
                "inverse_crown recovery: requested=%s actual=undispatched_phase",
                self.phase,
            )
            self._recovery_resume = True
            self.phase = "idle"
            self._phase_idle()

    def _single_hold(self, node_name: str, seconds: float) -> None:
        """Execute one bounded input, releasing through the existing scope.

        The elapsed interval completes the requested hold, not a claim about
        the game's charge state. Shared frame observation still checks stop,
        death and battle exit. Ultimate consumption is checked separately.
        """
        scope = self.combat.input_scope
        scope.assert_active()
        if getattr(self.combat, "fresh_entry", None) is not None:
            self.combat.fresh_entry.retire()
        started = scope.monotonic()

        def observe(image):
            if self.combat._hp_zero_since is not None:
                scope.request_stop("hp_zero")
                raise CombatInputError("inverse_crown:hp_zero")
            return scope.monotonic() - started >= seconds

        completed = self.action.hold_primary_input(
            node_name,
            duration=seconds + scope.capture_timeout + scope.input_timeout + 2.0,
            observe=observe,
        )
        if completed is not True or scope.owned_contact is not None or scope.release_pending:
            raise CombatInputError("inverse_crown:single_hold_unconfirmed")
        scope.assert_active()
        self.action.logger.info(
            "inverse_crown_single input=%s requested_ms=%d elapsed_ms=%d released=true",
            node_name, round(seconds * 1000), round((scope.monotonic() - started) * 1000),
        )

    def _single_prepare(self) -> None:
        self.phase = "prepare"
        self._stationary_deadline = self.combat.input_scope.monotonic() + _SINGLE_PREPARE_TIMEOUT

    def _perform_single(self) -> None:
        """Single-target standing cycle using the confirmed input sequence.

        An activated special-ring match starts the special/attack pair. The
        required attack is always released before considering an ultimate.
        Full resource plus the illuminated skill button authorizes the skill,
        and a later empty resource observation confirms its consumption.
        """
        scope = self.combat.input_scope
        if scope is None:
            raise CombatInputError("inverse_crown:input_scope_missing")
        try:
            scope.assert_active()
            if self.phase == "idle":
                self._single_prepare()
            if self.phase not in {"prepare", "ultimate_finish"}:
                raise CombatInputError("inverse_crown:unfinished_single_stage")
            if self._settlement_pending:
                raise CombatInputError("inverse_crown:unfinished_single_settlement")
            self._check_stationary_budget()
            # Identity recognition can use most of a frame's age budget.
            # Capture once at the decision point, before observing HP and HUD.
            scope.capture_frame()
            snapshot = self._observe_stationary(
                self.combat.frame, allow_missing_hp=True,
            )
            if snapshot is None:
                # Effects/cinematics can briefly hide HP, and result pages
                # remove it. Observe without input under the unchanged stage
                # deadline. Normal input paths already share this exit check;
                # duplicating it on every frame would spend the freshness budget.
                if self.combat.observe_hold_state(self.combat.frame):
                    self.combat._in_combat = False
                return
            self._check_stationary_budget()
            if self.phase == "ultimate_finish":
                if snapshot.resource_appearance == "empty":
                    self.action.logger.info("inverse_crown_single ultimate_resource_consumed=true")
                    self._single_prepare()
                return
            if snapshot.ultimate_ready is _Signal.YES:
                self.phase = "ultimate_charge"
                self._single_hold("技能", _SINGLE_SKILL_HOLD_SECONDS)
                self.phase = "ultimate_finish"
                self._stationary_deadline = scope.monotonic() + _SINGLE_FINISH_TIMEOUT
                return
            special = self.action.check_status(_SPECIAL_BALL_NODE, image=self.combat.frame)
            self._validate_stationary_snapshot(snapshot, retained=True)
            if special:
                self.phase = "special_hold"
                self._single_hold("长按1号球", _BURST_HOLD_MS / 1000)
                self.phase = "attack_settlement"
                self._settlement_pending = True
                # Keep the pair in this call. A main-loop role scan or QTE
                # here can spend the short special-to-attack transition.
                self._single_hold("攻击", _CHARGED_SLASH_MS / 1000)
                self._settlement_pending = False
                self._single_prepare()
                return
            # No usable special or ultimate yet. Ordinary attacks replenish
            # blade resource without consuming a teammate's switch/QTE button.
            self._rebuild_stationary_resource(snapshot)
        except BaseException as exc:
            failed_phase = self.phase
            self.phase = "recover"
            scope.fail(f"inverse_crown:requested={failed_phase}/{self._skill_sub or '-'}_actual_failed:{type(exc).__name__}")
            raise

    def _read_stationary_signals(self, image) -> _StationarySignals:
        """Consume only the supplied scope frame; no controller or input access.

        The smooth core shape has phase meaning only during our own single
        target special/necessary-attack chain. Other contexts, absence and
        obstruction remain UNKNOWN. In the sampled prepare layout, the full
        bar and blue rim have a same-field skill-start positive; availability
        still does not establish cleared settlement or any action completion.
        """
        metadata = self.combat.input_scope.frame_metadata
        phase = getattr(self, "phase", None)
        try:
            node = self.combat.context.get_node_data(_SPECIAL_BALL_NODE)
        except Exception:
            node = None
        geometry = self._stationary_visual_geometry(image)
        resource_appearance = _stationary_resource_appearance(image, geometry)
        skill_blue_ring = _stationary_skill_blue_ring(image, geometry)
        abyss_phase = _Signal.UNKNOWN
        if (
            phase in {"special_hold", "attack_settlement"}
            and getattr(self, "phase", None) == phase
            and self.inverse_crown_profile.enabled
            and self.inverse_crown_profile.scene == "single"
        ):
            abyss_phase = _stationary_abyss_shape(image, geometry)
        ultimate_ready = _Signal.UNKNOWN
        if (
            phase == "prepare" and getattr(self, "phase", None) == phase
            and self.inverse_crown_profile.enabled
            and self.inverse_crown_profile.scene == "single"
            and resource_appearance == "full" and skill_blue_ring is _Signal.YES
            and geometry.resource_roi == (76, 62, 213, 3)
            and geometry.skill_roi == (942, 594, 124, 126)
            and geometry.core_roi == (493, 602, 294, 50)
        ):
            ultimate_ready = _Signal.YES
        initial = False
        entry = getattr(self.combat, "fresh_entry", None)
        if entry is not None and geometry is None:
            entry.retire()
        if entry is not None and geometry is not None and phase == "prepare" and self.phase == phase:
            initial = entry.available(self.combat, self)
        return _StationarySignals(
            metadata.generation, metadata.sequence, metadata.captured_at,
            special_color=_stationary_special_color(image, node),
            resource_appearance=resource_appearance,
            skill_blue_ring=skill_blue_ring,
            abyss_phase=abyss_phase,
            ultimate_ready=ultimate_ready,
            settlement_required=_Signal.NO if initial else _Signal.UNKNOWN,
            special_activated=_Signal.YES if initial else _Signal.UNKNOWN,
            initial_basis="same_call_entry_rule_inference" if initial else "unknown",
        )

    def _stationary_visual_geometry(self, image) -> _StationaryVisualGeometry | None:
        """Recheck saved HUD/current Context without capturing or applying nodes.

        Cache hashes bind geometry, not element semantics. Initial use is limited
        to the manually compared HUD dimensions; layout/skin changes need a new
        comparison. No calibration or successful observation is retained here.
        """
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.shape != (720, 1280, 3):
            return None
        context = self.combat.context
        try:
            # entry.__init__ loads the runner/session, so resolve this helper
            # only after role discovery has finished, never at module import.
            from action.combat.entry.inverse_crown_layout import _controller as layout_controller

            controller = layout_controller(context)
            path = roi_zones.offset_path(controller)
            saved_bytes = path.read_bytes()
            saved = json.loads(saved_bytes)
            overrides = hud_overrides_from_cache(saved, controller, (1280, 720))

            def read_hud():
                snapshot = []
                for name, kind in (("检查血量", "OCR"), (_ENERGY_EMPTY_NODE, "ColorMatch")):
                    node = context.get_node_data(name)
                    recognition = node.get("recognition") if isinstance(node, dict) else None
                    if not isinstance(recognition, dict) or recognition.get("type") != kind:
                        return None
                    param = recognition.get("param")
                    if not isinstance(param, dict):
                        return None
                    roi, offset = param.get("roi"), param.get("roi_offset")
                    if any(not isinstance(rect, list) or len(rect) != 4
                           or any(type(value) is not int for value in rect) for rect in (roi, offset)):
                        return None
                    if offset != [0, 0, 0, 0] or roi != overrides[name]["recognition"]["param"]["roi"]:
                        return None
                    snapshot.append((name, kind, tuple(roi), tuple(offset)))
                return tuple(snapshot)

            hud_snapshot = read_hud()
            if hud_snapshot is None:
                return None
            skill_roi = tuple(saved["skill_zone"]["recognized"])
            center = primary_input_position(context, "技能")
            geometry = _StationaryVisualGeometry(
                (1280, 720), tuple(overrides[_ENERGY_EMPTY_NODE]["recognition"]["param"]["roi"]),
                skill_roi, center, tuple(saved["corepass_zone"]["recognized"]),
            )
            # One bounded readback after the synchronous skill lookup. Copy
            # parameters so mutation of an earlier returned dict cannot change
            # the initial snapshot. This is neither atomic nor a native deadline.
            if layout_controller(context) != controller or read_hud() != hud_snapshot:
                return None
            if context is not self.combat.context or path.read_bytes() != saved_bytes:
                return None
            return geometry if _supported_visual_geometry(image, geometry) else None
        except Exception:
            return None

    def _validate_stationary_snapshot(self, snapshot, *, retained=False):
        scope = self.combat.input_scope
        scope.assert_active()
        metadata = scope.last_frame_metadata if retained else scope.frame_metadata
        if (
            not isinstance(snapshot, _StationarySignals)
            or metadata is None
            or snapshot.hp is None
            or not snapshot.hp.alive
            or snapshot.generation != scope.generation
            or (snapshot.generation, snapshot.sequence, snapshot.captured_at)
            != (metadata.generation, metadata.sequence, metadata.captured_at)
            or not math.isfinite(snapshot.captured_at)
            or not 0 <= scope.monotonic() - snapshot.captured_at <= _SNAPSHOT_MAX_AGE
        ):
            raise CombatInputError("inverse_crown:invalid_or_expired_snapshot")
        if retained:
            if snapshot.sequence != self._stationary_last_sequence:
                raise CombatInputError("inverse_crown:handoff_snapshot_replaced")
        elif snapshot.sequence <= self._stationary_last_sequence:
            raise CombatInputError("inverse_crown:repeated_snapshot")

    def _observe_stationary(self, image, *, allow_missing_hp=False) -> _StationarySignals | None:
        scope = self.combat.input_scope
        scope.assert_active()
        if image is None or image is not self.combat.frame or scope.frame_metadata is None:
            raise CombatInputError("inverse_crown:observation_frame_invalid")
        metadata = scope.frame_metadata
        hp = self.combat.combat_check.read_hp_from_frame(self.combat.context, image)
        if self.combat.input_scope is not scope:
            raise CombatInputError("inverse_crown:observation_scope_replaced")
        scope.assert_active()
        if scope.frame_metadata is not metadata or self.combat.frame is not image:
            raise CombatInputError("inverse_crown:hp_frame_replaced")
        self._check_stationary_budget()
        if not hp.alive:
            # Effects and ultimate cinematics can hide the HP text. Only a
            # recognition miss permits this released-input wait; malformed HP
            # and recognition errors still fail. No input/consumption is inferred.
            if allow_missing_hp and hp.reason == "hp_not_recognized":
                return None
            raise CombatInputError(f"inverse_crown:hp_refused:{hp.reason}")
        # OCR is synchronous: positive text is insufficient if that read spent
        # the stage budget, became stale, or replaced this scope/frame binding.
        self._validate_stationary_snapshot(_StationarySignals(
            metadata.generation, metadata.sequence, metadata.captured_at, hp=hp,
        ))
        snapshot = replace(self._read_stationary_signals(image), hp=hp)
        if self.combat.input_scope is not scope:
            raise CombatInputError("inverse_crown:observation_scope_replaced")
        self._validate_stationary_snapshot(snapshot)
        self._stationary_last_sequence = snapshot.sequence
        if self.inverse_crown_profile.scene == "single" and snapshot.slash_phase is _Signal.YES:
            raise CombatInputError("inverse_crown:unexpected_slash_branch")
        return snapshot

    def _enter_stationary(self, phase):
        self.phase = phase
        self._stationary_deadline = (
            self.combat.input_scope.monotonic() + _STATIONARY_BUDGETS[phase]
        )

    def _check_stationary_budget(self):
        if self.combat.input_scope.monotonic() >= self._stationary_deadline:
            raise CombatInputError(f"inverse_crown:{self.phase}_budget_exhausted")

    def _hold_stationary(self, node_name, snapshot, completed, *, handoff=False):
        """One existing scope hold; return only a newly observed completion.

        Retained metadata supports an immediate Up -> next Down handoff in this
        call. It cannot authorize a later tick or survive an intervening capture.
        """
        scope = self.combat.input_scope
        self._validate_stationary_snapshot(snapshot, retained=True)
        if not handoff and (
            scope.frame_metadata is not scope.last_frame_metadata
            or self.combat.frame is None
        ):
            raise CombatInputError("inverse_crown:current_start_frame_missing")
        if scope.owned_contact is not None or scope.release_pending or scope.capture_in_flight:
            raise CombatInputError("inverse_crown:handoff_input_pending")
        self._check_stationary_budget()
        latest = None

        def should_stop():
            if self.combat.input_scope is not scope:
                raise CombatInputError("inverse_crown:handoff_scope_replaced")
            self._check_stationary_budget()
            # hold_semantic resolves the Context again before Down. Validate
            # after that synchronous read too, without acquiring a controller.
            if scope.owned_contact is None:
                self._validate_stationary_snapshot(snapshot, retained=True)
            return False

        def observe(image):
            nonlocal latest
            self._check_stationary_budget()
            latest = self._observe_stationary(image)
            self._check_stationary_budget()
            return completed(latest)

        if getattr(self.combat, "fresh_entry", None) is not None:
            self.combat.fresh_entry.retire()
        complete = self.action.hold_primary_input(
            node_name,
            duration=self._stationary_deadline - scope.monotonic(),
            observe=observe,
            should_stop=should_stop,
        )
        if complete is not True or latest is None:
            raise CombatInputError(f"inverse_crown:{self.phase}_not_completed")
        if self.combat.input_scope is not scope:
            raise CombatInputError("inverse_crown:handoff_scope_replaced")
        # A release may itself take time or fail. Never advance using an old
        # callback result unless the current scope confirms release and binding.
        self._validate_stationary_snapshot(latest, retained=True)
        self._check_stationary_budget()
        if scope.owned_contact is not None or scope.release_pending:
            raise CombatInputError("inverse_crown:release_not_confirmed")
        return latest

    def _rebuild_stationary_resource(self, snapshot):
        """One ordinary attack; the next tick must supply a new observation."""
        combat = self.combat
        scope, context, image = combat.input_scope, combat.context, combat.frame
        metadata = scope.frame_metadata

        def before_down():
            if (
                self.combat is not combat or combat.input_scope is not scope
                or combat.context is not context or scope.context is not context
                or combat.frame is not image or scope.frame_metadata is not metadata
                or scope.last_frame_metadata is not metadata
                or self.phase != "prepare" or self._settlement_pending
            ):
                raise CombatInputError("inverse_crown:rebuild_binding_changed")
            self._check_stationary_budget()
            self._validate_stationary_snapshot(snapshot, retained=True)
            return False

        before_down()
        if getattr(combat, "fresh_entry", None) is not None:
            combat.fresh_entry.retire()
        if self.action.tap_primary_input(
            "攻击",
            start_deadline=min(
                snapshot.captured_at + _SNAPSHOT_MAX_AGE, self._stationary_deadline
            ),
            should_stop=before_down,
        ) is not True:
            raise CombatInputError("inverse_crown:resource_tap_not_completed")
        if combat.input_scope is not scope or scope.owned_contact is not None or scope.release_pending:
            raise CombatInputError("inverse_crown:resource_tap_release_unconfirmed")
        scope.assert_active()
        self._check_stationary_budget()
        stats = self._resource_stats
        stats["rebuild_taps"] += 1
        if stats["started_at"] is None:
            stats["started_at"] = time.monotonic()
        # Keep prepare's original deadline. Re-entering here could turn an
        # unavailable detector/resource into an unbounded attack loop.

    def _settle_stationary_attack(self, snapshot, *, handoff=False):
        self._settlement_pending = True
        if snapshot.abyss_phase is not _Signal.YES:
            return  # No new Down while the necessary phase is unknown/absent.
        self._hold_stationary(
            "攻击", snapshot,
            lambda current: current.long_attack_complete is _Signal.YES,
            handoff=handoff,
        )
        self._settlement_pending = False
        self._enter_stationary("prepare")

    def _perform_stationary_multi(self, snapshot):
        """Consume an observed slash phase; entry remains unimplemented.

        Neither an uncharged ultimate nor a timer establishes slash entry.
        The hold budget only bounds input while confirmed slash frames persist.
        """
        if self.phase == "ultimate_finish":
            if (
                not self._settlement_pending
                and snapshot.settlement_required is _Signal.NO
                and snapshot.slash_phase is _Signal.NO
                and snapshot.ultimate_finished is _Signal.YES
            ):
                self._enter_stationary("prepare")
            return
        if self.phase != "prepare":
            raise CombatInputError("inverse_crown:unfinished_stationary_stage")
        if self._settlement_pending or snapshot.settlement_required is not _Signal.NO:
            raise InverseCrownUnavailable(
                "multi_slash_settlement_not_clear", actual=self.implementation_name,
            )
        if snapshot.slash_phase is not _Signal.YES:
            raise InverseCrownUnavailable(
                "multi_slash_entry_not_ready", actual=self.implementation_name,
            )

        def exited(current):
            if current.settlement_required is not _Signal.NO:
                raise CombatInputError("inverse_crown:multi_slash_settlement_not_clear")
            if current.slash_phase is _Signal.UNKNOWN:
                raise CombatInputError("inverse_crown:multi_slash_phase_unknown")
            return current.slash_phase is _Signal.NO

        self._enter_stationary("slash_attack")
        self._hold_stationary("攻击", snapshot, exited)
        # Even a YES on the exit frame cannot confirm the next phase. Only a
        # later capture may finish the ultimate after confirmed input release.
        self._enter_stationary("ultimate_finish")

    def _perform_stationary(self):
        if self.inverse_crown_profile.scene not in {"single", "multi"}:
            raise InverseCrownUnavailable("stationary_scene_required", actual=self.implementation_name)
        scope = self.combat.input_scope
        if scope is None:
            raise CombatInputError("inverse_crown:input_scope_missing")
        try:
            scope.assert_active()
            if self.phase == "idle":
                self._enter_stationary("prepare")
            if self.phase not in _STATIONARY_BUDGETS:
                raise CombatInputError("inverse_crown:stationary_state_invalid")
            self._check_stationary_budget()
            snapshot = self._observe_stationary(self.combat.frame)
            self._check_stationary_budget()

            if self.inverse_crown_profile.scene == "multi":
                self._perform_stationary_multi(snapshot)
                return

            if self.phase == "ultimate_finish":
                if snapshot.ultimate_finished is _Signal.YES:
                    self._enter_stationary("prepare")
                return

            if self._settlement_pending or snapshot.settlement_required is _Signal.YES:
                if not self._settlement_pending:
                    self._settlement_pending = True
                    self._enter_stationary("attack_settlement")
                self._settle_stationary_attack(snapshot)
                return

            if self.phase != "prepare":
                raise CombatInputError("inverse_crown:unfinished_stationary_stage")
            # UNKNOWN is not evidence that no necessary settlement is pending.
            if snapshot.settlement_required is not _Signal.NO:
                return
            if snapshot.ultimate_ready is _Signal.YES:
                self._enter_stationary("ultimate_charge")
                self._hold_stationary(
                    "技能", snapshot,
                    lambda current: current.ultimate_charge_complete is _Signal.YES,
                )
                self._enter_stationary("ultimate_finish")
                return
            if (
                snapshot.special_activated is _Signal.YES
                and snapshot.special_color in {"red", "blue"}
            ):
                self._enter_stationary("special_hold")
                completed = self._hold_stationary(
                    "长按1号球", snapshot,
                    lambda current: (
                        current.abyss_phase is _Signal.YES
                        and current.settlement_required is _Signal.YES
                    ) or (
                        current.settlement_required is _Signal.NO
                        and current.ultimate_ready is _Signal.YES
                    ),
                )
                if completed.settlement_required is _Signal.YES:
                    self._settlement_pending = True
                    self._enter_stationary("attack_settlement")
                    # Keep the necessary transition in this invocation: no
                    # extra capture, role scan, QTE, or main-loop delay here.
                    self._settle_stationary_attack(completed, handoff=True)
                else:
                    self._enter_stationary("prepare")
                return
            if all(getattr(snapshot, name) is _Signal.NO for name in (
                "settlement_required", "abyss_phase", "slash_phase",
                "special_activated", "ultimate_ready",
            )):
                self._rebuild_stationary_resource(snapshot)
        except Exception as exc:
            if getattr(self.combat, "fresh_entry", None) is not None:
                self.combat.fresh_entry.retire()
            failed_phase = self.phase
            self.phase = "recover"
            scope.fail(f"inverse_crown:requested={failed_phase}/{self._skill_sub or '-'}_actual_failed:{type(exc).__name__}")
            raise
    def _energy_almost_full(self) -> bool:
        return bool(self.action.check_status(_ENERGY_FULL_NODE))

    def _red_ring_ready_threshold(self) -> int:
        rank = self.inverse_crown_profile.effective_rank
        return _RED_RING_READY_SSS if rank.startswith("SSS") else _RED_RING_READY_BASE

    def _special_ball_ready(self) -> bool:
        return bool(self.action.check_status(_SPECIAL_BALL_NODE))

    def _is_cage_mode(self) -> bool:
        node = self.combat.context.get_node_data("选择角色程序")
        if node is None:
            return False
        return bool(node.get("attach", {}).get("cage", False))

    def _enter_skill(self, *, switch_after: bool, ring_ready: bool = False) -> None:
        self._skill_switch_after = switch_after
        # 循环标记按新机会更新：每个大招周期一次后置 QTE，同场后续大招不被首个周期锁死。
        self._post_ult_qte_used = False
        if self._is_cage_mode():
            self.action.logger.info("cage: True")
            self._skill_sub = "cage"
        elif ring_ready:
            # 红环已确认就绪：跳过等待失效能量节点，直接红环即点循环。
            self.action.logger.info("cage: False 红环就绪，点按施法")
            self._skill_sub = "tap_cast"
            self._skill_cast_ticks = 0
        else:
            self.action.logger.info("cage: False")
            self._skill_sub = "charge"
            self._skill_charge_deadline = time.monotonic() + _SKILL_CHARGE_TIMEOUT
            self._skill_cast_ticks = 0
        self.phase = "skill"

    def _finish_skill(self) -> None:
        if not self._post_ult_qte_used:
            self.action.auxiliary_machine()
            # ready 确认单点：避免固定队友坐标盲点触发换人（方案 3.4）。
            self.action.use_ready_qte()
            self._post_ult_qte_used = True
        self._skill_sub = ""
        self.phase = "switch" if self._skill_switch_after else "idle"

    def _phase_idle(self) -> None:
        self.action.lens_lock()
        self.action.attack()

        if self._recovery_resume:
            # 恢复当拍只做允许的回落（资源进度），不盲目再次开大（方案144行）。
            self._recovery_resume = False
        elif self._energy_almost_full():
            self.action.logger.info("大招")
            self._enter_skill(switch_after=True)
            return

        ball_count = self.action.count_signal_balls()
        if ball_count >= self._red_ring_ready_threshold():
            self.action.logger.info("红环就绪(%s/16)，进入红球全消爆发", ball_count)
            self.phase = "burst_hold"
            return

        if ball_count > _SPECIAL_BALL_MIN and self._special_ball_ready():
            self.action.logger.info("特殊球就绪")
            self.phase = "special_press"
            return

        self.action.logger.info("特殊球未就绪")
        self._farm_ticks = 0
        self.phase = "farm"

    def _phase_ball_dump(self) -> None:
        self.action.ball_elimination_target()
        self.phase = "idle"

    def _phase_special_press(self) -> None:
        self.combat.context.run_action(
            "长按1号球",
            pipeline_override={"长按1号球": {"duration": 5500}},
        )
        self.action.logger.info("特殊球按下完成")
        self.phase = "special_attack"

    def _phase_special_attack(self) -> None:
        self.action.logger.info("特殊球后长按普攻蓄力横扫")
        result = self.action.long_press_attack(_CHARGED_SLASH_MS)
        if self.combat.context.tasker.stopping:
            return
        if result is not True and getattr(result, "success", None) is not True:
            raise InverseCrownUnavailable(
                "special_attack_hold_failed", actual=self.implementation_name,
            )
        self._enter_skill(
            switch_after=True,
            ring_ready=self.action.count_signal_balls() >= self._red_ring_ready_threshold(),
        )

    def _phase_burst_hold(self) -> None:
        """沿用第1槽特殊球的已校准长按入口。"""
        self.action.logger.info("长按第1槽特殊球")
        result = self.combat.context.run_action(
            "长按1号球",
            pipeline_override={
                "长按1号球": {"action": {"param": {"duration": _BURST_HOLD_MS}}}
            },
        )
        if self.combat.context.tasker.stopping:
            return
        if result is not True and getattr(result, "success", None) is not True:
            raise InverseCrownUnavailable(
                "special_hold_failed", actual=self.implementation_name,
            )
        self.phase = "burst_slash"

    def _phase_burst_slash(self) -> None:
        """长按普攻蓄力横扫，随后按 cage/普通分派大招。"""
        self.action.logger.info("长按普攻蓄力横扫")
        self.action.long_press_attack(_CHARGED_SLASH_MS)
        self._enter_skill(switch_after=True, ring_ready=True)

    def _phase_skill(self) -> None:
        if self._skill_sub == "cage":
            self.action.long_press_skill(2000)
            self._finish_skill()
        elif self._skill_sub == "charge":
            if self._energy_almost_full():
                self._skill_sub = "cast"
                self._skill_cast_ticks = 0
            elif time.monotonic() >= self._skill_charge_deadline:
                # A charge timeout does not confirm a cast or its completion.
                self.action.logger.info(
                    "inverse_crown recovery: requested=skill/charge actual=charge_timeout"
                )
                self._recovery_resume = True
                self._skill_sub = ""
                self._skill_charge_deadline = 0.0
                self._skill_cast_ticks = 0
                self._skill_switch_after = False
                self.phase = "idle"
            else:
                self.action.attack()
        elif self._skill_sub == "cast":
            first_cast = self._skill_cast_ticks == 0
            self._skill_cast_ticks += 1
            if first_cast:
                # Match the existing cage hold as a provisional duration;
                # only subsequent energy observation can confirm consumption.
                result = self.action.long_press_skill(2000)
                if self.combat.context.tasker.stopping:
                    return
                if result is not True and getattr(result, "success", None) is not True:
                    raise InverseCrownUnavailable(
                        "skill_hold_failed", actual=self.implementation_name,
                    )
            image = self.action._get_image()
            if self.combat.context.tasker.stopping:
                return
            empty = self.action.check_status(_ENERGY_EMPTY_NODE, image=image)
            if self.combat.context.tasker.stopping:
                return
            if empty:
                self._skill_sub = "finish"
            elif self._skill_cast_ticks >= _SKILL_CAST_MAX:
                raise InverseCrownUnavailable(
                    "skill_empty_not_confirmed", actual=self.implementation_name,
                )
        elif self._skill_sub == "tap_cast":
            self._skill_cast_ticks += 1
            if self.combat.context.tasker.stopping:
                return
            self.action.tap_inverse_crown_special()
            if self.combat.context.tasker.stopping:
                return
            ball_count = self.action.count_signal_balls()
            if (
                ball_count < self._red_ring_ready_threshold()
                or self._skill_cast_ticks >= _TAP_CAST_MAX
            ):
                self._skill_sub = "finish"
        elif self._skill_sub == "finish":
            #self.action.long_press_attack(4000)
            self._finish_skill()
        else:
            self.action.logger.info(
                "inverse_crown recovery: requested=skill/%s actual=unknown_sub",
                self._skill_sub or "-",
            )
            self._recovery_resume = True
            self.phase = "idle"

    def _phase_farm(self) -> None:
        self.action.attack()
        if self._special_ball_ready():
            self.action.logger.info("特殊球就绪")
            self.phase = "idle"
            return
        self._farm_ticks += 1
        if self._farm_ticks >= _FARM_MAX_TICKS:
            self.phase = "idle"
