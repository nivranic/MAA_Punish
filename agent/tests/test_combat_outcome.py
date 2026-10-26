"""Combat callback completion never substitutes for game-result evidence."""

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from action.combat.config.inverse_crown_profile import (
    InverseCrownProfile,
    InverseCrownUnavailable,
)
from action.combat.core import role_factory
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.entry import runner
from action.combat.entry.inverse_crown_layout import LayoutCalibrationRequired
from test_support.fakes import FakeContext


def _combat(*, enabled=False):
    return CombatTask(
        FakeContext(), CombatCheck(),
        inverse_crown_profile=InverseCrownProfile(enabled=enabled),
    )


def _entered(combat):
    combat._in_combat = True


def test_result_survives_cleanup_and_mutation_and_emits_once(monkeypatch):
    combat = _combat()
    combat.team = TeamSnapshot.solo("GeneralFight")
    role = role_factory.create_role(combat, "R", "GeneralFight")
    combat.roles["R"] = role
    combat.field_identity = "confirmed"
    combat.battle_state = "active"
    combat.loop_count = 7
    combat.out_of_combat_reason = "combat_end_condition"
    closed, events = [], []
    combat.input_scope = SimpleNamespace(close=lambda: closed.append(True) or True)

    def emit(component, outcome):
        assert closed == [True]
        assert combat.roles == {"R": role} and combat.team is not None
        events.append(outcome.to_event())

    monkeypatch.setattr("action.combat.core.session.emit_combat_outcome", emit)
    combat.combat_end()
    outcome = combat.outcome
    combat.combat_end()
    assert len(events) == len(closed) == 1
    assert combat.team is None and combat.roles == {}
    assert combat.field_identity == "unknown"
    assert outcome.field_identity == "confirmed"
    assert outcome.battle_state == "active" and outcome.loop_count == 7
    assert outcome.game_outcome == "UNKNOWN"
    role.requested_cls_name = "changed"
    role.implementation_name = "changed"
    role.fallback_reason = "changed"
    assert outcome.role_bindings[0].requested_cls_name == "GeneralFight"
    assert outcome.role_bindings[0].implementation_name.endswith(".GeneralFight")
    assert outcome.role_bindings[0].fallback_reason is None
    with pytest.raises(FrozenInstanceError):
        outcome.field_identity = "changed"
    with pytest.raises(FrozenInstanceError):
        outcome.role_bindings[0].requested_cls_name = "changed"
    events[0]["role_bindings"][0]["requested_cls_name"] = "external mutation"
    assert outcome.role_bindings[0].requested_cls_name == "GeneralFight"


def test_strict_rejection_retains_the_unassigned_actual_fallback(monkeypatch):
    monkeypatch.delitem(role_factory.ROLE_CLASS_MAP, "InverseCrown")
    combat = _combat(enabled=True)
    monkeypatch.setattr(combat, "ensure_strategy_ready", lambda: None)
    monkeypatch.setattr(combat, "_wait_in_combat", lambda: _entered(combat))
    monkeypatch.setattr(
        combat.combat_check, "detect_team",
        lambda *_: TeamSnapshot.solo("InverseCrown"),
    )
    with pytest.raises(InverseCrownUnavailable, match="strategy_identity_mismatch"):
        combat.combat_once()
    assert combat.roles == {}
    assert combat.outcome.callback_status == "REJECTED"
    assert combat.outcome.exit_reason == "strategy_identity_mismatch"
    binding, = combat.outcome.role_bindings
    assert binding.requested_cls_name == "InverseCrown"
    assert binding.implementation_name.endswith(".GeneralFight")
    assert binding.fallback_reason == "requested_strategy_unavailable"


def test_real_rebinding_history_is_retained_and_identical_bindings_are_deduplicated():
    combat = _combat()
    combat.team = TeamSnapshot.solo("GeneralFight")
    combat.roles["R"] = role_factory.create_role(combat, "R", "GeneralFight")
    combat._rebind_role_at("R", "InverseCrown")
    role_factory.create_role(combat, "R", "InverseCrown")
    combat._rebind_role_at("R", "GeneralFight")
    combat.combat_end()
    assert [binding.requested_cls_name for binding in combat.outcome.role_bindings] == [
        "GeneralFight", "InverseCrown", "GeneralFight",
    ]
    assert [binding.sequence for binding in combat.outcome.role_bindings] == [1, 2, 3]


