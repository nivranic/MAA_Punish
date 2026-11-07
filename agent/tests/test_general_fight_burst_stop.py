"""Burst cancellation must reach the actual shared helpers and role caller."""

import importlib

import numpy as np
import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.general_fight import GeneralFight
from test_support.fakes import FakeClock, FakeContext, FakeJob, make_hit, make_miss


actions_module = importlib.import_module("action.combat.actions.CombatActions")


class ScriptedBurst:
    """Synthetic framework boundaries; production helpers and phases run unchanged."""

    def __init__(self, monkeypatch, *, phase="farm", stop_at=None,
                 warning_at=(), ready_at=(), gate_hit=True, protected=False,
                 fail_at=None):
        self.clock = FakeClock(now=100.0)
        self.context = FakeContext()
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot.solo("GeneralFight")
        self.role = GeneralFight(self.combat, "R", "GeneralFight")
        self.combat.roles = {"R": self.role}
        self.role.phase = phase
        self.role._farm_ticks = 7
        self.events = []
        self.counts = {}
        self.stop_at = stop_at
        self.stop_state = None
        self.warning_at = warning_at
        self.ready_at = ready_at
        self.gate_hit = gate_hit
        self.fail_at = fail_at
        monkeypatch.setattr(actions_module.time, "monotonic", self.clock.monotonic)
        monkeypatch.setattr(actions_module.time, "sleep", self.sleep)
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)
        monkeypatch.setattr(self.combat, "is_observation_protected", lambda: protected)

    def count(self, kind):
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return self.counts[kind]

    def stop(self, boundary, occurrence):
        if self.stop_at == (boundary, occurrence):
            self.context.tasker.stopping = True
            self.stop_state = (self.role.phase, self.role._farm_ticks)
            self.events.append(("stop", boundary, occurrence))

    def capture(self):
        number = self.count("capture")
        self.events.append(("capture", number))
        self.clock.now += 0.01
        self.stop("capture", number)
        return FakeJob(image=np.full((2, 2, 3), number, dtype=np.uint8))

    def recognize(self, name, image, pipeline_override=None):
        number = self.count(name)
        self.events.append(("recognition", name, number))
        self.clock.now += 0.01
        self.stop("recognition", (name, number))
        if name == "自动闪避":
            self.stop("warning", number)
            return make_hit() if number in self.warning_at else make_miss()
        if name == "战斗中":
            return make_hit() if self.gate_hit else make_miss()
        if name == "技能_能量条":
            return make_hit() if number in self.ready_at else make_miss()
        return make_miss()

    def act(self, name, pipeline_override=None):
        number = self.count(("action", name))
        self.events.append(("action_request", name, number))
        self.context.actions.append(name)
        self.clock.now += 0.01
        if self.fail_at == (name, number):
            raise RuntimeError("synthetic input failure")
        self.stop("action_return", (name, number))
        # The in-flight request started before Stop; its return is permitted.
        self.events.append(("action_return", name, number))
        return True

    def sleep(self, seconds):
        number = self.count(("sleep", seconds))
        self.events.append(("sleep", seconds, number))
        self.clock.sleep(seconds)
        self.stop("sleep", (seconds, number))

    def burst(self, kind, *, count=5, interval=25, target=2):
        if kind == "attack":
            return self.role.action.attack_burst(count=count, interval=interval)
        return self.role.action.ball_elimination_burst(
            target=target, count=count, interval=interval,
        )

    def assert_cancelled(self, *, role_state=False):
        stopped = next(i for i, event in enumerate(self.events) if event[0] == "stop")
        after_stop = self.events[stopped + 1:]
        assert not any(event[0] in ("capture", "recognition", "action_request")
                       for event in after_stop)
        if role_state:
            assert (self.role.phase, self.role._farm_ticks) == self.stop_state


