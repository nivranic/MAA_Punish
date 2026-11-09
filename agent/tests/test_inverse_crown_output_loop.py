"""逆冕输出循环：红环就绪 → 长按红球全消 → 长按普攻 → 大招分派。

2026-11-10 依用户手录 60Hz 视频实证的输出循环（台账 2.301/2.302/2.303）：
红环 ≈12/16 即爆发；囚笼长按大招、其余红环即点循环。本文件锁定普通
（非 stationary）路径的相变与输入类别契约；时长/阈值是临时先验。
"""

from unittest.mock import Mock

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
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


def test_burst_hold_long_presses_detected_red_slot(loop):
    context, _, role, _, _ = loop
    role.phase = "burst_hold"
    find_slot = Mock(return_value=3)
    hold_ball = Mock()
    role.action.find_signal_ball_slot = find_slot
    role.action.long_press_ball = hold_ball

    role.do_perform()

    find_slot.assert_called_once_with("red")
    hold_ball.assert_called_once_with(3, module._BURST_HOLD_MS)
    assert role.phase == "burst_slash"


def test_burst_hold_falls_back_to_targeted_elimination(loop):
    context, _, role, _, _ = loop
    role.phase = "burst_hold"
    role.action.find_signal_ball_slot = Mock(return_value=0)
    fallback = Mock()
    role.action.ball_elimination_target = fallback

    role.do_perform()

    fallback.assert_called_once_with()
    assert role.phase == "burst_slash"
    assert "长按信号球" not in context.actions


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


def test_idle_below_ready_keeps_legacy_gates(loop):
    context, _, role, _, counters = loop
    counters.return_value = 8

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
