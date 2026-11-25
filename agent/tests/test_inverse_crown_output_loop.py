"""逆冕输出循环：红环就绪 → 长按第1槽特殊球 → 长按普攻 → 大招分派。

基于 2026-11-10 手录输出循环（台账 2.301/2.302/2.303）：囚笼长按大招，
其余按红环就绪点按循环。回归按当前普通路径的品阶阈值（基础 8/16、SSS
系列 12/16）验证相变和输入类别，不把这些阈值写成普适的真机结论。
"""

from unittest.mock import Mock

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.config.inverse_crown_profile import InverseCrownProfile, InverseCrownUnavailable
from action.combat.roles import inverse_crown as module
from action.combat.roles.inverse_crown import InverseCrown
from test_support.fakes import FakeClock, FakeContext, make_hit, make_miss


@pytest.fixture
def loop(monkeypatch):
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
    counters = Mock(return_value=0)
    monkeypatch.setattr(role.action, "count_signal_balls", counters)
    monkeypatch.setattr(role.action, "lens_lock", Mock())
    monkeypatch.setattr(role.action, "attack", Mock())
    return context, combat, role, clock, counters


def test_idle_red_ring_ready_enters_burst_hold(loop):
    context, _, role, _, counters = loop
    counters.return_value = 13

    role.do_perform()

    assert role.phase == "burst_hold"
    assert role.action.lens_lock.called and role.action.attack.called
    assert "长按信号球" not in context.actions


def test_burst_hold_uses_first_slot_without_ordinary_ball_templates(loop):
    context, _, role, _, _ = loop
    role.phase = "burst_hold"
    assert role.action.template == {}
    context.run_action = Mock(wraps=context.run_action)

    role.do_perform()

    context.run_action.assert_called_once_with(
        "长按1号球",
        pipeline_override={
            "长按1号球": {"action": {"param": {"duration": module._BURST_HOLD_MS}}}
        },
    )
    assert context.actions == ["长按1号球"]
    assert role.phase == "burst_slash"


@pytest.mark.parametrize("result", [False, None])
def test_burst_hold_failure_does_not_advance_to_charged_attack(loop, result):
    context, _, role, _, _ = loop
    role.phase = "burst_hold"
    context.run_action = Mock(return_value=result)

    with pytest.raises(InverseCrownUnavailable, match="special_hold_failed"):
        role.do_perform()

    assert role.phase == "burst_hold"
    assert context.actions == []


def test_stop_during_burst_hold_does_not_advance(loop):
    context, _, role, _, _ = loop
    role.phase = "burst_hold"

    def stop_after_hold(*args, **kwargs):
        context.tasker.stopping = True
        return True

    context.run_action = Mock(side_effect=stop_after_hold)
    role.do_perform()

    assert role.phase == "burst_hold"
    context.run_action.assert_called_once()


@pytest.mark.parametrize("rank,count,subphase", [
    ("SS", 7, "charge"), ("SS", 8, "tap_cast"),
    ("SSS", 11, "charge"), ("SSS", 12, "tap_cast"), ("SSS", 16, "tap_cast"),
])
def test_special_ball_branch_holds_attack_before_observing_cast_readiness(loop, rank, count, subphase):
    _, _, role, _, counters = loop
    role.inverse_crown_profile = InverseCrownProfile(source="self", rank=rank)
    role.phase = "special_attack"
    role.action.long_press_attack = Mock(return_value=True)

    def observe_after_hold():
        role.action.long_press_attack.assert_called_once_with(module._CHARGED_SLASH_MS)
        return count

    counters.side_effect = observe_after_hold

    role.do_perform()

    role.action.long_press_attack.assert_called_once_with(module._CHARGED_SLASH_MS)
    counters.assert_called_once_with()
    role.action.attack.assert_not_called()
    assert role.phase == "skill" and role._skill_sub == subphase


@pytest.mark.parametrize("result", [False, None])
def test_special_attack_hold_failure_preserves_phase(loop, result):
    _, _, role, _, counters = loop
    role.phase = "special_attack"
    role.action.long_press_attack = Mock(return_value=result)

    with pytest.raises(InverseCrownUnavailable, match="special_attack_hold_failed"):
        role.do_perform()

    assert role.phase == "special_attack"
    counters.assert_not_called()


def test_stop_during_special_attack_hold_prevents_cast_observation(loop):
    context, _, role, _, counters = loop
    role.phase = "special_attack"

    def stop_after_hold(*args):
        context.tasker.stopping = True
        return True

    role.action.long_press_attack = Mock(side_effect=stop_after_hold)
    role.do_perform()

    assert role.phase == "special_attack"
    counters.assert_not_called()


def test_burst_slash_charged_attack_then_cage_long_press(loop):
    context, _, role, _, _ = loop
    context.node_data["选择角色程序"] = {"attach": {"cage": True}}
    role.phase = "burst_slash"
    long_attack = Mock()
    long_skill = Mock()
    role.action.long_press_attack = long_attack
    role.action.long_press_skill = long_skill

    role.do_perform()

    long_attack.assert_called_once_with(module._CHARGED_SLASH_MS)
    assert role.phase == "skill" and role._skill_sub == "cage"


def test_burst_slash_routes_ring_ready_to_tap_cast(loop):
    context, _, role, _, _ = loop
    role.phase = "burst_slash"
    role.action.long_press_attack = Mock()

    role.do_perform()

    assert role.phase == "skill" and role._skill_sub == "tap_cast"
    assert role._skill_cast_ticks == 0


def test_tap_cast_taps_until_counter_drops_below_ready(loop):
    context, _, role, _, counters = loop
    role.phase = "skill"
    role._skill_sub = "tap_cast"
    counters.side_effect = [5]

    role.do_perform()

    assert context.actions.count("逆冕特殊键") == 1
    assert role._skill_sub == "finish"


def test_tap_cast_caps_taps_at_budget(loop):
    context, _, role, _, counters = loop
    role.phase = "skill"
    role._skill_sub = "tap_cast"
    counters.return_value = 14

    for _ in range(module._TAP_CAST_MAX):
        role.do_perform()

    assert context.actions.count("逆冕特殊键") == module._TAP_CAST_MAX
    assert role._skill_sub == "finish"


def test_tap_cast_stop_prevents_trailing_tap(loop):
    context, _, role, _, counters = loop
    role.phase = "skill"
    role._skill_sub = "tap_cast"
    context.tasker.stopping = True

    role.do_perform()

    assert "逆冕特殊键" not in context.actions
    assert role._skill_sub == "tap_cast"


@pytest.mark.parametrize("rank,count", [("SS", 7), ("SSS", 11)])
def test_idle_below_ready_keeps_legacy_gates(loop, rank, count):
    context, _, role, _, counters = loop
    role.inverse_crown_profile = InverseCrownProfile(source="self", rank=rank)
    counters.return_value = count

    role.do_perform()

    assert role.phase == "farm"
    assert "长按信号球" not in context.actions and "逆冕特殊键" not in context.actions


def test_enter_skill_ring_ready_resets_cast_ticks(loop):
    _, _, role, _, _ = loop
    role._skill_cast_ticks = 9

    role._enter_skill(switch_after=True, ring_ready=True)

    assert role._skill_sub == "tap_cast" and role._skill_cast_ticks == 0


@pytest.mark.parametrize("state", [
    ("burst_hold", None), ("burst_slash", None), ("skill", "tap_cast"),
])
def test_new_states_keep_input_protection(loop, state):
    _, combat, role, _, _ = loop
    role.phase, role._skill_sub = state

    assert combat.is_observation_protected()
