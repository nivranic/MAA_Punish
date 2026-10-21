"""Incomplete layout scans must not replace or activate a combat calibration."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.basics.InterfaceZone import LoadRoiZone as load_module
from action.basics.InterfaceZone import ReadRoiZone as capture_module
from action.basics.InterfaceZone import roi_zone_controller as policy
from test_support.fakes import FakeContext


@pytest.fixture(params=["adb", "win32"])
def calibration(request, tmp_path, monkeypatch):
    controller = request.param
    path = tmp_path / f"{controller}_zone_offset.json"
    monkeypatch.setitem(policy.OFFSET_FILES, controller, path)
    monkeypatch.setattr(capture_module, "_send_msg", Mock())
    data = {
        key: {"hit": True, "offset": [0, 0, 0, 0], "recognized": rect}
        for key, _, rect in policy.zones_for(controller)
    }
    context = FakeContext(recognition_map={
        entry: SimpleNamespace(hit=True, box=rect)
        for _, entry, rect in policy.zones_for(controller)
    })
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
