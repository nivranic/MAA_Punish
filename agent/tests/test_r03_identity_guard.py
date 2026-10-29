"""R03 task-local identity rejection, without recognition-time combat input."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from action.combat.core import provider, role_factory
from action.combat.core.provider import CombatCheck, Roguelike3IdentityError
from action.combat.core.session import CombatTask
from action.combat.core.team import TEAM_ROSTER_NODE, TeamSnapshot
from action.combat.config.inverse_crown_profile import InverseCrownProfile, InverseCrownUnavailable
from action.combat.entry.runner import CombatRunner
from test_support.fakes import FakeContext, make_hit

POLICY = {"version": 1, "require_known_field": True}
NODE = {"attach": {"roguelike3_identity": POLICY}}
ROOT = Path(__file__).resolve().parents[2]


def no_inputs(context):
    assert context.actions == []
    assert context.tasker.controller.clicks == []
    assert context.tasker.controller.swipes == []
    assert context.tasker.controller.touches == []


def combat(*, r3=True, roster=None):
    context = FakeContext(recognition_map={"检查闪避": make_hit()})
    if roster is not None:
        context.node_data[TEAM_ROSTER_NODE] = {"attach": roster}
    result = CombatTask(context, CombatCheck(), roguelike3_identity_required=r3)
    result.frame = b"saved-test-pixels"
    return result


def detection(monkeypatch, target, answer):
    calls = []
    def detect(*args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("on_tick") is not None:
            kwargs["on_tick"]()
        if isinstance(answer, BaseException):
            raise answer
        return answer
    monkeypatch.setattr(target, "detect_current_role", detect)
    return calls


@pytest.mark.parametrize("policy", [
    None, {}, True, {"version": True, "require_known_field": True},
    {"version": 1.0, "require_known_field": True}, {"version": 2, "require_known_field": True},
    {"version": 1, "require_known_field": 1}, {"version": 1, "require_known_field": False},
    {"version": 1, "require_known_field": True, "cls_name": "Oblivion"},
])
def test_current_node_policy_is_strict_and_invalid_is_rejected(policy):
    with pytest.raises(Roguelike3IdentityError, match="r3_identity_policy_invalid"):
        provider.roguelike3_identity_required({"attach": {"roguelike3_identity": policy}})


def test_policy_requires_current_node_marker_and_does_not_read_role_selection():
    assert provider.roguelike3_identity_required(deepcopy(NODE))
    for value in (None, [], False, 42, "{}", {}, {"attach": {}}, {"attach": {"selection_mode": "roguelike_3"}}):
        assert not provider.roguelike3_identity_required(value)


@pytest.mark.parametrize("roster", [None, {"R": "GeneralFight", "B": "", "Y": ""}, {"R": "Oblivion", "B": "", "Y": ""}])
def test_r03_unknown_cannot_use_published_shortcut_or_blind_attack(monkeypatch, roster):
    item = combat(roster=roster)
    calls = detection(monkeypatch, provider, ("未知", "GeneralFight"))
    with pytest.raises(Roguelike3IdentityError, match="r3_field_identity_unknown"):
        item.combat_check.detect_team(item.context, item)
    assert len(calls) == 1 and calls[0]["on_tick"] is None
    no_inputs(item.context)


@pytest.mark.parametrize("answer", [("终焉", "Oblivion"), ("回音", "GeneralFight")])
def test_r03_known_generic_and_specialized_fields_are_valid(monkeypatch, answer):
    item = combat()
    calls = detection(monkeypatch, provider, answer)
    monkeypatch.setattr(provider, "detect_visible_team_colors", lambda *_: [])
    snapshot = item.combat_check.detect_team(item.context, item)
    assert snapshot == TeamSnapshot.solo(answer[1])
    assert item.current_role_name == answer[0] and item.field_identity == "confirmed"
    assert calls[0]["on_tick"] is None
    no_inputs(item.context)


def test_r03_stale_specialized_roster_mismatch_is_rejected(monkeypatch):
    item = combat(roster={"R": "Oblivion", "B": "", "Y": ""})
    detection(monkeypatch, provider, ("回音", "GeneralFight"))
    with pytest.raises(Roguelike3IdentityError, match="r3_field_identity_mismatch"):
        item.combat_check.detect_team(item.context, item)
    no_inputs(item.context)


def test_r03_verified_published_specialized_roster_still_requires_current_pixels(monkeypatch):
    item = combat(roster={"R": "Oblivion", "B": "", "Y": ""})
    calls = detection(monkeypatch, provider, ("终焉", "Oblivion"))
    assert item.combat_check.detect_team(item.context, item) == TeamSnapshot.solo("Oblivion")
    assert len(calls) == 1
    no_inputs(item.context)


def bound(item, cls_name="GeneralFight"):
    item.team = TeamSnapshot.solo(cls_name)
    role = role_factory.create_role(item, "R", cls_name)
    item.roles = {"R": role}
    return role


@pytest.mark.parametrize("stage", ["correct", "refresh"])
@pytest.mark.parametrize("answer,cls_name,accepted", [
    (("未知", "GeneralFight"), "GeneralFight", False),
    (("回音", "GeneralFight"), "GeneralFight", True),
    (("终焉", "Oblivion"), "Oblivion", True),
    (("回音", "GeneralFight"), "Oblivion", False),
])
def test_r03_setup_and_tick_identity_checks_are_input_free(monkeypatch, stage, answer, cls_name, accepted):
    import action.combat.core.session as session
    item = combat(); role = bound(item, cls_name)
    calls = detection(monkeypatch, session, answer)
    perform = lambda: item.refresh_field_role(role) if stage == "refresh" else item._correct_role_from_field("R", cls_name, item.frame, solo=True)
    if accepted:
        perform()
        assert item.field_identity == "confirmed"
    else:
        with pytest.raises(Roguelike3IdentityError): perform()
        assert item.field_identity == "unknown"
    assert calls and all(call["on_tick"] is None for call in calls)
    no_inputs(item.context)


@pytest.mark.parametrize("missing", ["frame", "team", "hud"])
def test_r03_tick_missing_identity_evidence_rejects_before_role_action(monkeypatch, missing):
    item = combat(); role = bound(item)
    if missing == "frame": item.frame = None
    elif missing == "team": item.team = None
    else: item.context.recognition_map.clear()
    monkeypatch.setattr(role, "do_perform", lambda: pytest.fail("Unknown identity cannot perform"))
    with pytest.raises(Roguelike3IdentityError, match="r3_field_identity_unknown"):
        role.perform()
    no_inputs(item.context)


def test_r03_factory_fallback_is_not_accepted_as_recognized_generic(monkeypatch):
    import action.combat.core.session as session
    item = combat()
    monkeypatch.delitem(role_factory.ROLE_CLASS_MAP, "Oblivion")
    role = bound(item, "Oblivion")
    detection(monkeypatch, session, ("终焉", "Oblivion"))
    with pytest.raises(Roguelike3IdentityError, match="r3_strategy_identity_mismatch"):
        item._correct_role_from_field("R", "Oblivion", item.frame, solo=True)
    assert role.fallback_reason == "requested_strategy_unavailable"
    no_inputs(item.context)


@pytest.mark.parametrize("failure", ["unknown", "recognition_error"])
def test_r03_original_cleanup_and_immutable_result_remain_truthful(monkeypatch, failure):
    item = combat()
    closed, outcomes = [], []
    item.input_scope = SimpleNamespace(close=lambda: closed.append(True) or True)
    def enter(): item._in_combat = True
    monkeypatch.setattr(item, "_wait_in_combat", enter)
    error = OSError("PRIVATE_RECOGNITION_ERROR")
    detection(monkeypatch, provider, error if failure == "recognition_error" else ("未知", "GeneralFight"))
    monkeypatch.setattr("action.combat.core.session.emit_combat_outcome", lambda _, value: outcomes.append(value))
    with pytest.raises(OSError if failure == "recognition_error" else Roguelike3IdentityError): item.combat_once()
    item.combat_end()
    assert len(closed) == len(outcomes) == 1
    assert item.team is None and item.roles == {} and item.frame is None and not item._in_combat
    assert item.outcome.game_outcome == "UNKNOWN"
    assert item.outcome.callback_status == ("ERROR" if failure == "recognition_error" else "REJECTED")
    assert item.outcome.exception_type == ("OSError" if failure == "recognition_error" else "Roguelike3IdentityError")
    assert "PRIVATE" not in json.dumps(item.outcome.to_event())
    no_inputs(item.context)


def test_r03_runner_consumes_task_policy_and_returns_false_after_real_cleanup(monkeypatch):
    context = FakeContext(node_data={"战斗中": deepcopy(NODE)})
    def enter(self): self.frame = b"pixels"; self._in_combat = True
    monkeypatch.setattr(CombatTask, "_wait_in_combat", enter)
    detection(monkeypatch, provider, ("未知", "GeneralFight"))
    action = CombatRunner()
    assert action.run(context, SimpleNamespace(node_name="战斗中", custom_action_param="{}")).success is False
    assert action.last_outcome.callback_status == "REJECTED"
    assert action.last_outcome.exit_reason == "r3_field_identity_unknown"
    assert action.last_outcome.game_outcome == "UNKNOWN"
    no_inputs(context)


def test_r03_setup_becoming_unknown_after_provider_rejects_before_first_input(monkeypatch):
    import action.combat.core.session as session
    item = combat()
    monkeypatch.setattr(item, "_wait_in_combat", lambda: setattr(item, "_in_combat", True))
    detection(monkeypatch, provider, ("回音", "GeneralFight"))
    calls = detection(monkeypatch, session, ("未知", "GeneralFight"))
    monkeypatch.setattr(provider, "detect_visible_team_colors", lambda *_: [])
    with pytest.raises(Roguelike3IdentityError, match="r3_field_identity_unknown"):
        item.combat_once()
    assert len(calls) == 1 and calls[0]["on_tick"] is None
    assert item.outcome.callback_status == "REJECTED" and item.outcome.field_identity == "unknown"
    assert item.outcome.role_bindings[0].requested_cls_name == "GeneralFight"
    no_inputs(item.context)


def test_r03_invalid_policy_runner_rejects_without_constructing_combat(monkeypatch):
    import action.combat.entry.runner as runner
    context = FakeContext(node_data={"战斗中": {"attach": {"roguelike3_identity": {}}}})
    monkeypatch.setattr(runner, "CombatTask", lambda *_args, **_kwargs: pytest.fail("Invalid policy cannot create a combat session"))
    action = CombatRunner()
    assert not action.run(context, SimpleNamespace(node_name="战斗中")).success
    assert action.last_outcome.callback_status == "REJECTED"
    assert action.last_outcome.exit_reason == "r3_identity_policy_invalid"
    assert action.last_outcome.game_outcome == "UNKNOWN"
    no_inputs(context)


@pytest.mark.parametrize("failure", ["recognition", "cleanup", "emission"])
def test_r03_runner_consumes_real_error_outcome_as_native_false(monkeypatch, failure):
    import action.combat.core.session as session
    context = FakeContext(node_data={"战斗中": deepcopy(NODE)})

    def fail(*_args):
        raise OSError("PRIVATE_R03_ERROR")

    def enter(self):
        if failure == "recognition":
            fail()
        if failure == "cleanup":
            self.input_scope = SimpleNamespace(close=fail)
        self.out_of_combat_reason = "enter_timeout"

    monkeypatch.setattr(CombatTask, "_wait_in_combat", enter)
    if failure == "emission":
        monkeypatch.setattr(session, "emit_combat_outcome", fail)
    action = CombatRunner()
    assert action.run(context, SimpleNamespace(node_name="战斗中")).success is False
    assert action.last_outcome.callback_status == "ERROR"
    assert action.last_outcome.exception_type == "OSError"
    assert action.last_outcome.game_outcome == "UNKNOWN"
    assert "PRIVATE" not in json.dumps(action.last_outcome.to_event())
    if failure == "cleanup":
        assert action.last_outcome.cleanup_exception_type == "OSError"
    if failure == "emission":
        assert action.last_outcome.emission_exception_type == "OSError"
    no_inputs(context)


@pytest.mark.parametrize("scene", ["single", "multi"])
def test_r03_marker_cannot_bypass_either_closed_inverse_strategy_gate(scene):
    context = FakeContext()
    item = CombatTask(context, CombatCheck(), roguelike3_identity_required=True,
                      inverse_crown_profile=InverseCrownProfile(enabled=True, scene=scene))
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        item.combat_once()
    assert item.outcome.callback_status == "REJECTED" and item.outcome.exit_reason == "strategy_not_ready"
    no_inputs(context)


def test_canonical_task_adds_only_local_policy_and_stop_sink():
    task = json.loads((ROOT / "tasks/矩阵循生.json").read_bytes())
    patch = task["task"][0]["pipeline_override"]
    assert patch["战斗中"] == {**NODE, "on_error": ["矩阵循生_身份拒绝停止"]}
    assert patch["矩阵循生_身份拒绝停止"] == dict(recognition=dict(type="DirectHit"), action=dict(type="StopTask"), next=[], on_error=[], enabled=True)
    assert all("战斗中" not in row["pipeline_override"] for row in task["option"]["选择模式"]["cases"])
