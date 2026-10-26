"""Incomplete layout scans must not replace or activate a combat calibration."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from maa.define import Rect

from action.basics.InterfaceZone import LoadRoiZone as load_module
from action.basics.InterfaceZone import ReadRoiZone as capture_module
from action.basics.InterfaceZone import roi_zone_controller as policy
from action.basics.InterfaceZone.hud_roi_calibration import (
    HUD_CALIBRATION_KEY,
    HudRoiCalibrationError,
    make_hud_calibration,
)
from test_support.fakes import FakeContext, FakeJob


@pytest.fixture(params=["adb", "win32"])
def calibration(request, tmp_path, monkeypatch):
    controller = request.param
    path = tmp_path / f"{controller}_zone_offset.json"
    monkeypatch.setitem(policy.OFFSET_FILES, controller, path)
    monkeypatch.setattr(capture_module, "_send_msg", Mock())
    data = {
        key: {"hit": True, "default": list(rect), "offset": [0, 0, 0, 0], "recognized": list(rect)}
        for key, _, rect in policy.zones_for(controller)
    }
    context = FakeContext(recognition_map={
        entry: SimpleNamespace(hit=True, box=rect)
        for _, entry, rect in policy.zones_for(controller)
    })
    context.tasker.controller.image = np.zeros((720, 1280, 3), dtype=np.uint8)
    context.tasker.resource = SimpleNamespace(node_list=list(load_module._get_node_zone_map()))
    return controller, path, data, context


@pytest.mark.parametrize("missing", ["skill_zone", "atk_zone", "switch_zone"])
def test_partial_cache_requires_recapture(calibration, missing):
    controller, path, data, _ = calibration
    data[missing] = {"hit": False, "offset": None}
    path.write_text(json.dumps(data), encoding="utf-8")

    assert not policy.cache_is_ready(controller)


@pytest.mark.parametrize("bad_offset", [None, [], [1, 2, 3], ["1", 0, 0, 0], [float("nan"), 0, 0, 0]])
def test_invalid_offset_requires_recapture(calibration, bad_offset):
    controller, path, data, _ = calibration
    data["skill_zone"]["offset"] = bad_offset
    path.write_text(json.dumps(data), encoding="utf-8")

    assert not policy.cache_is_ready(controller)


def test_complete_cache_is_ready(calibration):
    controller, path, data, _ = calibration
    path.write_text(json.dumps(data), encoding="utf-8")

    assert policy.cache_is_ready(controller)


@pytest.mark.parametrize("previous", [False, True])
def test_failed_capture_preserves_previous_cache(calibration, previous):
    controller, path, data, context = calibration
    if previous:
        path.write_text(json.dumps(data), encoding="utf-8")
    original = path.read_bytes() if previous else None
    context.recognition_map["识别大招区"] = SimpleNamespace(hit=False, box=None)

    assert not capture_module.capture_roi_zones(context, controller)
    assert (path.read_bytes() if path.exists() else None) == original


def test_successful_capture_saves_complete_cache(calibration):
    controller, path, _, context = calibration

    assert capture_module.capture_roi_zones(context, controller)
    assert policy.cache_is_ready(controller)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        key for key, _, _ in policy.zones_for(controller)
    }


@pytest.mark.parametrize("box_type", [list, tuple, Rect], ids=["list", "tuple", "sdk-rect"])
@pytest.mark.parametrize("with_hud", [False, True])
def test_supported_recognition_boxes_save_normalized_geometry(calibration, box_type, with_hud):
    controller, path, _, context = calibration
    expected = {}
    for key, entry, default in policy.zones_for(controller):
        recognized = [default[0] + 1, default[1] + 2, default[2], default[3]]
        box = Rect(*recognized) if box_type is Rect else box_type(recognized)
        context.recognition_map[entry] = SimpleNamespace(hit=True, box=box)
        expected[key] = {"hit": True, "default": list(default),
                         "recognized": recognized, "offset": [1, 2, 0, 0]}

    options = {"hud_calibration": _hud_payload()} if with_hud else {}
    assert capture_module.capture_roi_zones(context, controller, **options)
    if with_hud:
        expected = _cache_with_hud(controller, expected)
    assert json.loads(path.read_text(encoding="utf-8")) == expected


class UnsupportedIterableBox:
    def __iter__(self):
        raise AssertionError("unsupported recognition boxes must not be iterated")


@pytest.mark.parametrize("box", [
    pytest.param(Rect(True, 0, 1, 1), id="sdk-bool"),
    pytest.param(Rect(0, 0, 1.0, 1), id="sdk-float"),
    pytest.param(Rect(np.int64(0), 0, 1, 1), id="sdk-numpy-int"),
    pytest.param(Rect(-1, 0, 1, 1), id="sdk-negative-x"),
    pytest.param(Rect(0, -1, 1, 1), id="sdk-negative-y"),
    pytest.param(Rect(0, 0, 0, 1), id="sdk-zero-width"),
    pytest.param(Rect(0, 0, 1, 0), id="sdk-zero-height"),
    pytest.param(Rect(0, 0, -1, 1), id="sdk-negative-width"),
    pytest.param(Rect(1279, 0, 2, 1), id="sdk-right-overflow"),
    pytest.param(Rect(0, 719, 1, 2), id="sdk-bottom-overflow"),
    pytest.param([0, 0, 1], id="short-list"),
    pytest.param([0, 0, 1, 1, 1], id="long-list"),
    pytest.param([False, 0, 1, 1], id="list-bool"),
    pytest.param((0, 0, 1.0, 1), id="tuple-float"),
    pytest.param(np.array([0, 0, 1, 1]), id="numpy-array"),
    pytest.param(range(4), id="range"),
    pytest.param(iter([0, 0, 1, 1]), id="iterator"),
    pytest.param(UnsupportedIterableBox(), id="unsupported-iterable"),
])
def test_invalid_recognition_boxes_preserve_previous_cache(calibration, box):
    controller, path, data, context = calibration
    path.write_text(json.dumps(_cache_with_hud(controller, data)), encoding="utf-8")
    original = path.read_bytes()
    entry = policy.zones_for(controller)[0][1]
    context.recognition_map[entry] = SimpleNamespace(hit=True, box=box)

    assert not capture_module.capture_roi_zones(context, controller,
                                                hud_calibration=_hud_payload())
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_stopped_capture_does_not_write(calibration):
    controller, path, _, context = calibration
    context.tasker.stopping = True

    assert not capture_module.capture_roi_zones(context, controller)
    assert not path.exists()


@pytest.mark.parametrize("stop_entry", ["识别攻击区", "识别换人区"])
def test_stop_during_capture_preserves_previous_cache(calibration, monkeypatch, stop_entry):
    controller, path, data, context = calibration
    path.write_text(json.dumps(data), encoding="utf-8")
    original = path.read_bytes()
    recognize = context.run_recognition

    def stop_during_recognition(entry, image):
        if entry == stop_entry:
            context.tasker.stopping = True
        return recognize(entry, image)

    monkeypatch.setattr(context, "run_recognition", stop_during_recognition)

    assert not capture_module.capture_roi_zones(context, controller)
    assert path.read_bytes() == original


def test_failed_scan_is_reported_as_action_failure(calibration, monkeypatch):
    controller, _, _, context = calibration
    monkeypatch.setattr(load_module, "capture_roi_zones", Mock(return_value=False))
    loader = load_module.LoadRoiZone()
    monkeypatch.setattr(loader, "apply_offsets", Mock())
    argv = SimpleNamespace(custom_action_param={"controller": controller, "from_screen": True})

    assert not loader.run(context, argv).success
    loader.apply_offsets.assert_not_called()


def test_incomplete_cache_is_not_applied(calibration):
    controller, path, data, context = calibration
    del data["skill_zone"]
    path.write_text(json.dumps(data), encoding="utf-8")

    load_module.LoadRoiZone().apply_offsets(context, controller)

    assert context.pipeline_overrides == []


@pytest.mark.parametrize("action_type", ["Click", "LongPress", "TouchDown", "TouchMove"])
@pytest.mark.parametrize("target", [[1200, 620], [1200, 620, 1, 1]])
def test_fixed_combat_input_follows_calibrated_button(calibration, monkeypatch, action_type, target):
    controller, path, data, context = calibration
    data["atk_zone"]["offset"] = [-95, -120, 8, 6]
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(load_module, "_get_node_zone_map", lambda: {"攻击": "atk_zone"})
    context.node_data["攻击"] = {"action": {"type": action_type, "param": {
        "target": target, "contact": 2, "duration": 1000,
        "target_offset": [-95, -120, 0, 0],
    }}}

    load_module.LoadRoiZone().apply_offsets(context, controller)

    patch = context.pipeline_overrides[-1]["攻击"]
    assert patch["recognition"]["param"]["roi_offset"] == [-95, -120, 8, 6]
    assert patch["action"] == {"param": {"target_offset": [-95, -120, 0, 0]}}
    # 真机攻击区为 [1037,452,124,123]，旧点击点在区外，校准后在区内。
    x, y = [target[i] + patch["action"]["param"]["target_offset"][i] for i in range(2)]
    assert 1037 <= x < 1161 and 452 <= y < 575


@pytest.mark.parametrize("end", [[1200, 620], [[1200, 620], True, "识别目标"]])
def test_long_attack_moves_both_swipe_endpoints(calibration, monkeypatch, end):
    controller, path, data, context = calibration
    data["atk_zone"]["offset"] = [-95, -120, 0, 0]
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(load_module, "_get_node_zone_map", lambda: {"长按攻击": "atk_zone"})
    context.node_data["长按攻击"] = {"action": {"type": "Swipe", "param": {
        "begin": [1200, 620], "end": end, "duration": 4000,
    }}}

    load_module.LoadRoiZone().apply_offsets(context, controller)

    param = context.pipeline_overrides[-1]["长按攻击"]["action"]["param"]
    assert param["begin_offset"] == [-95, -120, 0, 0]
    assert param["end_offset"] == (
        [-95, -120, 0, 0] if end == [1200, 620]
        else [[-95, -120, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    )


@pytest.mark.parametrize("action", [
    {"type": "Click", "param": {"target": True}},
    {"type": "Click", "param": {"target": "识别目标"}},
    {"type": "ClickKey", "param": {"key": 74}},
    {"type": "KeyDown", "param": {"key": 74}},
    {"type": "TouchUp", "param": {"contact": 0}},
])
def test_recognized_targets_and_keyboard_inputs_are_not_moved(calibration, monkeypatch, action):
    controller, path, data, context = calibration
    data["atk_zone"]["offset"] = [-95, -120, 0, 0]
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(load_module, "_get_node_zone_map", lambda: {"攻击": "atk_zone"})
    context.node_data["攻击"] = {"action": action}

    load_module.LoadRoiZone().apply_offsets(context, controller)

    assert "action" not in context.pipeline_overrides[-1]["攻击"]


def test_loading_action_calibration_twice_does_not_accumulate(calibration, monkeypatch):
    controller, path, data, context = calibration
    data["atk_zone"]["offset"] = [-95, -120, 0, 0]
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(load_module, "_get_node_zone_map", lambda: {
        "攻击": "atk_zone", "战斗中": "atk_zone",
    })
    # 模拟已加载一次的运行节点：原始 target 保留，校准保存在 offset 字段。
    monkeypatch.setattr(context, "get_node_data", Mock(return_value={
        "action": {"type": "Click", "param": {
            "target": [1200, 620, 1, 1], "target_offset": [-95, -120, 0, 0],
        }},
    }))
    loader = load_module.LoadRoiZone()

    loader.apply_offsets(context, controller)
    loader.apply_offsets(context, controller)

    assert context.pipeline_overrides[0] == context.pipeline_overrides[1]
    assert "战斗中" not in context.pipeline_overrides[-1]
    assert context.pipeline_overrides[-1]["攻击"]["action"]["param"] == {
        "target_offset": [-95, -120, 0, 0],
    }


def _hud_payload():
    return {"schema": 1, "screen_size": [1280, 720],
            "source_frame_sha256": "a" * 64,
            "nodes": {"检查血量": [70, 15, 140, 29],
                      "检查逆冕能量空": [75, 62, 215, 3]}}


def _cache_with_hud(controller, data):
    result = dict(data)
    result[HUD_CALIBRATION_KEY] = make_hud_calibration(
        _hud_payload(), controller, (1280, 720), data,
    )
    return result


def test_explicit_hud_is_saved_and_only_requested_loading_applies_it(calibration):
    controller, path, _, context = calibration
    loader = load_module.LoadRoiZone()
    argv = SimpleNamespace(custom_action_param={
        "controller": controller, "from_screen": True,
        HUD_CALIBRATION_KEY: _hud_payload(),
    })
    assert loader.run(context, argv).success
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[HUD_CALIBRATION_KEY]["controller"] == controller
    assert "检查血量" not in context.pipeline_overrides[-1]
    loader.apply_offsets(context, controller, hud_screen_size=(1280, 720))
    assert context.pipeline_overrides[-1]["检查血量"] == {
        "recognition": {"param": {"roi": [70, 15, 140, 29],
                                   "roi_offset": [0, 0, 0, 0]}}
    }


def test_recapture_preserves_hud_only_for_unchanged_layout_and_screen(calibration):
    controller, path, data, context = calibration
    original = _cache_with_hud(controller, data)
    path.write_text(json.dumps(original), encoding="utf-8")
    assert capture_module.capture_roi_zones(context, controller)
    assert json.loads(path.read_text(encoding="utf-8")) == original


@pytest.mark.parametrize("change", ["layout", "screen", "invalid_block", "controller"])
def test_recapture_discards_stale_hud_but_keeps_successful_eight_zones(calibration, change):
    controller, path, data, context = calibration
    original = _cache_with_hud(controller, data)
    if change == "layout":
        entry = next(entry for name, entry, _ in policy.zones_for(controller) if name == "skill_zone")
        rect = list(data["skill_zone"]["recognized"])
        rect[0] += 1
        context.recognition_map[entry] = SimpleNamespace(hit=True, box=rect)
    elif change == "screen":
        context.tasker.controller.image = np.zeros((720, 1281, 3), dtype=np.uint8)
    elif change == "controller":
        original[HUD_CALIBRATION_KEY]["controller"] = "win32" if controller == "adb" else "adb"
    else:
        original[HUD_CALIBRATION_KEY]["unused"] = True
    path.write_text(json.dumps(original), encoding="utf-8")
    assert capture_module.capture_roi_zones(context, controller)
    current = json.loads(path.read_text(encoding="utf-8"))
    assert HUD_CALIBRATION_KEY not in current
    assert policy.cache_is_ready(controller)


def test_invalid_explicit_hud_preserves_old_cache_bytes(calibration):
    controller, path, data, context = calibration
    path.write_text(json.dumps(_cache_with_hud(controller, data)), encoding="utf-8")
    before = path.read_bytes()
    bad = _hud_payload()
    bad["nodes"]["检查血量"] = [1279, 0, 2, 1]
    assert not capture_module.capture_roi_zones(context, controller, hud_calibration=bad)
    assert path.read_bytes() == before


def test_failed_screenshot_with_stale_image_cannot_update_cache(calibration, monkeypatch):
    controller, path, data, context = calibration
    path.write_text(json.dumps(_cache_with_hud(controller, data)), encoding="utf-8")
    before = path.read_bytes()
    job = FakeJob(image=np.zeros((720, 1280, 3), dtype=np.uint8), succeeded=False)
    job.get = Mock(wraps=job.get)
    monkeypatch.setattr(context.tasker.controller, "post_screencap", Mock(return_value=job))
    monkeypatch.setattr(context, "run_recognition", Mock())
    assert not capture_module.capture_roi_zones(context, controller, hud_calibration=_hud_payload())
    job.get.assert_not_called()
    context.run_recognition.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.parametrize("image", [
    None, np.zeros((720, 1280, 3), dtype=np.float32),
    np.zeros((720, 1280), dtype=np.uint8), np.zeros((0, 1280, 3), dtype=np.uint8),
])
def test_successful_job_with_invalid_image_preserves_old_cache(calibration, monkeypatch, image):
    controller, path, data, context = calibration
    path.write_text(json.dumps(_cache_with_hud(controller, data)), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(context.tasker.controller, "post_screencap",
                        Mock(return_value=FakeJob(image=image, succeeded=True)))
    monkeypatch.setattr(context, "run_recognition", Mock())
    assert not capture_module.capture_roi_zones(context, controller, hud_calibration=_hud_payload())
    context.run_recognition.assert_not_called()
    assert path.read_bytes() == before


def test_layout_capture_uses_one_controller_handle(calibration):
    controller, _, _, context = calibration
    original_tasker = context.tasker
    native = original_tasker.controller

    class CountingTasker:
        reads = 0

        def __getattr__(self, name):
            return getattr(original_tasker, name)

        @property
        def controller(self):
            self.reads += 1
            return native

    context.tasker = CountingTasker()
    assert capture_module.capture_roi_zones(context, controller, hud_calibration=_hud_payload())
    assert context.tasker.reads == 1


def test_atomic_replace_failure_preserves_cache_and_removes_own_temporary_file(calibration, monkeypatch):
    controller, path, data, context = calibration
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(capture_module.os, "replace", Mock(side_effect=OSError("replace failed")))
    assert not capture_module.capture_roi_zones(context, controller, hud_calibration=_hud_payload())
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_stop_before_atomic_replace_preserves_cache(calibration, monkeypatch):
    controller, path, data, context = calibration
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(capture_module.os, "fsync", lambda _: setattr(context.tasker, "stopping", True))
    assert not capture_module.capture_roi_zones(context, controller)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("mode", [None, False, 1, "true"])
def test_hud_save_requires_explicit_from_screen_true(calibration, monkeypatch, mode):
    controller, path, _, context = calibration
    capture = Mock()
    monkeypatch.setattr(load_module, "capture_roi_zones", capture)
    loader = load_module.LoadRoiZone()
    argv = SimpleNamespace(custom_action_param={
        "controller": controller, "from_screen": mode, HUD_CALIBRATION_KEY: _hud_payload(),
    })
    assert not loader.run(context, argv).success
    capture.assert_not_called()
    assert not path.exists()
    assert context.pipeline_overrides == []


@pytest.mark.parametrize("controller_value", [None, "unknown", True])
def test_hud_save_cannot_silently_fallback_to_adb(calibration, monkeypatch, controller_value):
    _, path, _, context = calibration
    capture = Mock()
    monkeypatch.setattr(load_module, "capture_roi_zones", capture)
    argv = SimpleNamespace(custom_action_param={
        "controller": controller_value, "from_screen": True,
        HUD_CALIBRATION_KEY: _hud_payload(),
    })
    assert not load_module.LoadRoiZone().run(context, argv).success
    capture.assert_not_called()
    assert not path.exists()


@pytest.mark.parametrize("block", [None, False, [], "{}"])
def test_hud_save_rejects_non_object_before_capture(calibration, monkeypatch, block):
    controller, path, _, context = calibration
    capture = Mock()
    monkeypatch.setattr(load_module, "capture_roi_zones", capture)
    argv = SimpleNamespace(custom_action_param={
        "controller": controller, "from_screen": True, HUD_CALIBRATION_KEY: block,
    })
    assert not load_module.LoadRoiZone().run(context, argv).success
    capture.assert_not_called()
    assert not path.exists()


@pytest.mark.parametrize("bad_hud", [False, True])
def test_explicit_hud_load_refuses_before_any_context_override(calibration, bad_hud):
    controller, path, data, context = calibration
    current = _cache_with_hud(controller, data) if bad_hud else data
    if bad_hud:
        current[HUD_CALIBRATION_KEY]["layout_fingerprint"] = "b" * 64
    path.write_text(json.dumps(current), encoding="utf-8")
    with pytest.raises(HudRoiCalibrationError):
        load_module.LoadRoiZone().apply_offsets(context, controller, hud_screen_size=(1280, 720))
    assert context.pipeline_overrides == []


def test_normal_loading_ignores_optional_bad_hud_block(calibration):
    controller, path, data, context = calibration
    data[HUD_CALIBRATION_KEY] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    load_module.LoadRoiZone().apply_offsets(context, controller)
    assert len(context.pipeline_overrides) == 1
    assert "检查血量" not in context.pipeline_overrides[-1]


def test_explicit_hud_load_does_not_read_controller_getter(calibration):
    controller, path, data, context = calibration
    path.write_text(json.dumps(_cache_with_hud(controller, data)), encoding="utf-8")
    resource = context.tasker.resource

    class TaskerWithoutController:
        @property
        def controller(self):
            raise AssertionError("HUD loader must use caller-provided geometry")

    context.tasker = TaskerWithoutController()
    context.tasker.resource = resource
    load_module.LoadRoiZone().apply_offsets(context, controller, hud_screen_size=(1280, 720))
    assert len(context.pipeline_overrides) == 1


@pytest.mark.parametrize("controller_value", [None, True, "unknown", "ADB", []])
def test_explicit_hud_load_rejects_unsupported_controller_before_cache_lookup(monkeypatch, controller_value):
    lookup = Mock(side_effect=AssertionError("unsupported controller must not resolve a cache"))
    monkeypatch.setattr(load_module, "offset_path", lookup)
    context = FakeContext()
    with pytest.raises(HudRoiCalibrationError, match="controller"):
        load_module.LoadRoiZone().apply_offsets(context, controller_value, hud_screen_size=(1280, 720))
    lookup.assert_not_called()
    assert context.pipeline_overrides == []
