"""Ordinary skill charging must not claim an unconfirmed ultimate window."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.config.inverse_crown_profile import InverseCrownUnavailable
from action.combat.roles import inverse_crown as module
from action.combat.roles.inverse_crown import InverseCrown
from test_support.fakes import FakeClock, FakeContext, make_hit, make_miss


@pytest.fixture
def inverse_skill(monkeypatch):
    clock = FakeClock(now=100.0)
    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    context = FakeContext(recognition_map={
        "自动闪避": make_hit(),
        "检查逆冕能量即将满": make_miss(),
        "检查逆冕能量空": make_miss(),
        "检查逆冕特殊球": make_miss(),
    })
    combat = CombatTask(context, CombatCheck())
    combat.team = TeamSnapshot(R="InverseCrown", B="", Y="", current="R")
    role = InverseCrown(combat, "R", "InverseCrown")
    combat.roles = {"R": role}
    return context, combat, role, clock


def test_unready_charge_attack_uses_shared_warning_reaction(inverse_skill):
    context, combat, role, _ = inverse_skill
    role._enter_skill(switch_after=True)

    role.do_perform()

    assert role.phase == "skill" and role._skill_sub == "charge"
    assert not combat.is_observation_protected()
    assert context.actions == ["闪避", "攻击"]
    assert not role._post_ult_qte_used


@pytest.mark.parametrize("switch_after", [False, True])
def test_unready_charge_timeout_returns_to_normal_without_finishing_skill(inverse_skill, monkeypatch, switch_after):
    context, combat, role, clock = inverse_skill
    switch = Mock()
    monkeypatch.setattr(combat, "request_role_switch", switch)
    role._enter_skill(switch_after=switch_after)
    clock.now = role._skill_charge_deadline

    role.do_perform()

    assert role.phase == "idle" and role._skill_sub == ""
    assert role._skill_charge_deadline == 0.0 and role._skill_cast_ticks == 0
    assert not role._skill_switch_after and not role._post_ult_qte_used
    assert not combat.is_observation_protected()
    assert context.actions == []

    # The existing idle dispatcher resumes ordinary actions and warning checks.
    role.do_perform()
    assert role.phase == "farm"
    assert "闪避" in context.actions and "攻击" in context.actions
    assert not set(context.actions) & {"技能", "长按技能", "辅助机", "qte1", "qte2"}
    switch.assert_not_called()


@pytest.mark.parametrize("at_deadline", [False, True])
def test_confirmed_ready_enters_protected_cast_even_at_deadline(inverse_skill, at_deadline):
    context, combat, role, clock = inverse_skill
    role._enter_skill(switch_after=True)
    context.recognition_map["检查逆冕能量即将满"] = make_hit()
    if at_deadline:
        clock.now = role._skill_charge_deadline

    role.do_perform()

    assert role.phase == "skill" and role._skill_sub == "cast"
    assert combat.is_observation_protected()
    assert context.actions == []

    context.recognition_map["检查逆冕能量空"] = make_hit()
    role.do_perform()
    assert context.actions == ["长按技能"]
    assert role._skill_sub == "finish" and combat.is_observation_protected()

    role.do_perform()
    assert context.actions == ["长按技能", "辅助机", "qte1", "qte2"]
    assert role.phase == "switch" and role._post_ult_qte_used


def test_cage_skill_preserves_existing_protected_release(inverse_skill):
    context, combat, role, _ = inverse_skill
    context.node_data["选择角色程序"] = {"attach": {"cage": True}}
    role._enter_skill(switch_after=True)

    assert role._skill_sub == "cage" and combat.is_observation_protected()
    role.do_perform()

    assert context.actions == ["长按技能", "辅助机", "qte1", "qte2"]
    assert role.phase == "switch" and role._post_ult_qte_used


def test_stop_before_charge_tick_prevents_all_actions(inverse_skill):
    context, _, role, _ = inverse_skill
    role._enter_skill(switch_after=True)
    context.tasker.stopping = True
    context.run_recognition = Mock(side_effect=AssertionError("no recognition after stop"))

    role.do_perform()

    assert context.actions == []
    assert role.phase == "skill" and role._skill_sub == "charge"


@pytest.mark.parametrize("stop_at", ["fresh_capture", "warning_detection"])
def test_stop_during_charge_warning_prevents_trailing_attack(inverse_skill, monkeypatch, stop_at):
    context, _, role, _ = inverse_skill
    role._enter_skill(switch_after=True)
    events = []
    original_capture = role.action._get_image
    original_recognition = context.run_recognition

    def capture(*, fresh=False):
        image = original_capture(fresh=fresh)
        if fresh:
            events.append("fresh_capture")
            if stop_at == "fresh_capture":
                context.tasker.stopping = True
        return image

    def recognize(name, image, *args, **kwargs):
        result = original_recognition(name, image, *args, **kwargs)
        if name == "自动闪避":
            events.append("warning_detection")
            if stop_at == "warning_detection":
                context.tasker.stopping = True
        return result

    monkeypatch.setattr(role.action, "_get_image", capture)
    monkeypatch.setattr(context, "run_recognition", recognize)

    role.do_perform()

    expected_events = ["fresh_capture"]
    if stop_at == "warning_detection":
        expected_events.append("warning_detection")
    assert events == expected_events and context.tasker.stopping
    assert context.actions == []
    assert role.phase == "skill" and role._skill_sub == "charge"


def test_cast_holds_once_then_only_observes_until_empty(inverse_skill, monkeypatch):
    context, combat, role, _ = inverse_skill
    switch = Mock()
    monkeypatch.setattr(combat, "request_role_switch", switch)
    role.phase, role._skill_sub = "skill", "cast"
    role._skill_switch_after = True

    for expected_ticks in range(1, 4):
        role.do_perform()
        assert role._skill_cast_ticks == expected_ticks
        assert role._skill_sub == "cast" and combat.is_observation_protected()
        assert context.actions == ["长按技能"]

    context.recognition_map["检查逆冕能量空"] = make_hit()
    role.do_perform()

    assert role._skill_cast_ticks == 4 and role._skill_sub == "finish"
    assert context.actions == ["长按技能"]
    assert context.node_data["长按技能"]["action"]["param"]["duration"] == 2000
    switch.assert_not_called()


def test_twentieth_empty_miss_rejects_without_finishing_skill(inverse_skill, monkeypatch):
    context, combat, role, _ = inverse_skill
    switch = Mock()
    monkeypatch.setattr(combat, "request_role_switch", switch)
    role.phase, role._skill_sub = "skill", "cast"
    role._skill_switch_after = True

    for _ in range(19):
        role.do_perform()
    with pytest.raises(InverseCrownUnavailable) as exc:
        role.do_perform()

    assert exc.value.reason == "skill_empty_not_confirmed"
    assert role._skill_cast_ticks == 20 and role._skill_sub == "cast"
    assert role.phase == "skill" and combat.is_observation_protected()
    assert not role._post_ult_qte_used
    assert context.actions == ["长按技能"]
    switch.assert_not_called()


def test_twentieth_empty_hit_takes_priority_over_observation_limit(inverse_skill):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    for _ in range(19):
        role.do_perform()
    context.recognition_map["检查逆冕能量空"] = make_hit()

    role.do_perform()

    assert role._skill_cast_ticks == 20 and role._skill_sub == "finish"
    assert context.actions == ["长按技能"]


@pytest.mark.parametrize("result", [
    False, None, SimpleNamespace(success=False), SimpleNamespace(success=1), object(), 1,
])
def test_failed_or_unknown_hold_result_rejects_before_empty_observation(
    inverse_skill, monkeypatch, result,
):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    run_action = Mock(return_value=result)
    monkeypatch.setattr(context, "run_action", run_action)
    monkeypatch.setattr(context, "run_recognition", Mock(side_effect=AssertionError("no observation after failed hold")))

    with pytest.raises(InverseCrownUnavailable) as exc:
        role.do_perform()

    assert exc.value.reason == "skill_hold_failed"
    run_action.assert_called_once_with("长按技能")
    assert role._skill_sub == "cast" and not role._post_ult_qte_used


def test_native_success_detail_allows_empty_confirmation(inverse_skill, monkeypatch):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    context.recognition_map["检查逆冕能量空"] = make_hit()
    run_action = Mock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(context, "run_action", run_action)

    role.do_perform()

    run_action.assert_called_once_with("长按技能")
    assert role._skill_sub == "finish"


def test_first_empty_observation_uses_frame_captured_after_hold(inverse_skill, monkeypatch):
    context, combat, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    combat.frame = context.tasker.controller.image.copy()
    current_frame = context.tasker.controller.image
    observed_frames = []

    def recognize(entry, image, *args, **kwargs):
        observed_frames.append(image)
        return make_hit()

    monkeypatch.setattr(context, "run_recognition", recognize)

    role.do_perform()

    assert len(observed_frames) == 1 and observed_frames[0] is current_frame
    assert combat.frame is current_frame and role._skill_sub == "finish"
    assert context.actions == ["长按技能"]


def test_stop_before_cast_prevents_hold_and_recognition(inverse_skill, monkeypatch):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    context.tasker.stopping = True
    monkeypatch.setattr(context, "run_recognition", Mock(side_effect=AssertionError("no recognition after stop")))

    role.do_perform()

    assert context.actions == [] and role._skill_cast_ticks == 0
    assert role._skill_sub == "cast"


def test_stop_after_hold_prevents_empty_observation(inverse_skill, monkeypatch):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"

    def hold(entry):
        context.actions.append(entry)
        context.tasker.stopping = True
        return True

    monkeypatch.setattr(context, "run_action", hold)
    monkeypatch.setattr(context, "run_recognition", Mock(side_effect=AssertionError("no recognition after stop")))

    role.do_perform()
    role.do_perform()

    assert context.actions == ["长按技能"] and role._skill_sub == "cast"
    assert not role._post_ult_qte_used


@pytest.mark.parametrize("previous_ticks", [0, 1])
def test_stop_during_empty_capture_prevents_recognition(
    inverse_skill, monkeypatch, previous_ticks,
):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    role._skill_cast_ticks = previous_ticks
    original_capture = context.tasker.controller.post_screencap

    def capture():
        job = original_capture()
        original_wait = job.wait

        def wait():
            result = original_wait()
            context.tasker.stopping = True
            return result

        job.wait = wait
        return job

    monkeypatch.setattr(context.tasker.controller, "post_screencap", capture)
    monkeypatch.setattr(context, "run_recognition", Mock(side_effect=AssertionError("no recognition after capture stop")))

    role.do_perform()
    role.do_perform()

    assert context.tasker.stopping and role._skill_sub == "cast"
    assert context.actions == (["长按技能"] if previous_ticks == 0 else [])
    assert role._skill_cast_ticks == previous_ticks + 1
    assert not role._post_ult_qte_used


def test_later_empty_observation_consumes_current_runner_frame(inverse_skill, monkeypatch):
    context, combat, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    original_capture = context.tasker.controller.post_screencap
    capture = Mock(side_effect=original_capture)
    monkeypatch.setattr(context.tasker.controller, "post_screencap", capture)
    observed_frames = []

    def recognize(entry, image, *args, **kwargs):
        observed_frames.append(image)
        return make_miss()

    monkeypatch.setattr(context, "run_recognition", recognize)
    first_frame = context.tasker.controller.image
    role.do_perform()
    next_frame = first_frame.copy()
    context.tasker.controller.image = next_frame
    # CombatTask captures once at the start of each ordinary runner tick.
    combat._capture_frame()

    role.do_perform()

    assert len(observed_frames) == 2
    assert observed_frames[0] is first_frame and observed_frames[1] is next_frame
    assert capture.call_count == 2  # One post-hold capture, one new runner tick.
    assert context.actions == ["长按技能"] and role._skill_cast_ticks == 2


def test_stop_during_empty_confirmation_does_not_finish(inverse_skill, monkeypatch):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    recognitions = []

    def recognize(entry, *args, **kwargs):
        recognitions.append(entry)
        context.tasker.stopping = True
        return make_hit()

    monkeypatch.setattr(context, "run_recognition", recognize)

    role.do_perform()
    role.do_perform()

    assert recognitions == ["检查逆冕能量空"]
    assert context.actions == ["长按技能"] and role._skill_sub == "cast"
    assert not role._post_ult_qte_used


def test_hold_exception_propagates_without_empty_observation(inverse_skill, monkeypatch):
    context, _, role, _ = inverse_skill
    role.phase, role._skill_sub = "skill", "cast"
    run_action = Mock(side_effect=RuntimeError("native failure"))
    monkeypatch.setattr(context, "run_action", run_action)
    monkeypatch.setattr(context, "run_recognition", Mock(side_effect=AssertionError("no observation after exception")))

    with pytest.raises(RuntimeError, match="native failure"):
        role.do_perform()

    run_action.assert_called_once_with("长按技能")
    assert role._skill_sub == "cast" and not role._post_ult_qte_used
