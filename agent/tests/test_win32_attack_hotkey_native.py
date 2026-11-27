"""The PI's desktop attack hotkey must pair both native key edges."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from maa.custom_action import CustomAction
from maa.define import MaaControllerFeatureEnum
from maa.resource import Resource
from maa.tasker import Tasker

from action.combat.actions.CombatActions import CombatActions
from test_r01_zero_refresh_native import Controller
from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
PI = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
WIN32 = [entry for entry in PI["controller"] if entry["type"] == "Win32"]
CN = [entry for entry in PI["resource"] if entry["name"] in {"zh_CN", "zh_TW"}]
PROBE = "内存攻击按键对照"


def option_override(configured):
    path, = [entry for entry in PI["import"] if entry.removeprefix("./") == "options/global_option.json"]
    document = json.loads((ROOT / path).read_text(encoding="utf-8"))
    options = document["option"]
    assert "自定义键位" in document["global_option"]
    switch = options["自定义键位"]
    assert switch["default_case"] == "No"
    assert set(switch["controller"]) == {entry["name"] for entry in WIN32}
    choice = next(case for case in switch["cases"] if case["name"] == ("Yes" if configured else "No"))
    if not configured:
        assert not choice.get("pipeline_override") and not choice.get("option")
        return {}, ord("J")
    name, = choice["option"]
    hotkey = options[name]
    assert hotkey["type"] == "hotkey"
    attack = next(entry for entry in hotkey["hotkeys"] if entry["name"] == "攻击")
    assert attack["default"] == "J"
    overrides = {}
    # Resolve only this test's ASCII key. This does not stand in for the full
    # launcher compiler, and no real controller is attached to the tasker.
    for node, data in hotkey["pipeline_override"].items():
        if data["action"]["param"].get("key") == "{攻击.primary}":
            overrides[node] = deepcopy(data)
            overrides[node]["action"]["param"]["key"] = ord("A")
    return overrides, ord("A")


@pytest.mark.parametrize("controller", WIN32, ids=lambda row: row["name"])
@pytest.mark.parametrize("language", CN, ids=lambda row: row["name"])
@pytest.mark.parametrize("configured", [False, True], ids=["default-off", "custom-A"])
def test_chinese_desktop_attack_key_is_released_by_the_same_hotkey(tmp_path, controller, language, configured):
    overrides, expected = option_override(configured)
    events, errors = [], []

    class Keys(Controller):
        def get_features(self):
            return MaaControllerFeatureEnum.UseKeyboardDownAndUpInsteadOfClick

        def key_down(self, token):
            events.append(("down", token))
            return True

        def key_up(self, token):
            events.append(("up", token))
            return True

    class AttackPair(CustomAction):
        def run(self, context, _argv):
            try:
                actions = CombatActions(context)
                down = actions.down_attack()
                up = actions.up_attack()
                return self.RunResult(success=bool(down and up and down.success and up.success))
            except Exception as error:
                errors.append(repr(error))
                return self.RunResult(success=False)

    assert Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.set_cpu()
    for path in language["path"] + controller["attach_resource_path"]:
        assert resource.post_bundle(ROOT / path).wait().succeeded
    assert resource.register_custom_action(PROBE, AttackPair())
    keys, tasker = Keys(), Tasker()
    assert keys.post_connection().wait().succeeded and tasker.bind(resource, keys)
    for name in ("按下攻击", "松开攻击"):
        overrides.setdefault(name, {}).update(pre_delay=0, post_delay=0)
    overrides[PROBE] = {
        "action": {"type": "Custom", "param": {"custom_action": PROBE}},
        "pre_delay": 0, "post_delay": 0, "next": [], "on_error": [],
    }
    try:
        job = tasker.post_task(PROBE, overrides).wait()
        assert job.succeeded and not errors
        assert events == [("down", expected), ("up", expected)]
    finally:
        stop_and_wait_idle(tasker)
