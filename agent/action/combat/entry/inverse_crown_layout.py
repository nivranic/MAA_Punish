"""Opt-in layout preflight using the existing per-controller calibration.

This checks geometry in one Context, never a fresh game frame or input success.
It neither captures the layout editor nor changes the global cache option.
"""

from __future__ import annotations

import json

from action.basics.InterfaceZone.LoadRoiZone import LoadRoiZone
from action.basics.InterfaceZone import roi_zone_controller as zones
from action.basics.InterfaceZone.hud_roi_calibration import hud_overrides_from_cache
from action.combat.actions.signal_ball_layout import signal_ball_slot_centers
from action.combat.config.inverse_crown_profile import InverseCrownUnavailable
from action.combat.input_control import primary_input_position


_loader: LoadRoiZone | None = None


def _apply_offsets(context, controller: str, screen_size: tuple[int, int]) -> None:
    # Like CheckROIZoneCache, retain the stateless loader for the Agent lifetime.
    # A temporary instance would close the shared logger's handlers on deletion.
    # Only the instance is reused: every call reads this controller's file and
    # applies it to the caller's Context; no calibration/result is retained.
    global _loader
    if _loader is None:
        _loader = LoadRoiZone()
    _loader.apply_offsets(context, controller, hud_screen_size=screen_size)


class LayoutCalibrationRequired(InverseCrownUnavailable):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__("layout_calibration_required")

    def __str__(self) -> str:
        return f"{super().__str__()}; layout={self.detail}"


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise LayoutCalibrationRequired(detail)


def _rect(value, detail: str) -> list[int]:
    _require(
        isinstance(value, list) and len(value) == 4
        and all(type(item) is int for item in value), detail,
    )
    return value


def _inside(rect: list[int], bounds: list[int]) -> bool:
    x, y, width, height = rect
    bx, by, bw, bh = bounds
    return width > 0 and height > 0 and bx <= x and by <= y and x + width <= bx + bw and y + height <= by + bh


def _controller(context) -> str:
    resolved = []
    for name, section, custom_key, expected in (
        ("覆盖布局菜单", "recognition", "custom_recognition", "CheckROIZoneCache"),
        ("识别区域", "action", "custom_action", "LoadROIZone"),
    ):
        node = context.get_node_data(name)
        part = node.get(section, {}) if isinstance(node, dict) else {}
        _require(isinstance(part, dict) and part.get("type") == "Custom", f"controller_node_invalid:{name}")
        param = part.get("param", {})
        _require(isinstance(param, dict) and param.get(custom_key) == expected, f"controller_node_invalid:{name}")
        custom = zones.parse_param(param.get(f"{custom_key}_param"))
        controller = custom.get("controller")
        _require(isinstance(controller, str) and controller in zones.ZONES_BY_CONTROLLER, f"controller_unknown:{name}")
        resolved.append(controller)
    _require(resolved[0] == resolved[1], "controller_nodes_disagree")
    # The specialized primary-input scope currently supports touch only.
    # Never reinterpret Win32 key actions or an unknown platform as ADB taps.
    _require(resolved[0] == "adb", f"primary_controller_unsupported:{resolved[0]}")
    return resolved[0]


def _recognition_rect(context, name, expected_offset, screen):
    node = context.get_node_data(name)
    _require(isinstance(node, dict), f"recognition_node_missing:{name}")
    param = node.get("recognition", {}).get("param", {})
    offset = _rect(param.get("roi_offset"), f"recognition_offset_invalid:{name}")
    _require(offset == expected_offset, f"recognition_offset_mismatch:{name}")
    roi = _rect(param.get("roi"), f"recognition_roi_invalid:{name}")
    effective = [value + shift for value, shift in zip(roi, offset)]
    _require(_inside(effective, screen), f"recognition_out_of_bounds:{name}")
    return effective


