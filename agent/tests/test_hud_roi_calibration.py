"""Strict, layout-bound HUD rectangles; this does not label game signals."""

from copy import deepcopy

import pytest

from action.basics.InterfaceZone import roi_zone_controller as zones
from action.basics.InterfaceZone.hud_roi_calibration import (
    HUD_CALIBRATION_KEY,
    HudRoiCalibrationError,
    hud_overrides_from_cache,
    make_hud_calibration,
)


def layout(controller="adb"):
    return {
        key: dict(default=list(rect), recognized=list(rect),
                  offset=[0, 0, 0, 0], hit=True)
        for key, _, rect in zones.zones_for(controller)
    }


def payload():
    return dict(schema=1, screen_size=[1280, 720],
                source_frame_sha256="a" * 64,
                nodes={"检查血量": [70, 15, 140, 29],
                       "检查逆冕能量空": [75, 62, 215, 3]})


def cache(controller="adb"):
    result = layout(controller)
    result[HUD_CALIBRATION_KEY] = make_hud_calibration(
        payload(), controller, (1280, 720), result,
    )
    return result


@pytest.mark.parametrize("controller", ["adb", "win32"])
@pytest.mark.parametrize("size", [[1280, 720], (1280, 720)])
def test_explicit_hud_roundtrip_copies_rectangles_without_offset_accumulation(controller, size):
    data = cache(controller)
    before = deepcopy(data)
    expected = {
        name: {"recognition": {"param": {"roi": rect, "roi_offset": [0, 0, 0, 0]}}}
        for name, rect in payload()["nodes"].items()
    }
    first = hud_overrides_from_cache(data, controller, size)
    assert first == expected
    first["检查血量"]["recognition"]["param"]["roi"][0] = 1
    assert data == before
    assert hud_overrides_from_cache(data, controller, size) == expected


def test_layout_fingerprint_is_canonical_and_ignores_source_payload_aliasing():
    data, raw = layout(), payload()
    block = make_hud_calibration(raw, "adb", (1280, 720), data)
    reversed_data = dict(reversed(list(data.items())))
    assert make_hud_calibration(raw, "adb", (1280, 720), reversed_data) == block
    raw["screen_size"][0] = 1
    raw["nodes"]["检查血量"][0] = 1
    data["skill_zone"]["recognized"][0] = 1
    assert block["screen_size"] == [1280, 720]
    assert block["nodes"]["检查血量"] == [70, 15, 140, 29]


@pytest.mark.parametrize("field,value", [
    ("schema", True), ("schema", 1.0), ("schema", 2),
    ("screen_size", [True, 720]), ("screen_size", [1280.0, 720]),
    ("screen_size", (1280, 720)), ("screen_size", [1280, 0]),
    ("screen_size", [1280, 721]), ("screen_size", [1280]),
    ("source_frame_sha256", "A" * 64), ("source_frame_sha256", "a" * 63),
    ("source_frame_sha256", "a" * 64 + "\n"),
    ("source_frame_sha256", 1), ("nodes", []),
    ("nodes", {"检查血量": [70, 15, 140, 29]}),
    ("nodes", {"检查血量": [70, 15, 140, 29],
               "检查逆冕能量空": [75, 62, 215, 3], "未知节点": [0, 0, 1, 1]}),
])
def test_malformed_payload_is_rejected(field, value):
    raw = payload()
    raw[field] = value
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(raw, "adb", (1280, 720), layout())


@pytest.mark.parametrize("rect", [
    None, [0, 0, 1], [0, 0, 1, 1, 1], [True, 0, 1, 1],
    [0.0, 0, 1, 1], ["0", 0, 1, 1], [-1, 0, 1, 1],
    [0, -1, 1, 1], [0, 0, 0, 1], [0, 0, 1, 0],
    [1280, 0, 1, 1], [0, 720, 1, 1], [1279, 719, 2, 1],
    [1279, 719, 1, 2],
])
def test_hud_rectangle_must_be_a_strict_in_bounds_integer_box(rect):
    raw = payload()
    raw["nodes"]["检查血量"] = rect
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(raw, "adb", (1280, 720), layout())


@pytest.mark.parametrize("value", [None, [], {}, [1280], [True, 720], [1280.0, 720], [1280, 0]])
def test_caller_screen_size_is_strict(value):
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(payload(), "adb", value, layout())


@pytest.mark.parametrize("controller", [None, True, "unknown", "ADB"])
def test_unknown_controller_cannot_use_adb_fallback(controller):
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(payload(), controller, (1280, 720), layout())


@pytest.mark.parametrize("field", ["schema", "screen_size", "source_frame_sha256", "nodes"])
def test_missing_or_extra_payload_fields_are_rejected(field):
    raw = payload()
    del raw[field]
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(raw, "adb", (1280, 720), layout())
    raw = payload() | {"unused": True}
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(raw, "adb", (1280, 720), layout())


@pytest.mark.parametrize("change", ["missing", "hit", "default", "recognized", "offset", "geometry"])
def test_layout_binding_requires_all_eight_confirmed_geometry_records(change):
    data = layout()
    zone = data["skill_zone"]
    if change == "missing":
        del data["skill_zone"]
    elif change == "hit":
        zone["hit"] = 1
    elif change == "default":
        zone["default"][0] += 1
    elif change == "recognized":
        zone["recognized"] = [1280, 0, 1, 1]
    elif change == "offset":
        zone["offset"] = [True, 0, 0, 0]
    else:
        zone["offset"] = [1, 0, 0, 0]
    with pytest.raises(HudRoiCalibrationError):
        make_hud_calibration(payload(), "adb", (1280, 720), data)


def test_a_valid_new_layout_cannot_reuse_an_old_hud_fingerprint():
    data = cache()
    data["skill_zone"]["offset"][0] = 1
    data["skill_zone"]["recognized"][0] += 1
    with pytest.raises(HudRoiCalibrationError, match="fingerprint"):
        hud_overrides_from_cache(data, "adb", (1280, 720))


@pytest.mark.parametrize("change", ["missing", "extra", "controller", "size", "fingerprint", "node"])
def test_saved_block_is_required_and_cannot_drift(change):
    data = cache()
    block = data[HUD_CALIBRATION_KEY]
    if change == "missing":
        del data[HUD_CALIBRATION_KEY]
    elif change == "extra":
        block["unused"] = True
    elif change == "controller":
        block["controller"] = "win32"
    elif change == "size":
        block["screen_size"] = [1280, 721]
    elif change == "fingerprint":
        block["layout_fingerprint"] = "b" * 64
    else:
        block["nodes"]["检查血量"] = [0, 0, 0, 1]
    with pytest.raises(HudRoiCalibrationError):
        hud_overrides_from_cache(data, "adb", (1280, 720))
