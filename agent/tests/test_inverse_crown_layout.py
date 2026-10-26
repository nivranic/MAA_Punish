"""Layout gate contracts; in-memory geometry is not game/device acceptance."""

from copy import deepcopy
import gc
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from action.basics.InterfaceZone import roi_zone_controller as zones
from action.basics.InterfaceZone.hud_roi_calibration import make_hud_calibration
from action.basics.InterfaceZone.roi_zone_classify import load_combat_pipeline_nodes
from action.combat.entry import inverse_crown_layout as layout
from action.combat.entry import runner
from test_support.fakes import FakeContext


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def calibrated(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "_loader", None)
    path = tmp_path / "adb_zone_offset.json"
    monkeypatch.setitem(zones.OFFSET_FILES, "adb", path)
    data = {
        key: {"hit": True, "default": rect, "recognized": rect, "offset": [0, 0, 0, 0]}
        for key, _, rect in zones.zones_for("adb")
    }
    # Synthetic translations, deliberately unrelated to the phone's cache.
    for key, offset in (("atk_zone", [-20, -30, 0, 0]), ("skill_zone", [-10, -10, 0, 0]), ("signal_zone", [-100, 0, 0, 0])):
        data[key]["offset"] = offset
        data[key]["recognized"] = [value + shift for value, shift in zip(data[key]["default"], offset)]
    data["hud_roi_calibration"] = make_hud_calibration({
        "schema": 1, "screen_size": [1280, 720], "source_frame_sha256": "0" * 64,
        "nodes": {"检查血量": [70, 15, 140, 29], "检查逆冕能量空": [75, 62, 215, 3]},
    }, "adb", (1280, 720), data)
    path.write_text(json.dumps(data), encoding="utf-8")
    resource = Resource()
    nodes = load_combat_pipeline_nodes()
    for filename in ("Read_Zone.json", "Cache_Roi_Zone.json"):
        nodes.update(json.loads((ROOT / "resource/base/pipeline/Common/Read_Interface" / filename).read_text(encoding="utf-8")))
    assert resource.override_pipeline(nodes)
    context = FakeContext()
    context.tasker.resource = resource
    context.tasker.controller.cached_image = np.zeros((720, 1280, 3), dtype=np.uint8)
    monkeypatch.setattr(context, "get_node_data", resource.get_node_data)
    monkeypatch.setattr(context, "override_pipeline", resource.override_pipeline)
    monkeypatch.setattr(context.tasker.controller, "post_screencap", lambda: pytest.fail("layout gate must not capture"))
    return context, path, data


def test_valid_cache_loads_even_when_no_without_mutating_option_or_file(calibrated):
    context, path, _ = calibrated
    before = path.read_bytes()
    option = context.get_node_data("覆盖布局菜单")
    assert option["enabled"] is False
    layout.require_inverse_crown_layout(context)
    first = context.get_node_data("攻击")
    assert first["action"]["param"]["target_offset"] == [-20, -30, 0, 0]
    assert context.get_node_data("技能")["action"]["param"]["target_offset"] == [-10, -10, 0, 0]
    layout.require_inverse_crown_layout(context)
    assert context.get_node_data("攻击") == first
    assert context.get_node_data("覆盖布局菜单") == option
    assert path.read_bytes() == before
    assert context.actions == context.tasker.controller.clicks == context.tasker.controller.touches == []