@pytest.mark.parametrize("kind", ("attack", "ball"))
def test_preexisting_stop_does_not_capture_or_request_input(monkeypatch, kind):
    fight = ScriptedBurst(monkeypatch)
    fight.context.tasker.stopping = True
    fight.combat.frame = np.zeros((2, 2, 3), dtype=np.uint8)

    assert fight.burst(kind) is False
    assert fight.events == []
    assert fight.combat.frame is None


@pytest.mark.parametrize("boundary", (("capture", 1), ("recognition", ("战斗中", 1))))
def test_attack_combat_gate_stop_does_not_continue_observation(monkeypatch, boundary):
    fight = ScriptedBurst(monkeypatch, stop_at=boundary)
    fight.role.action.skip_combat_gate = False

    assert fight.burst("attack") is False
    fight.assert_cancelled()
    assert not fight.context.actions
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind", ("attack", "ball"))
@pytest.mark.parametrize("warning_hit", (False, True))
def test_warning_return_stop_does_not_start_burst(monkeypatch, kind, warning_hit):
    fight = ScriptedBurst(monkeypatch, stop_at=("warning", 1),
                          warning_at=(1,) if warning_hit else ())

    assert fight.burst(kind) is False
    fight.assert_cancelled()
    assert not fight.context.actions
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind,node", (("attack", "攻击"), ("ball", "消球2")))
@pytest.mark.parametrize("number", (1, 3, 5))
def test_native_return_stop_cancels_remaining_inputs(monkeypatch, kind, node, number):
    fight = ScriptedBurst(monkeypatch, stop_at=("action_return", (node, number)))

    assert fight.burst(kind) is False
    fight.assert_cancelled()
    assert fight.context.actions == [node] * number
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind,node", (("attack", "攻击"), ("ball", "消球2")))
@pytest.mark.parametrize("number", (1, 3, 5))
def test_interval_stop_cancels_even_after_the_last_input(monkeypatch, kind, node, number):
    fight = ScriptedBurst(monkeypatch, stop_at=("sleep", (0.025, number)))

    assert fight.burst(kind) is False
    fight.assert_cancelled()
    assert fight.context.actions == [node] * number
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind,node", (("attack", "攻击"), ("ball", "消球2")))
def test_input_failure_propagates_and_invalidates_frame(monkeypatch, kind, node):
    fight = ScriptedBurst(monkeypatch, fail_at=(node, 3))

    with pytest.raises(RuntimeError, match="synthetic input failure"):
        fight.burst(kind)

    assert fight.context.actions == [node] * 3
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind,node", (("attack", "攻击"), ("ball", "消球2")))
@pytest.mark.parametrize("mode", ("miss", "hit", "protected"))
def test_normal_burst_keeps_one_warning_and_exact_input_timing(monkeypatch, kind, node, mode):
    fight = ScriptedBurst(monkeypatch, warning_at=(1,) if mode == "hit" else (),
                          protected=mode == "protected")

    assert fight.burst(kind) is True

    assert fight.context.actions == (["闪避"] if mode == "hit" else []) + [node] * 5
    assert fight.counts.get("自动闪避", 0) == (0 if mode == "protected" else 1)
    assert [event[1] for event in fight.events if event[0] == "sleep"] == [0.025] * 5
    assert fight.combat.frame is None


@pytest.mark.parametrize("target,count,node,expected", (
    (0, 5, "消球2", 5), (-3, 2, "消球3", 2), (8, 0, "消球8", 1),
    (1, -2, "消球1", 1), (9, 5, None, 0), (-9, 5, None, 0),
))
def test_ball_target_and_count_contract_is_unchanged(monkeypatch, target, count, node, expected):
    fight = ScriptedBurst(monkeypatch)

    assert fight.burst("ball", target=target, count=count, interval=0) is bool(expected)
    assert fight.context.actions == [node] * expected
    assert fight.counts.get("自动闪避", 0) == bool(expected)


@pytest.mark.parametrize("count", (0, -2))
def test_attack_count_still_runs_at_least_once(monkeypatch, count):
    fight = ScriptedBurst(monkeypatch)

    assert fight.burst("attack", count=count, interval=0) is True
    assert fight.context.actions == ["攻击"]
    assert fight.counts["自动闪避"] == 1