def test_binding_history_has_an_explicit_bound_and_truncation_count():
    combat = _combat()
    for index in range(combat.ROLE_BINDING_HISTORY_LIMIT + 5):
        combat.record_role_binding(SimpleNamespace(
            color="R", requested_cls_name=f"Role{index % 2}",
            implementation_name=f"roles.Role{index % 2}", fallback_reason=None,
        ))
    combat.combat_end()
    assert len(combat.outcome.role_bindings) == combat.ROLE_BINDING_HISTORY_LIMIT
    assert combat.outcome.role_bindings_truncated == 5
    assert combat.outcome.role_bindings[0].sequence == 6
    assert combat.outcome.role_bindings[-1].sequence == 69


def test_cleanup_failure_does_not_replace_the_original_exit_reason():
    combat = _combat()
    combat.out_of_combat_reason = "stopped"
    scope = SimpleNamespace(close=lambda: False, release_pending=True)
    combat.input_scope = scope
    combat.combat_end()
    assert combat.out_of_combat_reason == "stopped"
    assert combat.outcome.exit_reason == "stopped"
    assert combat.outcome.cleanup_reason == "input_stop_unconfirmed"
    assert combat.input_scope is scope and scope.release_pending


def test_cleanup_exception_is_recorded_and_propagates_when_it_is_the_only_error():
    combat = _combat()
    combat.out_of_combat_reason = "stopped"
    error = OSError("private cleanup detail")

    def close():
        raise error

    combat.input_scope = SimpleNamespace(close=close)
    with pytest.raises(OSError) as raised:
        combat.combat_end()
    assert raised.value is error
    assert combat.outcome.callback_status == "ERROR"
    assert combat.outcome.exit_reason == "stopped"
    assert combat.outcome.cleanup_reason == "input_cleanup_exception"
    assert combat.outcome.cleanup_exception_type == "OSError"
    assert combat.team is None and combat.roles == {}


def test_cleanup_exception_does_not_hide_original_exception_or_log_its_message(monkeypatch, capsys):
    combat = _combat()
    primary = ValueError("PRIVATE_PRIMARY_SECRET")

    def fail():
        raise primary

    def close():
        raise OSError("PRIVATE_CLEANUP_SECRET")

    monkeypatch.setattr(combat, "_wait_in_combat", fail)
    combat.input_scope = SimpleNamespace(close=close)
    with pytest.raises(ValueError) as raised:
        combat.combat_once()
    assert raised.value is primary
    assert combat.outcome.exit_reason == "exception"
    assert combat.outcome.exception_type == "ValueError"
    assert combat.outcome.cleanup_exception_type == "OSError"
    assert "PRIVATE_" not in json.dumps(combat.outcome.to_event())
    assert "PRIVATE_" not in capsys.readouterr().out


@pytest.mark.parametrize("reason", [
    "combat_end_condition", "hp_zero", "outer_interface", "combat_ui_lost",
    "stopped", "on_combat_check_failed", "team_detect_failed", "enter_timeout",
])
def test_actual_exit_branches_remain_unknown_game_outcomes(monkeypatch, reason):
    combat = _combat()
    if reason == "enter_timeout":
        combat.wait_combat_time = 0
    else:
        monkeypatch.setattr(combat, "_wait_in_combat", lambda: _entered(combat))
    monkeypatch.setattr(combat, "load_team", lambda: reason != "team_detect_failed")
    combat.context.tasker.stopping = reason == "stopped"
    monkeypatch.setattr(combat, "_capture_frame", lambda: None)
    monkeypatch.setattr(combat, "perform", lambda: None)
    presence = reason if reason in {"hp_zero", "outer_interface", "combat_ui_lost"} else ""
    monkeypatch.setattr(combat, "_check_combat_presence", lambda: presence)
    monkeypatch.setattr(combat.combat_check, "combat_end_condition", lambda *_: reason == "combat_end_condition")
    monkeypatch.setattr(combat.combat_check, "on_combat_check", lambda *_: False)
    combat.combat_once()
    assert combat.outcome.exit_reason == reason
    assert combat.outcome.callback_status == "COMPLETED"
    assert combat.outcome.game_outcome == "UNKNOWN"