def require_inverse_crown_layout(context) -> None:
    """Apply and read back the existing calibration before any combat input.

    cached_image supplies only scaled screen bounds. A future strategy must
    bind these bounds to its first successful new frame before issuing input.
    This gate deliberately does not remove ``strategy_not_ready``.
    """
    try:
        _require(not context.tasker.stopping, "task_stopping")
        controller = _controller(context)
        _require(zones.cache_is_ready(controller), "cache_missing_or_incomplete")
        path = zones.offset_path(controller)
        snapshot = path.read_bytes()
        data = json.loads(snapshot)
        image = context.tasker.controller.cached_image
        shape = getattr(image, "shape", ())
        _require(len(shape) == 3 and shape[2] == 3 and shape[0] > 0 and shape[1] > 0, "screen_geometry_unavailable")
        screen = [0, 0, int(shape[1]), int(shape[0])]
        for key, _, default in zones.zones_for(controller):
            zone = data[key]
            _require(zone.get("hit") is True, f"cache_zone_not_confirmed:{key}")
            offset = _rect(zone.get("offset"), f"cache_offset_invalid:{key}")
            recognized = _rect(zone.get("recognized"), f"cache_recognition_invalid:{key}")
            _require(zone.get("default") == default, f"cache_reference_mismatch:{key}")
            _require(recognized == [value + shift for value, shift in zip(default, offset)], f"cache_geometry_mismatch:{key}")
            _require(_inside(recognized, screen), f"cache_zone_out_of_bounds:{key}")
        for key in ("atk_zone", "skill_zone", "signal_zone"):
            _require(data[key]["offset"][2:] == [0, 0], f"scaled_inputs_unverified:{key}")

        screen_size = (screen[2], screen[3])
        try:
            hud_overrides = hud_overrides_from_cache(data, controller, screen_size)
        except ValueError as exc:
            raise LayoutCalibrationRequired(f"hud_calibration_invalid:{exc}") from exc

        # Existing loader applies absolute offsets to this Context only. Its
        # callback can report success without loading, so validate the result.
        _apply_offsets(context, controller, screen_size)
        _require(path.read_bytes() == snapshot, "cache_changed_during_load")
        for name, recognition_type in (("检查血量", "OCR"), ("检查逆冕能量空", "ColorMatch")):
            node = context.get_node_data(name)
            _require(isinstance(node, dict), f"hud_node_missing:{name}")
            _require(node.get("recognition", {}).get("type") == recognition_type, f"hud_type_changed:{name}")
            actual = _recognition_rect(context, name, [0, 0, 0, 0], screen)
            expected = hud_overrides[name]["recognition"]["param"]["roi"]
            _require(actual == expected, f"hud_roi_mismatch:{name}")
        attack_roi = _recognition_rect(context, "检查角色", data["atk_zone"]["offset"], screen)
        signal_roi = _recognition_rect(context, "识别信号球", data["signal_zone"]["offset"], screen)
        special_roi = _recognition_rect(context, "检查逆冕特殊球", data["signal_zone"]["offset"], screen)
        centers = signal_ball_slot_centers(context)
        _require(centers is not None, "signal_centers_unavailable")
        for name, key in (
            ("攻击", "atk_zone"), ("按下攻击", "atk_zone"),
            ("技能", "skill_zone"), ("长按1号球", "signal_zone"),
            *((f"消球{slot}", "signal_zone") for slot in range(1, 9)),
        ):
            node = context.get_node_data(name)
            _require(isinstance(node, dict), f"input_node_missing:{name}")
            expected = data[key]["offset"][:2] + [0, 0]
            _require(node.get("action", {}).get("param", {}).get("target_offset") == expected, f"input_offset_mismatch:{name}")
            point = primary_input_position(context, name)
            target = [*point, 1, 1]
            _require(_inside(target, screen), f"input_out_of_bounds:{name}")
            _require(_inside(target, data[key]["recognized"]), f"input_outside_calibrated_zone:{name}")
            if name == "技能":
                # Preserve validation of the existing resolved input above;
                # centering must not conceal a malformed/offscreen source.
                # Only this opt-in Context is corrected, never the resource
                # defaults or user's persisted rectangle/translation.
                x, y, width, height = data[key]["default"]
                context.override_pipeline({"技能": {"action": {"param": {
                    "target": [x + width // 2, y + height // 2],
                }}}})
                x, y, width, height = data[key]["recognized"]
                _require(primary_input_position(context, name) == (x + width // 2, y + height // 2),
                         "skill_center_readback_mismatch")
            if key == "atk_zone":
                _require(_inside(target, attack_roi), f"attack_recognition_mismatch:{name}")
            elif key == "signal_zone":
                slot = int(name[2:]) - 1 if name.startswith("消球") else 0
                _require(point == centers[slot] and _inside(target, signal_roi), f"signal_recognition_mismatch:{name}")
                if slot == 0:
                    _require(_inside(target, special_roi), f"special_recognition_mismatch:{name}")
        _require(path.read_bytes() == snapshot, "cache_changed_during_readback")
        _require(not context.tasker.stopping, "task_stopping")
    except LayoutCalibrationRequired:
        raise
    except Exception as exc:
        raise LayoutCalibrationRequired(f"layout_read_failed:{type(exc).__name__}:{exc}") from exc
