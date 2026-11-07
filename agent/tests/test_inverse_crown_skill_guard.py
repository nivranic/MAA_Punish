"""Ordinary skill charging must not claim an unconfirmed ultimate window."""

from unittest.mock import Mock

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
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
    assert context.actions == ["技能"]
    assert role._skill_sub == "finish" and combat.is_observation_protected()

    role.do_perform()
    assert context.actions == ["技能", "辅助机", "qte1", "qte2"]
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
