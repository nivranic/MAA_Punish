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

"""逆冕战斗程序"""

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
_SPECIAL_ATK_TIMEOUT = 5.0
_SKILL_CHARGE_TIMEOUT = 10.0
_SKILL_CAST_MAX = 20
_FARM_MAX_TICKS = 20
_FULL_BALL_THRESHOLD = 16
_SPECIAL_BALL_MIN = 7

# Provisional local safety budgets, not measured skill durations. Live gates
# remain closed until the real detectors and device transitions are accepted.
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
    if np.any((blue >= 240) & (green >= 240) & (red >= 240)):
        return "unknown"
    if np.all(warm(pixels)):
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
    """逆冕：特殊球 → 大招 → 切人。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._special_atk_deadline = 0.0
        self._skill_charge_deadline = 0.0
        self._skill_cast_ticks = 0
        self._skill_sub = ""
        self._skill_switch_after = False
        self._farm_ticks = 0
        self._post_ult_qte_used = False
        self._stationary_deadline = None
        self._stationary_last_sequence = 0
        self._settlement_pending = False

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
        super().reset_state()
        self._special_atk_deadline = 0.0
        self._skill_charge_deadline = 0.0
        self._skill_cast_ticks = 0
        self._skill_sub = ""
        self._skill_switch_after = False
        self._farm_ticks = 0
        self._post_ult_qte_used = False
        self._stationary_deadline = None

    def do_perform(self) -> None:
        if self.inverse_crown_profile.enabled:
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
        elif self.phase == "skill":
            self._phase_skill()
        elif self.phase == "farm":
            self._phase_farm()
        else:
            self.phase = "idle"
            self._phase_idle()

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

    def _observe_stationary(self, image) -> _StationarySignals:
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
            scope.fail(f"inverse_crown:{failed_phase}_failed:{type(exc).__name__}")
            raise
    def _energy_almost_full(self) -> bool:
        return bool(self.action.check_status(_ENERGY_FULL_NODE))

    def _special_ball_ready(self) -> bool:
        return bool(self.action.check_status(_SPECIAL_BALL_NODE))

    def _is_cage_mode(self) -> bool:
        node = self.combat.context.get_node_data("选择角色程序")
        if node is None:
            return False
        return bool(node.get("attach", {}).get("cage", False))

    def _enter_skill(self, *, switch_after: bool) -> None:
        self._skill_switch_after = switch_after
        if self._is_cage_mode():
            self.action.logger.info("cage: True")
            self._skill_sub = "cage"
        else:
            self.action.logger.info("cage: False")
            self._skill_sub = "charge"
            self._skill_charge_deadline = time.monotonic() + _SKILL_CHARGE_TIMEOUT
            self._skill_cast_ticks = 0
        self.phase = "skill"

    def _finish_skill(self) -> None:
        if not self._post_ult_qte_used:
            self.action.auxiliary_machine()
            self.action.use_qte()
            self._post_ult_qte_used = True
        self._skill_sub = ""
        self.phase = "switch" if self._skill_switch_after else "idle"

    def _phase_idle(self) -> None:
        self.action.lens_lock()
        self.action.attack()

        if self._energy_almost_full():
            self.action.logger.info("大招")
            self._enter_skill(switch_after=True)
            return

        ball_count = self.action.count_signal_balls()
        if ball_count >= _FULL_BALL_THRESHOLD:
            self.action.logger.info("球数满足条件，进行球消")
            self.phase = "ball_dump"
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
        self._special_atk_deadline = time.monotonic() + _SPECIAL_ATK_TIMEOUT
        self.phase = "special_attack"

    def _phase_special_attack(self) -> None:
        if self._energy_almost_full() or time.monotonic() >= self._special_atk_deadline:
            self._enter_skill(switch_after=True)
            return
        self.action.attack()

    def _phase_skill(self) -> None:
        if self._skill_sub == "cage":
            self.action.long_press_skill(2000)
            self._finish_skill()
        elif self._skill_sub == "charge":
            if (
                self._energy_almost_full()
                or time.monotonic() >= self._skill_charge_deadline
            ):
                self._skill_sub = "cast"
                self._skill_cast_ticks = 0
            else:
                self.action.attack()
        elif self._skill_sub == "cast":
            self.action.use_skill()
            self._skill_cast_ticks += 1
            if (
                self.action.check_status(_ENERGY_EMPTY_NODE)
                or self._skill_cast_ticks >= _SKILL_CAST_MAX
            ):
                self._skill_sub = "finish"
        elif self._skill_sub == "finish":
            #self.action.long_press_attack(4000)
            self._finish_skill()
        else:
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
