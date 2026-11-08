"""Each waiting attack refreshes the shared warning through real consumers."""

import numpy as np
import pytest

from action.combat.core import provider, switch
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.inverse_crown import InverseCrown
from test_support.fakes import FakeContext, FakeJob, make_hit, make_miss


class WaitingFight:
    """Use real capture, auto_dodge and inverse protection, with no device."""

    def __init__(self, monkeypatch, *, warning_at=(), stop_at=None,
                 protect_at=None, fail_at=None, action_result=True):
        self.context = FakeContext()
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot.solo("InverseCrown")
        self.role = InverseCrown(self.combat, "R", "InverseCrown")
        self.role.phase = "farm"
        self.combat.roles = {"R": self.role}
        self.warning_at = warning_at
        self.stop_at = stop_at
        self.protect_at = protect_at
        self.fail_at = fail_at
        self.action_result = action_result
        self.events = []
        self.counts = {}
        self.warning_frames = []
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
            self.role.phase = "special_press"
            self.events.append(("protected", number))
        if token == self.fail_at:
            raise RuntimeError(f"synthetic {kind} failure")
        return number

    def capture(self):
        number = self.boundary("capture")
        return FakeJob(image=np.full((2, 2, 3), number, dtype=np.uint8))

    def recognize(self, name, image, pipeline_override=None):
        if name != "自动闪避":
            return make_miss()
        self.warning_frames.append(image)
        number = self.boundary("warning")
        return make_hit() if number in self.warning_at else make_miss()

    def act(self, name, pipeline_override=None):
        self.context.actions.append(name)
        self.boundary("attack" if name == "攻击" else "dodge")
        return self.action_result

    def call(self, consumer="direct", *, attack_count=2, monkeypatch=None):
        if consumer == "direct":
            return switch.blind_attack_click(self.context, attack_count=attack_count,
                                             combat=self.combat)
        if consumer == "session":
            return self.combat._blind_attack_tick()

        def detect(*args, on_tick=None, **kwargs):
            assert on_tick is not None
            on_tick()
            return "逆冕", "InverseCrown"

        monkeypatch.setattr(provider, "detect_current_role", detect)
        return self.combat.combat_check.detect_team(self.context, self.combat)


@pytest.mark.parametrize("consumer", ["direct", "provider", "session"])
def test_warning_appearing_between_two_attacks_is_handled_by_each_consumer(monkeypatch, consumer):
    fight = WaitingFight(monkeypatch, warning_at=(2,))

    fight.call(consumer, monkeypatch=monkeypatch)

    assert fight.context.actions == ["攻击", "闪避", "攻击"]
    assert fight.counts["warning"] == 2
    assert fight.warning_frames[0] is not fight.warning_frames[1]
    assert fight.events.index(("attack", 1)) < fight.events.index(("warning", 2))
    assert fight.events.index(("dodge", 1)) < fight.events.index(("attack", 2))


@pytest.mark.parametrize("kind", ["capture", "warning", "dodge", "attack"])
@pytest.mark.parametrize("cancel", ["stop", "protect"])
def test_stop_or_new_inverse_protection_cancels_remaining_inputs(monkeypatch, kind, cancel):
    arguments = {"stop_at" if cancel == "stop" else "protect_at": (kind, 1)}
    fight = WaitingFight(monkeypatch, warning_at=(1, 2), **arguments)

    assert fight.call() is None

    assert fight.context.actions == {
        "capture": [], "warning": [], "dodge": ["闪避"], "attack": ["闪避", "攻击"]
    }[kind]
    marker = "stopped" if cancel == "stop" else "protected"
    index = next(i for i, event in enumerate(fight.events) if event[0] == marker)
    assert fight.events[index + 1:] == []


@pytest.mark.parametrize("phase,skill_sub", [
    ("special_press", ""), ("special_attack", ""),
    ("skill", "cast"), ("skill", "cage"), ("skill", "finish"),
])
def test_existing_inverse_protection_prevents_capture_detection_and_input(monkeypatch, phase, skill_sub):
    fight = WaitingFight(monkeypatch, warning_at=(1, 2))
    fight.role.phase = phase
    fight.role._skill_sub = skill_sub
    assert fight.combat.is_observation_protected()

    assert fight.call() is None

    assert fight.events == [] and fight.context.actions == []


@pytest.mark.parametrize("attack_count,expected", [(0, 1), (-2, 1), (1, 1), (2, 2), (3, 3)])
@pytest.mark.parametrize("action_result", [True, False])
def test_miss_and_native_false_preserve_at_least_one_attack_and_none_return(
    monkeypatch, attack_count, expected, action_result
):
    fight = WaitingFight(monkeypatch, action_result=action_result)

    assert fight.call(attack_count=attack_count) is None

    assert fight.context.actions == ["攻击"] * expected
    assert fight.counts["warning"] == expected


@pytest.mark.parametrize("kind", ["capture", "warning", "dodge", "attack"])
def test_failure_propagates_without_followup_input(monkeypatch, kind):
    fight = WaitingFight(monkeypatch, warning_at=(1,) if kind == "dodge" else (),
                         fail_at=(kind, 1))

    with pytest.raises(RuntimeError, match=f"synthetic {kind} failure"):
        fight.call()

    assert fight.context.actions == {"capture": [], "warning": [], "dodge": ["闪避"],
                                     "attack": ["攻击"]}[kind]


@pytest.mark.parametrize("consumer", ["direct", "session"])
def test_initial_stop_keeps_observation_pure(monkeypatch, consumer):
    fight = WaitingFight(monkeypatch)
    fight.context.tasker.stopping = True

    assert fight.call(consumer) is None

    assert fight.events == [] and fight.context.actions == []