def _complete_with_identity(combat):
    role_factory.create_role(combat, "R", "GeneralFight")
    combat.field_identity = "confirmed"
    combat.out_of_combat_reason = "outer_interface"
    combat.combat_end()


def test_runner_preserves_bool_and_reused_instance_drops_old_result_before_rejection(monkeypatch):
    monkeypatch.setattr(CombatTask, "combat_once", _complete_with_identity)
    action = runner.CombatRunner()
    context = FakeContext(node_data={"ordinary": {}, "invalid": {"attach": {"inverse_crown_profile": {"enabled": 1}}}})
    assert action.run(context, SimpleNamespace(node_name="ordinary")).success is True
    first = action.last_outcome
    assert first.callback_status == "COMPLETED"
    assert first.field_identity == "confirmed" and first.role_bindings
    assert first.game_outcome == "UNKNOWN"
    assert action.run(context, SimpleNamespace(node_name="invalid")).success is False
    assert action.last_outcome is not first
    assert action.last_outcome.exit_reason == "profile_invalid"
    assert action.last_outcome.callback_status == "REJECTED"
    assert action.last_outcome.role_bindings == ()
    assert action.last_outcome.field_identity == "unknown"


def test_runner_layout_preflight_uses_same_rejection_schema_without_detail_leak(monkeypatch, capsys):
    context = FakeContext(node_data={"combat": {"attach": {"inverse_crown_profile": {"enabled": True}}}})

    def reject(_):
        raise LayoutCalibrationRequired("PRIVATE_LAYOUT_SECRET")

    monkeypatch.setattr(runner, "require_inverse_crown_layout", reject)
    monkeypatch.setattr(runner, "CombatTask", lambda *a, **kw: pytest.fail("preflight must reject before task creation"))
    action = runner.CombatRunner()
    assert action.run(context, SimpleNamespace(node_name="combat")).success is False
    assert action.last_outcome.exit_reason == "layout_calibration_required"
    assert action.last_outcome.callback_status == "REJECTED"
    assert action.last_outcome.game_outcome == "UNKNOWN"
    assert "PRIVATE_" not in capsys.readouterr().out


@pytest.mark.parametrize("preflight", [False, True])
def test_runner_propagates_generic_exception_and_consumes_redacted_outcome(monkeypatch, capsys, preflight):
    action = runner.CombatRunner()
    context = FakeContext(node_data={"combat": {}})
    error = OSError("PRIVATE_EXCEPTION_SECRET")

    def fail(*_):
        raise error

    if preflight:
        monkeypatch.setattr(context, "get_node_data", fail)
    else:
        monkeypatch.setattr(CombatTask, "_wait_in_combat", fail)
    with pytest.raises(OSError) as raised:
        action.run(context, SimpleNamespace(node_name="combat"))
    assert raised.value is error
    assert action.last_outcome.callback_status == "ERROR"
    assert action.last_outcome.exit_reason == ("preflight_exception" if preflight else "exception")
    assert action.last_outcome.exception_type == "OSError"
    assert "PRIVATE_" not in capsys.readouterr().out


