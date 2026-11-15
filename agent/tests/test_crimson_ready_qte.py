"""Crimson must not turn an unavailable support QTE into a character swap."""

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.qte_release import click_any_release_qte
from action.combat.roles.crimson_weave import CrimsonWeave
from test_support.fakes import FakeContext, FakeJob, make_hit, make_miss


def fight(monkeypatch):
    context = FakeContext()
    combat = CombatTask(context, CombatCheck())
    combat.team = TeamSnapshot(R="CrimsonWeave", B="GeneralFight", Y="", current="R")
    role = CrimsonWeave(combat, "R", "CrimsonWeave")
    combat.roles = {"R": role}
    monkeypatch.setattr(role, "_cast_ult_if_ready", lambda: False)
    monkeypatch.setattr(role.action, "auxiliary_machine", lambda: None)
    monkeypatch.setattr(role.action, "_auto_dodge", lambda: False)
    return context, combat, role


@pytest.mark.parametrize("phase", ["small_ult", "great_ult"])
@pytest.mark.parametrize("ready", [False, True])
def test_ult_support_call_requires_ready_and_never_blind_swaps(monkeypatch, phase, ready):
    context, combat, role = fight(monkeypatch)
    role.phase = phase
    if ready:
        result = make_hit()
        result.best_result.box = (1100, 100, 120, 120)
        context.recognition_map["释放蓝色QTE"] = result

    role.do_perform()

    assert context.actions == []
    assert context.tasker.controller.clicks == ([(1160, 160)] if ready else [])
    assert role.phase == phase + "_wait"
    assert combat.team.current == "R"
    assert combat.frame is None


@pytest.mark.parametrize("phase", ["small_ult", "great_ult"])
@pytest.mark.parametrize("boundary", ["auxiliary", "warning", "capture", "recognition"])
def test_stop_before_support_input_preserves_role_phase(monkeypatch, phase, boundary):
    context, _, role = fight(monkeypatch)
    role.phase = phase

    def stop():
        context.tasker.stopping = True

    if boundary == "auxiliary":
        monkeypatch.setattr(role.action, "auxiliary_machine", stop)
    elif boundary == "warning":
        monkeypatch.setattr(role.action, "_auto_dodge", stop)
    elif boundary == "capture":
        def capture():
            stop()
            return FakeJob()
        monkeypatch.setattr(context.tasker.controller, "post_screencap", capture)
    else:
        def recognize(*_):
            stop()
            return make_hit()
        monkeypatch.setattr(context, "run_recognition", recognize)

    role.do_perform()

    assert context.actions == []
    assert context.tasker.controller.clicks == []
    assert role.phase == phase


def test_ready_helper_does_not_use_frame_cached_before_auxiliary(monkeypatch):
    context, combat, role = fight(monkeypatch)
    old = object()
    combat.frame = old
    seen = []

    def recognize(name, frame, pipeline_override=None):
        seen.append(frame)
        return make_miss()

    monkeypatch.setattr(context, "run_recognition", recognize)
    assert role.action.use_ready_qte() is False
    assert seen and all(frame is not old for frame in seen)
    assert combat.frame is None
    assert context.actions == context.tasker.controller.clicks == []


@pytest.mark.parametrize("stopped_before", [False, True])
def test_release_helper_stops_between_recognition_and_click(monkeypatch, stopped_before):
    context = FakeContext()
    calls = []
    context.tasker.stopping = stopped_before

    def recognize(name, frame):
        calls.append(name)
        context.tasker.stopping = True
        return make_hit()

    monkeypatch.setattr(context, "run_recognition", recognize)
    assert click_any_release_qte(context, object()) is None
    assert context.tasker.controller.clicks == []
    assert calls == ([] if stopped_before else ["释放红色QTE"])


def test_ready_helper_clicks_only_one_color_from_a_frame(monkeypatch):
    context, _, role = fight(monkeypatch)
    calls = []

    def recognize(name, frame):
        calls.append(name)
        return make_hit()

    monkeypatch.setattr(context, "run_recognition", recognize)
    assert role.action.use_ready_qte() is True
    assert len(context.tasker.controller.clicks) == 1
    assert calls == ["释放红色QTE"]


@pytest.mark.parametrize("boundary", ["capture", "recognition"])
def test_ready_failure_invalidates_frame_without_input(monkeypatch, boundary):
    context, combat, role = fight(monkeypatch)
    combat.frame = object()

    def fail(*args):
        raise RuntimeError("synthetic QTE failure")

    if boundary == "capture":
        monkeypatch.setattr(context.tasker.controller, "post_screencap", fail)
    else:
        monkeypatch.setattr(context, "run_recognition", fail)
    with pytest.raises(RuntimeError, match="synthetic QTE failure"):
        role.action.use_ready_qte()
    assert combat.frame is None
    assert context.actions == context.tasker.controller.clicks == []


def test_failed_click_is_not_reported_as_released(monkeypatch):
    context, _, role = fight(monkeypatch)
    context.recognition_map["释放红色QTE"] = make_hit()
    context.recognition_map["释放蓝色QTE"] = make_hit()
    clicks = []

    def failed_click(*args):
        clicks.append(args)
        return FakeJob(succeeded=False)

    monkeypatch.setattr(context.tasker.controller, "post_click", failed_click)
    assert role.action.use_ready_qte() is False
    assert len(clicks) == 1