@pytest.mark.parametrize("gate_hit", (False, True))
def test_combat_gate_miss_remains_distinct_from_warning_miss(monkeypatch, gate_hit):
    fight = ScriptedBurst(monkeypatch, gate_hit=gate_hit)
    fight.role.action.skip_combat_gate = False

    assert fight.burst("attack") is gate_hit
    assert fight.context.actions == ["攻击"] * (5 if gate_hit else 0)
    assert fight.counts.get("自动闪避", 0) == int(gate_hit)


@pytest.mark.parametrize("phase", ("idle", "farm", "ult"))
@pytest.mark.parametrize("boundary", ("warning", "attack", "ball", "attack_wait", "ball_wait"))
def test_role_propagates_burst_cancellation_without_phase_or_counter_advance(monkeypatch, phase, boundary):
    stop_at = {
        "warning": ("warning", 2 if phase == "idle" else 1),
        "attack": ("action_return", ("攻击", 3)),
        "ball": ("action_return", ("消球2", 3)),
        "attack_wait": ("sleep", (0.025, 3)),
        "ball_wait": ("sleep", (0.025, 8)),
    }[boundary]
    fight = ScriptedBurst(monkeypatch, phase=phase, stop_at=stop_at,
                          ready_at=(1,) if phase == "ult" else ())

    fight.role.do_perform()

    fight.assert_cancelled(role_state=True)
    if boundary in ("warning", "attack", "attack_wait"):
        assert "消球2" not in fight.context.actions
    assert fight.context.actions.count("技能") == (1 if phase == "ult" else 0)


@pytest.mark.parametrize("phase,node", (("idle", "锁定视角"), ("ult", "技能")))
def test_role_checks_stop_after_existing_intro_action_returns(monkeypatch, phase, node):
    fight = ScriptedBurst(monkeypatch, phase=phase,
                          stop_at=("action_return", (node, 1)), ready_at=(1,))

    fight.role.do_perform()

    fight.assert_cancelled(role_state=True)


@pytest.mark.parametrize("phase", ("farm", "ult"))
@pytest.mark.parametrize("boundary", ("capture", "recognition"))
@pytest.mark.parametrize("ready", (False, True))
def test_role_energy_observation_stop_does_not_start_or_complete_burst(monkeypatch, phase, boundary, ready):
    stop_at = ("capture", 1) if boundary == "capture" else ("recognition", ("技能_能量条", 1))
    fight = ScriptedBurst(monkeypatch, phase=phase, stop_at=stop_at,
                          ready_at=(1,) if ready else ())

    fight.role.do_perform()

    fight.assert_cancelled(role_state=True)
    assert fight.context.actions == (["技能"] if phase == "ult" else [])


def test_ultimate_poll_stop_does_not_switch_or_cast_again(monkeypatch):
    fight = ScriptedBurst(monkeypatch, phase="ult", ready_at=(1,),
                          stop_at=("sleep", (0.05, 1)))

    fight.role.do_perform()

    fight.assert_cancelled(role_state=True)
    assert fight.context.actions == ["技能"] + ["攻击"] * 5 + ["消球2"] * 5


@pytest.mark.parametrize("phase", ("idle", "farm", "ult"))
def test_normal_role_keeps_burst_order_and_existing_phase_progress(monkeypatch, phase):
    fight = ScriptedBurst(monkeypatch, phase=phase, ready_at=(1,) if phase == "ult" else ())

    fight.role.do_perform()

    expected = ["攻击"] * 5 + ["消球2"] * 5
    if phase == "idle":
        expected = ["锁定视角"] + expected
    elif phase == "ult":
        expected = ["技能"] + expected + ["技能"]
    assert fight.context.actions == expected
    assert fight.counts["自动闪避"] == (3 if phase == "idle" else 2)
    assert fight.role.phase == ("switch" if phase == "ult" else "farm")
    assert fight.role._farm_ticks == {"idle": 0, "farm": 8, "ult": 7}[phase]