@pytest.mark.parametrize("audit", [False, True])
def test_outcomes_use_actual_normal_and_audit_sinks(tmp_path, audit):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("MAA_PUNISH_AUDIT_")
    }
    environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONUTF8="1")
    if audit:
        environment.update(MAA_PUNISH_AUDIT_LOG_MODE="stdout", MAA_PUNISH_AUDIT_SESSION_ID="a" * 32)
    script = '''
from types import SimpleNamespace
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.entry.runner import CombatRunner
from test_support.fakes import FakeContext
combat = CombatTask(FakeContext(), CombatCheck())
combat.out_of_combat_reason = "hp_zero"
combat.combat_end()
combat.combat_end()
context = FakeContext(node_data={"bad": {"attach": {"inverse_crown_profile": {"enabled": 1}}}})
action = CombatRunner()
assert action.run(context, SimpleNamespace(node_name="bad")).success is False
assert action.last_outcome.exit_reason == "profile_invalid"
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    if audit:
        envelopes = [json.loads(line) for line in result.stdout.splitlines()]
        assert all(item["event"] == "maa_punish_audit" for item in envelopes)
        assert [item["sequence"] for item in envelopes] == list(range(1, len(envelopes) + 1))
        messages = [json.loads(item["message"]) for item in envelopes if item["message"].startswith("{")]
        events = [item for item in messages if item.get("event") == "combat_result"]
        assert not (tmp_path / "debug").exists()
    else:
        events = [json.loads(line.split("战斗结果: ", 1)[1]) for line in result.stdout.splitlines() if "战斗结果: " in line]
        files = list((tmp_path / "debug").glob("custom_*.log"))
        assert len(files) == 1
        assert "profile_invalid" in files[0].read_text(encoding="utf-8")
        assert "maa_punish_audit" not in result.stdout
    assert len(events) == 2
    assert [event["exit_reason"] for event in events] == ["hp_zero", "profile_invalid"]
    assert all(event["event"] == "combat_result" and event["game_outcome"] == "UNKNOWN" for event in events)
    assert all(event["emission_exception_type"] is None for event in events)


@pytest.mark.parametrize("case", [
    "combat_exception", "cleanup_exception", "combat_and_cleanup_exception",
    "preflight_rejection", "preflight_exception", "combat_rejection",
    "combat_rejection_and_cleanup_exception", "emission_only",
])
@pytest.mark.parametrize("persistent_failure", [False, True])
def test_actual_audit_sink_failure_preserves_error_priority_and_last_outcome(tmp_path, case, persistent_failure):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("MAA_PUNISH_AUDIT_")
    }
    environment.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONUTF8="1",
        MAA_PUNISH_AUDIT_LOG_MODE="stdout", MAA_PUNISH_AUDIT_SESSION_ID="a" * 32,
    )
    script = '''
import json
import sys
from types import SimpleNamespace

persistent_failure = sys.argv[2] == "persistent"

class BrokenOnCombatResult:
    failures = 0

    def write(self, value):
        if "combat_result" in value or (persistent_failure and self.failures):
            self.failures += 1
            raise BrokenPipeError("PRIVATE_EMISSION_SECRET")
        return sys.__stdout__.write(value)

    def flush(self):
        return sys.__stdout__.flush()

broken_sink = BrokenOnCombatResult()
sys.stdout = broken_sink

from action.combat.config.inverse_crown_profile import InverseCrownUnavailable
from action.combat.core.session import CombatTask
from action.combat.entry.runner import CombatRunner
from test_support.fakes import FakeContext

case = sys.argv[1]
primary = ValueError("PRIVATE_COMBAT_SECRET")
cleanup = OSError("PRIVATE_CLEANUP_SECRET")
preflight = OSError("PRIVATE_PREFLIGHT_SECRET")
rejection = InverseCrownUnavailable("strategy_not_ready", actual="PRIVATE_IDENTITY_SECRET")
tasks = []
original_init = CombatTask.__init__

def close():
    raise cleanup

def init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    tasks.append(self)
    if case in {"cleanup_exception", "combat_and_cleanup_exception", "combat_rejection_and_cleanup_exception"}:
        self.input_scope = SimpleNamespace(close=close)

def wait(self):
    if case in {"combat_exception", "combat_and_cleanup_exception"}:
        raise primary
    if case in {"combat_rejection", "combat_rejection_and_cleanup_exception"}:
        raise rejection
    self.out_of_combat_reason = "enter_timeout"

CombatTask.__init__ = init
CombatTask._wait_in_combat = wait
context = FakeContext(node_data={"PRIVATE_ARGV_SECRET": {}})
if case == "preflight_rejection":
    context = FakeContext(node_data={"PRIVATE_ARGV_SECRET": {
        "attach": {"inverse_crown_profile": {"enabled": 1}},
    }})
elif case == "preflight_exception":
    def fail_node_data(_):
        raise preflight
    context.get_node_data = fail_node_data

