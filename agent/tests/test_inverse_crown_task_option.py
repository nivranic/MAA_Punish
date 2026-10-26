"""Public profile contract and real Maa routing; never device acceptance.

The native cases use actual base resources and CombatRunner. Page recognition
is a memory endpoint and the layout preflight is bypassed solely to reach the
unchanged strategy-not-ready guard. App input compilation is verified separately
against the pinned RunPlanBuilder, not emulated by this module.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace

import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from action.basics import ScopedRecovery as recovery
from action.combat.config.inverse_crown_profile import InverseCrownProfile
from action.combat.core import session
from action.combat.entry import runner


ROOT = Path(__file__).resolve().parents[2]
PARENT = "逆冕站场专项"
CHILD = "逆冕本场档案"
OPTIONS = json.loads((ROOT / "options/inverse_crown_profile.json").read_text(encoding="utf-8"))["option"]
TASK = json.loads((ROOT / "tasks/战斗辅助.json").read_text(encoding="utf-8"))
YES = next(case for case in OPTIONS[PARENT]["cases"] if case["name"] == "Yes")["pipeline_override"]
NO = next(case for case in OPTIONS[PARENT]["cases"] if case["name"] == "No")["pipeline_override"]


def test_public_option_is_last_and_cannot_silently_skip_saved_yes_by_controller():
    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    assert interface["import"].count("./options/inverse_crown_profile.json") == 1
    assert TASK["task"][0]["option"][-1] == PARENT
    assert CHILD not in TASK["task"][0]["option"]
    assert OPTIONS[PARENT]["default_case"] == "No"
    assert "controller" not in OPTIONS[PARENT] and "controller" not in OPTIONS[CHILD]
    assert next(case for case in OPTIONS[PARENT]["cases"] if case["name"] == "Yes")["option"] == [CHILD, "逆冕开始方式"]
    assert set(NO) == {"战斗中", "逆冕准备页开始"}
    assert set(NO["战斗中"]) == {"attach"}
    assert InverseCrownProfile.from_node_data(NO["战斗中"]) == InverseCrownProfile()
    assert YES["战斗辅助"]["next"] == ["战斗中"]
    assert YES["战斗辅助"]["timeout"] == 5000
    for name in ("战斗辅助", "战斗中"):
        assert YES[name]["on_error"] == ["返回主菜单_err"]
        assert "enabled" not in YES[name]
    assert YES["战斗中"]["next"] == []
    assert InverseCrownProfile.from_node_data(YES["战斗中"]) == InverseCrownProfile(enabled=True)


@pytest.mark.parametrize("field,values", [
    ("scene", ["single", "multi", "unknown"]),
    ("source", ["self", "trial", "borrowed", "mode", "unknown"]),
    ("rank", ["S", "S5", "SS", "SS3", "SSS", "SSS3", "SSS6", "SSS+", "unknown"]),
    ("signature_weapon_confirmed", ["true", "false"]),
])
def test_every_supported_literal_is_accepted_by_public_input_verifier(field, values):
    definition = next(item for item in OPTIONS[CHILD]["inputs"] if item["name"] == field)
    for value in values:
        assert re.fullmatch(definition["verify"], value), (field, value)
    assert definition["default"] == ("false" if field == "signature_weapon_confirmed" else "unknown")
    assert definition["pipeline_type"] == ("bool" if field == "signature_weapon_confirmed" else "string")


@pytest.mark.parametrize("field,value", [
    ("scene", "boss"), ("source", "account"), ("rank", "A"), ("rank", "SSS" + "\\"),
    ("signature_weapon_confirmed", "True"), ("signature_weapon_confirmed", "1"),
    ("signature_weapon_confirmed", "yes"), ("signature_weapon_confirmed", ""),
])
def test_malformed_input_is_rejected_before_it_can_turn_into_battle_facts(field, value):
    definition = next(item for item in OPTIONS[CHILD]["inputs"] if item["name"] == field)
    assert re.fullmatch(definition["verify"], value) is None


def test_compound_publishes_whole_profile_and_all_public_text_has_four_translations():
    profile = OPTIONS[CHILD]["pipeline_override"]["战斗中"]["attach"]["inverse_crown_profile"]
    assert profile == {"enabled": True, "scene": "{scene}", "source": "{source}",
                       "rank": "{rank}", "signature_weapon_confirmed": "{signature_weapon_confirmed}"}
    assert OPTIONS[CHILD]["pipeline_override"]["逆冕准备页开始"]["attach"]["inverse_crown_profile"] == profile
    start = OPTIONS["逆冕开始方式"]
    assert start["default_case"] == "takeover"
    assert start["cases"][0]["pipeline_override"] == {"战斗辅助": {"next": ["战斗中"]}}
    assert start["cases"][1]["pipeline_override"] == {"战斗辅助": {"next": ["逆冕准备页开始"]}}
    keys = re.findall(r'"\$([^"\n]+)"', json.dumps(OPTIONS, ensure_ascii=False))
    for language in ("zh_cn", "zh_tw", "en_us", "ja_jp"):
        translations = json.loads((ROOT / f"i18n/{language}.json").read_text(encoding="utf-8"))
        assert all(isinstance(translations[key], str) and translations[key] for key in keys)


class _QuietLog:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _MemoryController(CustomController):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.captures = 0

    def connect(self): return True
    def request_uuid(self): return "inverse-profile-task-option-memory-only"
    def get_features(self): return 0
    def screencap(self):
        self.captures += 1
        return np.zeros((720, 1280, 3), np.uint8)
    def click(self, *args): self.inputs.append(("Click", *args)); return False
    def swipe(self, *args): self.inputs.append(("Swipe", *args)); return False
    def touch_down(self, *args): self.inputs.append(("TouchDown", *args)); return False
    def touch_up(self, *args): self.inputs.append(("TouchUp", *args)); return False
    def start_app(self, *args): self.inputs.append(("StartApp", *args)); return False
    def stop_app(self, *args): self.inputs.append(("StopApp", *args)); return False


class _MatchBattleAndDanger(CustomRecognition):
    def analyze(self, context, argv):
        if argv.node_name in {"战斗中", "逆冕准备页开始", "自动闪避后攻击", "战斗重启1"}:
            return (10, 10, 30, 30)
        return None


class _ObserveRunner(CustomAction):
    def __init__(self):
        super().__init__()
        self.actual = runner.CombatRunner()
        self.profile = None
        self.dangers_match = []

    def run(self, context, argv):
        self.profile = context.get_node_data(argv.node_name)["attach"]["inverse_crown_profile"]
        image = context.tasker.controller.cached_image
        self.dangers_match = [context.run_recognition(name, image).hit
                              for name in ("自动闪避后攻击", "战斗重启1")]
        return self.actual.run(context, argv)


@pytest.mark.parametrize("mode", ["自动检测", "持续战斗"])
@pytest.mark.parametrize("entry", ["战斗中", "逆冕准备页开始"])
def test_enabled_real_base_route_rejects_strategy_without_visiting_matchable_native_inputs(
        tmp_path, monkeypatch, mode, entry):
    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    quiet = lambda _name: SimpleNamespace(logger=_QuietLog(), emit_audit=lambda *_: None)
    for module in (session, runner, recovery):
        monkeypatch.setattr(module, "LoggerComponent", quiet)
    # Only the geometry preflight endpoint is bypassed; the real session's
    # strategy-not-ready guard below must remain active and return rejection.
    monkeypatch.setattr(runner, "require_inverse_crown_layout", lambda _context: None)
    resource = Resource()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    recognizer = _MatchBattleAndDanger()
    assert resource.register_custom_recognition("ProfileMatchFixture", recognizer)
    combat = _ObserveRunner()
    fallback = recovery.ScopedRecovery()
    assert resource.register_custom_action("CombatRunner", combat)
    assert resource.register_custom_action("ScopedRecovery", fallback)
    controller = _MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    fixture = {name: {"recognition": {"type": "Custom", "param": {
        "custom_recognition": "ProfileMatchFixture"}}, "pre_delay": 0, "post_delay": 0,
        "pre_wait_freezes": {"time": 0}, "post_wait_freezes": {"time": 0}, "rate_limit": 0}
        for name in ("战斗中", "逆冕准备页开始", "自动闪避后攻击", "战斗重启1", "检查主界面2")}
    fixture["战斗辅助"] = {"pre_delay": 0, "post_delay": 0}
    mode_patch = next(case for case in TASK["option"]["战斗模式"]["cases"] if case["name"] == mode)["pipeline_override"]
    job = tasker.post_task("战斗辅助", [TASK["task"][0]["pipeline_override"], mode_patch, fixture,
                                          deepcopy(YES), {"战斗辅助": {"next": [entry]}}])
    job.wait()
    until = time.monotonic() + 3
    while time.monotonic() < until:
        detail = tasker.get_task_detail(job.job_id)
        if detail is not None and detail.status.done:
            break
        time.sleep(.005)
    else:
        pytest.fail("native TaskDetail did not become terminal")
    assert combat.profile == YES["战斗中"]["attach"]["inverse_crown_profile"]
    assert combat.dangers_match == [True, True]
    assert combat.actual.last_outcome.exit_reason == "strategy_not_ready"
    assert combat.actual.last_outcome.callback_status == "REJECTED"
    names = [tasker.get_node_detail(node_id).name for node_id in detail.node_id_list]
    assert names[:2] == ["战斗辅助", entry]
    assert "返回主菜单_err" in names
    assert not {"自动闪避后攻击", "战斗重启1", "攻击", "逆冕本次作战开始点击"} & set(names)
    assert fallback.last_result["business_success"] is False
    assert controller.captures >= 1
    assert controller.inputs == []
