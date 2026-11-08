"""Effulgence's ordinary p2 probes keep a fresh shared warning boundary."""

import importlib

import numpy as np
import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.effulgence import Effulgence
from test_support.fakes import FakeClock, FakeContext, FakeJob, make_hit, make_miss


role_module = importlib.import_module("action.combat.roles.effulgence")


class BurstFight:
    """Run the real role, capture and warning helpers with scripted returns."""

    def __init__(self, monkeypatch, *, warning_at=(), stop_at=None,
                 protect_at=None, protected=False, fail_at=None,
                 action_result=True, core_at=None):
        self.context = FakeContext()
        self.clock = FakeClock(now=100.0)
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot.solo("Effulgence")
        self.role = Effulgence(self.combat, "R", "Effulgence")
        self.combat.roles = {"R": self.role}
        self.role.phase = "core_dodge"
        self.protected = protected
        self.warning_at = warning_at
        self.stop_at = stop_at
        self.protect_at = protect_at
        self.fail_at = fail_at
        self.action_result = action_result
        self.core_at = core_at
        self.events = []
        self.counts = {}
        self.warning_frames = []
        self.previous_frame = np.zeros((2, 2, 3), dtype=np.uint8)
        self.combat.frame = self.previous_frame
        monkeypatch.setattr(role_module.time, "monotonic", self.clock.monotonic)
        monkeypatch.setattr(self.role, "is_input_protected", lambda: self.protected)
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)

    def boundary(self, kind):
        number = self.counts.get(kind, 0) + 1
        self.counts[kind] = number
        token = (kind, number)
        self.events.append(token)
        if token == self.stop_at:
            self.context.tasker.stopping = True
            self.events.append(("stopped", number))
        if token == self.protect_at:
            self.protected = True
            self.events.append(("protected", number))
        if token == self.fail_at:
            raise RuntimeError(f"synthetic {kind} failure")
        return number

    def capture(self):
        number = self.boundary("capture")
        return FakeJob(image=np.full((2, 2, 3), number, dtype=np.uint8))

    def recognize(self, name, image, pipeline_override=None):
        if name == "自动闪避":
            self.warning_frames.append(image)
            number = self.boundary("warning")
            return make_hit() if number in self.warning_at else make_miss()
        if name == "检查p2核心条_烬航":
            number = self.boundary("core")
            return make_hit() if number == self.core_at else make_miss()
        if name == "检查阶段p2_烬航":
            self.boundary("p2")
            return make_hit()
        assert name == "技能_能量条"
        self.boundary("energy")
        return make_miss()

    def act(self, name, pipeline_override=None):
        self.context.actions.append(name)
        kind = "attack" if name == "攻击" else "dodge" if name == "闪避" else name
        self.boundary(kind)
        if name == "攻击":
            self.clock.now += 3.1  # Two ordinary inputs exhaust the existing 6s budget.
        return self.action_result

    def burst(self):
        return self.role._burst_then_ult_or_p1()


def test_warning_appearing_after_first_attack_is_handled_before_second(monkeypatch):
    fight = BurstFight(monkeypatch, warning_at=(2,))

    assert fight.burst() is None

    assert fight.context.actions == ["攻击", "闪避", "攻击"]
    assert fight.events.index(("attack", 1)) < fight.events.index(("warning", 2))
    assert fight.events.index(("warning", 2)) < fight.events.index(("dodge", 1))
    assert fight.events.index(("dodge", 1)) < fight.events.index(("attack", 2))
    assert len(fight.warning_frames) == 2
    assert all(image is not fight.previous_frame for image in fight.warning_frames)
    assert fight.warning_frames[0] is not fight.warning_frames[1]


@pytest.mark.parametrize("warning_at", [(), (1,), (1, 2)])
@pytest.mark.parametrize("action_result", [True, False])
def test_warning_miss_or_failed_dodge_keeps_original_probe_count_and_exit(
    monkeypatch, warning_at, action_result
):
    fight = BurstFight(monkeypatch, warning_at=warning_at, action_result=action_result)

    assert fight.burst() is None

    expected = []
    for number in (1, 2):
        expected += (["闪避"] if number in warning_at else []) + ["攻击"]
    assert fight.context.actions == expected
    assert fight.counts["warning"] == fight.counts["core"] == 2
    assert fight.counts["energy"] == 1
    assert fight.role.phase == "combat"
    assert fight.context.pipeline_overrides == []


@pytest.mark.parametrize("kind", ["capture", "warning", "dodge"])
@pytest.mark.parametrize("cancel", ["stop", "protect"])
def test_cancellation_during_shared_warning_prevents_ordinary_dispatch(monkeypatch, kind, cancel):
    arguments = {"stop_at" if cancel == "stop" else "protect_at": (kind, 1)}
    fight = BurstFight(monkeypatch, warning_at=(1,), **arguments)

    assert fight.burst() is None

    assert fight.context.actions == (["闪避"] if kind == "dodge" else [])
    assert "core" not in fight.counts and "energy" not in fight.counts
    assert fight.role.phase == ("combat" if cancel == "stop" else "core_dodge")


@pytest.mark.parametrize("cancel", ["stop", "protect"])
def test_entry_cancellation_does_not_capture_or_detect_or_input(monkeypatch, cancel):
    fight = BurstFight(monkeypatch, protected=cancel == "protect")
    fight.context.tasker.stopping = cancel == "stop"

    assert fight.burst() is None

    assert fight.events == [] and fight.context.actions == []


@pytest.mark.parametrize("kind", ["capture", "warning", "dodge", "attack"])
def test_observation_or_input_failure_propagates_without_followup(monkeypatch, kind):
    fight = BurstFight(monkeypatch, warning_at=(1,) if kind == "dodge" else (),
                       fail_at=(kind, 1))

    with pytest.raises(RuntimeError, match=f"synthetic {kind} failure"):
        fight.burst()

    assert fight.context.actions == {"capture": [], "warning": [], "dodge": ["闪避"],
                                     "attack": ["攻击"]}[kind]
    assert "core" not in fight.counts and "energy" not in fight.counts


def test_core_hit_keeps_original_700ms_hold_then_ultimate_branch(monkeypatch):
    fight = BurstFight(monkeypatch, core_at=1)
    tail = []
    monkeypatch.setattr(fight.role, "_try_enter_ult", lambda: tail.append("energy") or True)
    monkeypatch.setattr(fight.role, "_phase_ult", lambda: tail.append("ult"))

    assert fight.burst() is None

    assert fight.context.actions == ["攻击", "长按攻击"]
    assert fight.context.pipeline_overrides == [
        {"长按攻击": {"action": {"param": {"duration": 700}}}}
    ]
    assert fight.counts["warning"] == 1
    assert tail == ["energy", "ult"]


def test_core_dodge_entry_releases_then_uses_the_same_ordinary_warning_path(monkeypatch):
    fight = BurstFight(monkeypatch, warning_at=(2,))

    assert fight.role.do_perform() is None

    assert fight.context.actions == ["松开闪避", "攻击", "闪避", "攻击"]
    assert fight.counts["p2"] == 1
    assert fight.role.phase == "combat"