def test_opt_in_requires_separate_hud_calibration(calibrated):
    context, path, data = calibrated
    data.pop("hud_roi_calibration", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(layout.LayoutCalibrationRequired, match="hud"):
        layout.require_inverse_crown_layout(context)
    assert path.read_bytes() == before
    assert context.actions == context.tasker.controller.touches == []


def test_skill_point_is_calibrated_center_instead_of_reference_bottom(calibrated):
    context, path, data = calibrated
    before = path.read_bytes()
    layout.require_inverse_crown_layout(context)
    x, y, width, height = data["skill_zone"]["recognized"]
    assert layout.primary_input_position(context, "技能") == (x + width // 2, y + height // 2)
    assert path.read_bytes() == before
    assert context.get_node_data("技能")["action"]["param"]["target_offset"] == [-10, -10, 0, 0]


def test_hud_readback_is_exact_and_clears_previous_offsets(calibrated):
    context, path, data = calibrated
    context.override_pipeline({name: {"recognition": {"param": {"roi_offset": [12, 8, 0, 0]}}}
                               for name in ("检查血量", "检查逆冕能量空")})
    before = path.read_bytes()
    layout.require_inverse_crown_layout(context)
    for name, rect in data["hud_roi_calibration"]["nodes"].items():
        node = context.get_node_data(name)
        assert node["recognition"]["param"]["roi"] == rect
        assert node["recognition"]["param"]["roi_offset"] == [0, 0, 0, 0]
    assert context.get_node_data("检查血量")["recognition"]["type"] == "OCR"
    assert context.get_node_data("检查逆冕能量空")["recognition"]["type"] == "ColorMatch"
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["hp_roi", "bar_offset", "hp_type", "bar_type"])
def test_partial_hud_application_is_rejected(calibrated, monkeypatch, fault):
    context, _, _ = calibrated
    apply = layout.LoadRoiZone.apply_offsets

    def partial(loader, ctx, controller, *, hud_screen_size=None):
        apply(loader, ctx, controller, hud_screen_size=hud_screen_size)
        if fault == "hp_roi":
            ctx.override_pipeline({"检查血量": {"recognition": {"param": {"roi": [19, 15, 141, 29]}}}})
        elif fault == "bar_offset":
            ctx.override_pipeline({"检查逆冕能量空": {"recognition": {"param": {"roi_offset": [1, 0, 0, 0]}}}})
        else:
            get_node = ctx.get_node_data

            def wrong_type(name):
                node = deepcopy(get_node(name))
                if name == ("检查血量" if fault == "hp_type" else "检查逆冕能量空"):
                    node["recognition"]["type"] = "DirectHit"
                return node

            monkeypatch.setattr(ctx, "get_node_data", wrong_type)

    monkeypatch.setattr(layout.LoadRoiZone, "apply_offsets", partial)
    with pytest.raises(layout.LayoutCalibrationRequired, match="hud|recognition_offset"):
        layout.require_inverse_crown_layout(context)
    assert context.actions == context.tasker.controller.touches == []


@pytest.mark.parametrize("fault", ["ignored_center", "file_changed_after_load"])
def test_skill_center_override_cannot_be_ignored_or_hide_cache_change(calibrated, monkeypatch, fault):
    context, path, _ = calibrated
    override = context.override_pipeline

    def change(pipeline):
        centering = "target" in pipeline.get("技能", {}).get("action", {}).get("param", {})
        if centering and fault == "ignored_center":
            return False
        result = override(pipeline)
        if centering:
            path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(context, "override_pipeline", change)
    with pytest.raises(layout.LayoutCalibrationRequired, match="skill_center|cache_changed_during_readback"):
        layout.require_inverse_crown_layout(context)
    assert context.actions == context.tasker.controller.touches == []


def test_retained_loader_preserves_existing_same_name_logger(calibrated, capsys):
    context, _, _ = calibrated
    existing = layout.LoadRoiZone()
    existing.logger.info("layout-existing-before")
    layout.require_inverse_crown_layout(context)
    retained = layout._loader
    gc.collect()
    existing.logger.info("layout-existing-after-first")
    layout.require_inverse_crown_layout(context)
    assert layout._loader is retained
    gc.collect()
    existing.logger.info("layout-existing-after-repeat")
    output = capsys.readouterr().out
    assert all(marker in output for marker in (
        "layout-existing-before", "layout-existing-after-first", "layout-existing-after-repeat",
    ))
    assert existing.logger.handlers
    assert all(not getattr(handler, "_closed", False) for handler in existing.logger.handlers)


@pytest.mark.parametrize("fault", ["missing", "bad_json", "partial", "boolean", "fraction", "missing_recognized", "reference", "geometry", "scaled", "outside"])
def test_bad_cache_fails_before_loading(calibrated, monkeypatch, fault):
    context, path, data = calibrated
    if fault == "missing":
        path.unlink()
    elif fault == "bad_json":
        path.write_text("{", encoding="utf-8")
    else:
        zone = data["signal_zone"]
        if fault == "partial":
            zone["hit"] = False
        elif fault == "boolean":
            zone["offset"][0] = True
        elif fault == "fraction":
            zone["offset"][0] = 0.25
        elif fault == "missing_recognized":
            del zone["recognized"]
        elif fault == "reference":
            zone["default"] = [1, 2, 3, 4]
        elif fault == "geometry":
            zone["recognized"][0] += 1
        elif fault == "scaled":
            zone["offset"][2] = -20
            zone["recognized"][2] -= 20
        elif fault == "outside":
            zone["offset"][0] = -500
            zone["recognized"][0] = zone["default"][0] - 500
        path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(layout.LoadRoiZone, "apply_offsets", lambda *_: pytest.fail("invalid cache must not load"))
    with pytest.raises(layout.LayoutCalibrationRequired, match="layout_calibration_required"):
        layout.require_inverse_crown_layout(context)
    assert context.actions == []


@pytest.mark.parametrize("left,right", [(None, "adb"), ("unknown", "adb"), ("ADB", "adb"), ("adb", "win32"), ("win32", "win32")])
def test_controller_is_explicit_and_consistent_with_no_fallback(calibrated, monkeypatch, left, right):
    context, _, _ = calibrated
    context.override_pipeline({
        "覆盖布局菜单": {"recognition": {"param": {"custom_recognition_param": {"controller": left}}}},
        "识别区域": {"action": {"param": {"custom_action_param": {"controller": right, "from_screen": True}}}},
    })
    monkeypatch.setattr(zones, "cache_is_ready", lambda *_: pytest.fail("controller rejected before cache read"))
    with pytest.raises(layout.LayoutCalibrationRequired, match="controller"):
        layout.require_inverse_crown_layout(context)


@pytest.mark.parametrize("image", [None, np.zeros((0, 0, 3), dtype=np.uint8), np.zeros((720, 1280), dtype=np.uint8)])
def test_unknown_screen_geometry_is_rejected(calibrated, image):
    context, _, _ = calibrated
    context.tasker.controller.cached_image = image
    with pytest.raises(layout.LayoutCalibrationRequired, match="screen_geometry_unavailable"):
        layout.require_inverse_crown_layout(context)


@pytest.mark.parametrize("fault", ["no_load", "changed_file", "wrong_offset", "offscreen_input", "wrong_slot", "offscreen_roi"])
def test_readback_rejects_incomplete_or_inconsistent_application(calibrated, monkeypatch, fault):
    context, path, _ = calibrated
    apply = layout.LoadRoiZone.apply_offsets
    def altered(loader, ctx, controller, *, hud_screen_size=None):
        if fault == "no_load":
            return
        apply(loader, ctx, controller, hud_screen_size=hud_screen_size)
        if fault == "changed_file":
            path.write_bytes(path.read_bytes() + b" ")
        elif fault == "wrong_offset":
            ctx.override_pipeline({"技能": {"action": {"param": {"target_offset": [0, 0, 0, 0]}}}})
        elif fault == "offscreen_input":
            ctx.override_pipeline({"技能": {"action": {"param": {"target": [3000, 4000]}}}})
        elif fault == "wrong_slot":
            ctx.override_pipeline({"消球2": {"action": {"param": {"target": [1003, 500]}}}})
        elif fault == "offscreen_roi":
            ctx.override_pipeline({"检查逆冕特殊球": {"recognition": {"param": {"roi": [3000, 4000, 10, 10]}}}})
    monkeypatch.setattr(layout.LoadRoiZone, "apply_offsets", altered)
    with pytest.raises(layout.LayoutCalibrationRequired):
        layout.require_inverse_crown_layout(context)
    assert context.actions == []


def test_valid_layout_does_not_unlock_legacy_strategy(calibrated):
    context, _, _ = calibrated
    context.override_pipeline({"LayoutRunner": {"attach": {"inverse_crown_profile": {"enabled": True}}}})
    action = runner.CombatRunner()
    assert not action.run(context, SimpleNamespace(node_name="LayoutRunner")).success
    assert action.last_outcome.exit_reason == "strategy_not_ready"
    assert action.last_outcome.game_outcome == "UNKNOWN"
    assert context.actions == []


def test_disabled_runner_performs_no_layout_reads(monkeypatch):
    context = FakeContext(node_data={"ordinary": {"attach": {"inverse_crown_profile": {"enabled": False}}}})
    reads = []
    def read(name):
        reads.append(name)
        assert name == "ordinary"
        return context.node_data[name]
    monkeypatch.setattr(context, "get_node_data", read)
    monkeypatch.setattr(runner, "CombatTask", lambda *_args, **_kwargs: SimpleNamespace(combat_once=lambda: None, outcome=None))
    monkeypatch.setattr(runner, "require_inverse_crown_layout", lambda _: pytest.fail("ordinary mode must not read calibration"))
    assert runner.CombatRunner().run(context, SimpleNamespace(node_name="ordinary")).success
    assert reads == ["ordinary"]


def test_layout_failure_prevents_combat_construction(calibrated, monkeypatch):
    context, path, _ = calibrated
    path.unlink()
    context.override_pipeline({"LayoutRunner": {"attach": {"inverse_crown_profile": {"enabled": True}}}})
    monkeypatch.setattr(runner, "CombatTask", lambda *_args, **_kwargs: pytest.fail("layout failure must precede combat"))
    action = runner.CombatRunner()
    assert not action.run(context, SimpleNamespace(node_name="LayoutRunner")).success
    assert action.last_outcome.callback_status == "REJECTED"
    assert action.last_outcome.exit_reason == "layout_calibration_required"
    assert action.last_outcome.exception_type == "LayoutCalibrationRequired"
    assert action.last_outcome.game_outcome == "UNKNOWN"


def test_native_runner_load_is_local_to_its_real_context(calibrated):
    """Real Maa Context/Runner, synthetic image, no physical or simulated inputs."""
    _, path, _ = calibrated
    class ImageOnly(CustomController):
        def connect(self): return True
        def request_uuid(self): return "inverse-layout-unit"
        def get_features(self): return 0
        def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
        def reject(self, *args): raise AssertionError("no input allowed")
        start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = reject
    records = []
    details = []
    class Inspect(CustomAction):
        def run(self, ctx, argv):
            if argv.node_name == "NativeLayoutRun":
                result = runner.CombatRunner().run(ctx, argv)
                assert not result.success  # strategy_not_ready remains in force
                layout.require_inverse_crown_layout(ctx)  # repeat in same Context
            records.append(deepcopy(ctx.get_node_data("攻击")))
            details.append({name: deepcopy(ctx.get_node_data(name)) for name in (
                "技能", "检查血量", "检查逆冕能量空",
            )})
            assert not ctx.get_node_data("覆盖布局菜单")["enabled"]
            return True
    resource = Resource()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert resource.register_custom_action("LayoutInspect", Inspect())
    assert resource.override_pipeline({name: {
        "action": {"type": "Custom", "param": {"custom_action": "LayoutInspect"}},
        "attach": {"inverse_crown_profile": {"enabled": name == "NativeLayoutRun"}},
        "pre_delay": 0, "post_delay": 0,
    } for name in ("NativeLayoutRun", "NativeLayoutOrdinary")})
    controller = ImageOnly()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    before = path.read_bytes()
    try:
        assert controller.post_screencap().wait().succeeded
        for name in ("NativeLayoutOrdinary", "NativeLayoutRun", "NativeLayoutOrdinary", "NativeLayoutRun"):
            assert tasker.post_task(name).wait().succeeded
        assert [row["action"]["param"]["target_offset"] for row in records] == [
            [0, 0, 0, 0], [-20, -30, 0, 0], [0, 0, 0, 0], [-20, -30, 0, 0],
        ]
        assert resource.get_node_data("攻击")["action"]["param"]["target_offset"] == [0, 0, 0, 0]
        assert [row["技能"]["action"]["param"]["target"][1] for row in details] == [690, 634, 690, 634]
        assert [row["检查血量"]["recognition"]["param"]["roi"] for row in details] == [
            [19, 15, 141, 29], [70, 15, 140, 29], [19, 15, 141, 29], [70, 15, 140, 29],
        ]
        assert resource.get_node_data("技能")["action"]["param"]["target"][1] == 690
        assert resource.get_node_data("检查血量")["recognition"]["param"]["roi"] == [19, 15, 141, 29]
        assert path.read_bytes() == before
    finally:
        tasker.post_stop().wait()
