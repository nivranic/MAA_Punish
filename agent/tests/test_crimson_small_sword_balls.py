"""Small-sword farming must spend stocked balls before repeating basic attacks."""

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.crimson_weave import CrimsonWeave
from test_support.fakes import FakeContext, FakeJob


def fight(monkeypatch, *, mode="small", count=7):
    context = FakeContext()
    combat = CombatTask(context, CombatCheck())
    combat.team = TeamSnapshot(R="CrimsonWeave", B="", Y="", current="R")
    role = CrimsonWeave(combat, "R", "CrimsonWeave")
    role.phase = "small_dodge"
    events = []
    monkeypatch.setattr(role, "_probe_sword_mode", lambda: (mode, 0, 0))
    monkeypatch.setattr(role, "_tap_attack", lambda: None)
    monkeypatch.setattr(role, "_skill_energy_ready", lambda **_: False)
    monkeypatch.setattr(role.action, "count_signal_balls", lambda **_: count)
    monkeypatch.setattr(role.action, "_auto_dodge", lambda *a: False)

    def combo():
        events.append("dodge_combo")
        return False

    monkeypatch.setattr(role, "_dodge_then_attack", combo)
    return context, combat, role, events


@pytest.mark.parametrize("phase", ["small_dodge", "small_attack"])
@pytest.mark.parametrize("count", [3, 7, 13])
def test_stocked_blue_balls_leave_farming_once_per_tick(monkeypatch, phase, count):
    context, combat, role, events = fight(monkeypatch, count=count)
    role.phase = phase

    role.do_perform()

    assert context.actions == ["消球1"]
    assert events == []
    assert combat.frame is None


@pytest.mark.parametrize("count", [0, 1, 2])
def test_insufficient_balls_keep_the_existing_farming_combo(monkeypatch, count):
    context, _, role, events = fight(monkeypatch, count=count)
    role.do_perform()
    assert context.actions == []
    assert events == ["dodge_combo"]


@pytest.mark.parametrize("mode", [None, "great"])
def test_unconfirmed_small_sword_does_not_spend_balls(monkeypatch, mode):
    context, _, role, events = fight(monkeypatch, mode=mode)
    role._phase_small_dodge()
    assert context.actions == []
    assert events == ["dodge_combo"]


def test_next_tick_checks_ultimate_before_spending_more_balls(monkeypatch):
    context, _, role, events = fight(monkeypatch)
    role.do_perform()
    monkeypatch.setattr(role, "_skill_energy_ready", lambda **_: True)
    monkeypatch.setattr(role, "_phase_small_ult", lambda: events.append("ultimate"))

    role.do_perform()

    assert context.actions == ["消球1"]
    assert events == ["ultimate"]
    assert role.phase == "small_ult"


@pytest.mark.parametrize("boundary", ["capture", "count", "warning", "input"])
def test_stop_during_ball_handling_does_not_fall_through_to_combo(monkeypatch, boundary):
    context, _, role, events = fight(monkeypatch)

    def stop():
        context.tasker.stopping = True

    def capture():
        stop()
        return FakeJob()

    def count(**_):
        events.append("count")
        if boundary == "count":
            stop()
        return 7

    monkeypatch.setattr(role.action, "count_signal_balls", count)
    if boundary == "capture":
        monkeypatch.setattr(context.tasker.controller, "post_screencap", capture)
    elif boundary == "warning":
        monkeypatch.setattr(role.action, "_auto_dodge", stop)
    elif boundary == "input":
        def action(name, pipeline_override=None):
            context.actions.append(name)
            stop()
            return False
        monkeypatch.setattr(context, "run_action", action)

    role._phase_small_dodge()

    assert context.tasker.stopping
    assert "dodge_combo" not in events
    assert context.actions == (["消球1"] if boundary == "input" else [])
    if boundary == "capture":
        assert events == []


def test_failed_ball_input_yields_instead_of_starting_another_combo(monkeypatch):
    context, _, role, events = fight(monkeypatch)

    def action(name, pipeline_override=None):
        context.actions.append(name)
        return False

    monkeypatch.setattr(context, "run_action", action)
    role.do_perform()
    assert context.actions == ["消球1"]
    assert events == []