action = CombatRunner()
observed = None
result = None
try:
    result = action.run(context, SimpleNamespace(node_name="PRIVATE_ARGV_SECRET"))
except BaseException as exc:
    observed = exc

outcome = action.last_outcome
assert outcome.emission_exception_type == "RuntimeError", outcome
assert outcome.game_outcome == "UNKNOWN"
assert broken_sink.failures == 1, broken_sink.failures
assert "PRIVATE_" not in json.dumps(outcome.to_event())
if case in {"combat_exception", "combat_and_cleanup_exception"}:
    assert observed is primary, type(observed)
    assert outcome.exception_type == "ValueError"
    assert outcome.callback_status == "ERROR"
elif case == "cleanup_exception":
    assert observed is cleanup, type(observed)
    assert outcome.exception_type == "OSError"
    assert outcome.callback_status == "ERROR"
elif case == "preflight_exception":
    assert observed is preflight, type(observed)
    assert outcome.exception_type == "OSError"
    assert outcome.callback_status == "ERROR"
    assert outcome.exit_reason == "preflight_exception"
elif case in {"preflight_rejection", "combat_rejection", "combat_rejection_and_cleanup_exception"}:
    assert observed is None, type(observed)
    assert result.success is False
    assert outcome.callback_status == "REJECTED"
    assert outcome.exception_type == (
        "InverseCrownProfileError" if case == "preflight_rejection"
        else "InverseCrownUnavailable"
    )
    assert outcome.exit_reason == (
        "profile_invalid" if case == "preflight_rejection" else "strategy_not_ready"
    )
else:
    assert isinstance(observed, RuntimeError), type(observed)
    assert outcome.callback_status == "ERROR"
    assert outcome.exception_type == "RuntimeError"
    assert observed.__cause__.__class__ is BrokenPipeError
if case in {"cleanup_exception", "combat_and_cleanup_exception", "combat_rejection_and_cleanup_exception"}:
    assert outcome.cleanup_exception_type == "OSError"
    assert outcome.cleanup_reason == "input_cleanup_exception"
else:
    assert outcome.cleanup_exception_type is None
if tasks:
    task, = tasks
    assert task.outcome is outcome
    assert task.team is None and task.roles == {} and task.frame is None
    # No retry through the failed sink, even when cleanup is called again.
    task.combat_end()
    assert broken_sink.failures == 1
else:
    assert case.startswith("preflight_")
sys.__stdout__.write(json.dumps({"case": case, "outcome": outcome.to_event()}) + "\\n")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, case, "persistent" if persistent_failure else "selective"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PRIVATE_" not in result.stdout + result.stderr
    diagnostic = json.loads(result.stdout.splitlines()[-1])
    assert diagnostic["case"] == case
    assert diagnostic["outcome"]["emission_exception_type"] == "RuntimeError"


@pytest.mark.parametrize("known_rejection", [False, True])
def test_preflight_logger_initialization_failure_keeps_primary_contract(monkeypatch, known_rejection):
    context = FakeContext(node_data={"bad": {"attach": {"inverse_crown_profile": {"enabled": 1}}}})
    primary = OSError("PRIVATE_PREFLIGHT_SECRET")

    def fail_node_data(_):
        raise primary

    def fail_logger(_):
        raise PermissionError("PRIVATE_LOGGER_SECRET")

    if not known_rejection:
        monkeypatch.setattr(context, "get_node_data", fail_node_data)
    monkeypatch.setattr(runner, "LoggerComponent", fail_logger)
    action = runner.CombatRunner()
    if known_rejection:
        assert action.run(context, SimpleNamespace(node_name="bad")).success is False
        assert action.last_outcome.callback_status == "REJECTED"
        assert action.last_outcome.exception_type == "InverseCrownProfileError"
    else:
        with pytest.raises(OSError) as raised:
            action.run(context, SimpleNamespace(node_name="bad"))
        assert raised.value is primary
        assert action.last_outcome.callback_status == "ERROR"
        assert action.last_outcome.exception_type == "OSError"
    assert action.last_outcome.emission_exception_type == "PermissionError"
    assert "PRIVATE_" not in json.dumps(action.last_outcome.to_event())
