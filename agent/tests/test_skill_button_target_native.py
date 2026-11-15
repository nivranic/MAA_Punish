"""Skill inputs stay inside the button after layout translation."""
import json
from pathlib import Path

import pytest
from maa.custom_action import CustomAction
from maa.define import MaaControllerFeatureEnum
from maa.tasker import Tasker

from action.basics.InterfaceZone import roi_zone_controller as policy
from action.basics.InterfaceZone.LoadRoiZone import LoadRoiZone
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("platform", ["base", "playcover", "pgr_win32"])
@pytest.mark.parametrize("offset", [[0, 0, 0, 0], [40, -35, 0, 0], [-120, 0, 0, 0]])
@pytest.mark.parametrize("node", ["技能", "长按技能"])
def test_skill_tap_and_hold_use_the_calibrated_button_interior(tmp_path, monkeypatch, platform, offset, node):
    controller_type = "win32" if platform == "pgr_win32" else "adb"
    cache = {
        key: {"hit": True, "offset": offset if key == "skill_zone" else [0, 0, 0, 0]}
        for key, _, _ in policy.zones_for(controller_type)
    }
    cache_path = tmp_path / "layout.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setitem(policy.OFFSET_FILES, controller_type, cache_path)
    inputs, errors = [], []

    class Capture(Controller):
        def get_features(self):
            return MaaControllerFeatureEnum.UseKeyboardDownAndUpInsteadOfClick if platform == "pgr_win32" else 0
        def click(self, x, y, *args):
            inputs.append(("touch", x, y))
            return True
        def swipe(self, x, y, ex, ey, *args):
            inputs.extend([("touch", x, y), ("touch", ex, ey)])
            return True
        def click_key(self, key):
            inputs.append(("key", key))
            return True
        def key_down(self, key):
            inputs.append(("down", key))
            return True
        def key_up(self, key):
            inputs.append(("up", key))
            return True

    class Run(CustomAction):
        def run(self, context, argv):
            try:
                loader = LoadRoiZone()
                loader.apply_offsets(context, controller_type)
                loader.apply_offsets(context, controller_type)
                return bool(context.run_action(node))
            except Exception as exc:
                errors.append(repr(exc))
                return False

    resource = load_resource(tmp_path)
    if platform != "base":
        assert resource.post_bundle(Path(__file__).resolve().parents[2] / "resource" / platform).wait().succeeded
    assert resource.register_custom_action("SkillTargetProbe", Run())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task("SkillTargetProbe", {
            "SkillTargetProbe": {"recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "SkillTargetProbe"}}, "next": []},
            "长按技能": {"action": {"param": {"duration": 1}}},
        }).wait()
        assert job.succeeded and not errors
        if platform == "pgr_win32":
            assert inputs == [("down", 76), ("up", 76)]
        else:
            _, _, zone = next(row for row in policy.zones_for("adb") if row[0] == "skill_zone")
            center = (zone[0] + zone[2] // 2 + offset[0], zone[1] + zone[3] // 2 + offset[1])
            assert inputs
            for kind, x, y in inputs:
                assert kind == "touch"
                assert abs(x - center[0]) <= zone[2] // 4
                assert abs(y - center[1]) <= zone[3] // 4
    finally:
        stop_and_wait_idle(tasker)
