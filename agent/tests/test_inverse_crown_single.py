"""Single-target runtime entry, continuous combo, and input cleanup regressions."""

from dataclasses import replace

import pytest

from action.combat.config.inverse_crown_profile import InverseCrownUnavailable
from action.combat.input_control import CombatInputError
from action.combat.roles import inverse_crown as module
from test_inverse_crown_stationary import observations, setup_role as setup_signal_role, YES
from test_inverse_crown_visual import appearance_frame
from test_support.fakes import TimedFakeJob, make_hit


def setup_role(monkeypatch):
    return setup_signal_role(monkeypatch, signal_consumer=False)


def test_single_scene_accepts_unknown_account_facts_but_multi_remains_unavailable(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    combat.ensure_strategy_ready()
    combat.ensure_strategy_ready(role)
    assert not context.actions and not controller.touches
    combat.inverse_crown_profile = replace(combat.inverse_crown_profile, scene="multi")
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        combat.ensure_strategy_ready()


def test_single_normal_role_entry_reaches_single_consumer(monkeypatch):
    role, combat, _, _, _ = setup_role(monkeypatch)
    calls = []
    monkeypatch.setattr(combat, "refresh_field_role", lambda _role: False)
    monkeypatch.setattr(role, "_perform_single", lambda: calls.append("single"))
    monkeypatch.setattr(role, "_perform_stationary", lambda: pytest.fail("multi consumer"))
    monkeypatch.setattr(role, "_phase_idle", lambda: pytest.fail("ordinary rotation"))
    role.perform()
    assert calls == ["single"]


def test_other_cage_team_keeps_its_ordinary_role_and_switch_behavior():
    from test_inverse_crown_profile import _combat

    combat = _combat()
    combat.inverse_crown_profile = replace(combat.inverse_crown_profile, scene="single")
    combat.team.current = "B"
    assert not combat.is_inverse_crown_active()
    assert not combat.is_observation_protected()
    assert not combat.is_switch_disabled()
    combat.ensure_strategy_ready(combat.get_current_role())
    combat.team.current = "R"
    assert combat.is_inverse_crown_active() and combat.is_switch_disabled()


def test_special_hold_immediately_chains_required_attack_and_stays_on_field(monkeypatch):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()
    observations(monkeypatch, role, [{}])
    role.do_perform()
    downs = [post for post in controller.posts if isinstance(post, tuple)]
    assert downs == [("down", 975, 510), ("down", 1105, 500)]
    first_up = controller.posts.index("up")
    assert controller.posts[first_up + 1] == ("down", 1105, 500)
    assert clock.now >= 7.0
    assert role.phase == "prepare" and not role._settlement_pending
    assert combat.input_scope.owned_contact is None
    assert not context.actions


def test_ready_ultimate_uses_hold_and_waits_for_resource_consumption(monkeypatch):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [
        dict(ultimate_ready=YES),
        dict(resource_appearance="unknown"),
        dict(resource_appearance="full"),
        dict(resource_appearance="empty"),
    ])
    role.do_perform()
    assert role.phase == "ultimate_finish" and clock.now >= 2
    for _ in range(2):
        role.do_perform()
        assert role.phase == "ultimate_finish"
    role.do_perform()
    assert role.phase == "prepare"
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 1000, 650)]
    assert combat.input_scope.owned_contact is None and not context.actions


def test_unfinished_settlement_never_starts_an_ultimate(monkeypatch):
    role, _, _, controller, _ = setup_role(monkeypatch)
    role._settlement_pending = True
    observations(monkeypatch, role, [dict(ultimate_ready=YES)])
    with pytest.raises(CombatInputError, match="unfinished_single_settlement"):
        role.do_perform()
    assert not controller.touches and role._settlement_pending


def test_public_single_rebuilds_after_two_observed_consumptions(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [
        {}, dict(ultimate_ready=YES), dict(resource_appearance="full"), dict(resource_appearance="empty"),
        {}, dict(ultimate_ready=YES), dict(resource_appearance="full"), dict(resource_appearance="empty"),
        {},
    ])
    expected_phases = ["prepare", "ultimate_finish", "ultimate_finish", "prepare"] * 2 + ["prepare"]
    for phase in expected_phases:
        role.do_perform()
        assert role.phase == phase and combat.input_scope.owned_contact is None
    attack, skill = ("down", 1105, 500), ("down", 1000, 650)
    assert [p for p in controller.posts if isinstance(p, tuple)] == [attack, skill, attack, skill, attack]
    assert controller.posts.count("up") == 5 and not context.actions


def test_no_ready_signal_replenishes_resource_instead_of_waiting_without_input(monkeypatch):
    role, _, context, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [{}])
    role.do_perform()
    assert ("down", 1105, 500) in controller.posts
    assert controller.posts[-1] == "up"
    assert role.phase == "prepare" and not context.actions


def test_prepare_does_not_repeat_slow_shared_observation_before_tap(monkeypatch):
    role, combat, _, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [{}])
    original = combat.observe_hold_state

    def slow_observe(image):
        clock.now += 0.6
        return original(image)

    monkeypatch.setattr(combat, "observe_hold_state", slow_observe)
    role.do_perform()
    assert ("down", 1105, 500) in controller.posts
    assert not combat.input_scope.blocked


@pytest.mark.parametrize("boundary", ["special_leaf", "special_target", "ultimate_target", "controller"])
def test_expired_prepare_never_posts_the_first_hold(monkeypatch, boundary):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    scope = combat.input_scope
    role._single_prepare()
    clock.now = role._stationary_deadline - 0.05
    observations(monkeypatch, role, [dict(ultimate_ready=YES) if boundary == "ultimate_target" else {}])
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()

    if boundary == "special_leaf":
        recognize = context.run_recognition

        def slow(name, *args, **kwargs):
            if name == module._SPECIAL_BALL_NODE:
                clock.now += 0.075
            return recognize(name, *args, **kwargs)

        monkeypatch.setattr(context, "run_recognition", slow)
    elif boundary == "controller":
        acquire = scope.controller_for_jobs

        def slow():
            if not scope.capture_in_flight:
                clock.now += 0.075
            return acquire()

        monkeypatch.setattr(scope, "controller_for_jobs", slow)
    else:
        resolve = context.get_node_data
        node = "技能" if boundary == "ultimate_target" else "长按1号球"

        def slow(name, *args, **kwargs):
            if name == node:
                clock.now += 0.075
            return resolve(name, *args, **kwargs)

        monkeypatch.setattr(context, "get_node_data", slow)

    with pytest.raises(CombatInputError, match="budget_exhausted|开始期限"):
        role.do_perform()
    assert not controller.touches and scope.owned_contact is None and not scope.release_pending
    assert role.phase == "recover" and scope.blocked and context.tasker.stop_requests == 1


@pytest.mark.parametrize("ultimate, resolution", [(False, 0.45), (True, 0.45)])
def test_single_hold_dwell_starts_after_down_ack(monkeypatch, ultimate, resolution):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()
    observations(monkeypatch, role, [dict(ultimate_ready=YES) if ultimate else {}])
    resolve, down, up = context.get_node_data, controller.post_touch_down, controller.post_touch_up
    node = "技能" if ultimate else "长按1号球"
    intervals = []
    ack_delay = 0.045

    def slow_resolve(name, *args, **kwargs):
        if name == node:
            clock.now += resolution
        return resolve(name, *args, **kwargs)

    def record_down(*args, **kwargs):
        down(*args, **kwargs)
        ack_at = clock.now + ack_delay
        intervals.append([ack_at])
        return TimedFakeJob(clock, finish_at=ack_at)

    def record_up(*args, **kwargs):
        intervals[-1].append(clock.now)
        return up(*args, **kwargs)

    monkeypatch.setattr(context, "get_node_data", slow_resolve)
    monkeypatch.setattr(controller, "post_touch_down", record_down)
    monkeypatch.setattr(controller, "post_touch_up", record_up)
    role.do_perform()
    expected = [2.0] if ultimate else [3.0, 4.0]
    assert len(intervals) == len(expected)
    for (ack, released), seconds in zip(intervals, expected):
        assert seconds <= released - ack <= seconds + 0.03
    assert combat.input_scope.owned_contact is None
    assert role.phase == ("ultimate_finish" if ultimate else "prepare")


def test_slow_resolution_never_posts_a_stale_special_hold(monkeypatch):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()
    observations(monkeypatch, role, [{}])
    resolve = context.get_node_data

    def slow_resolve(name, *args, **kwargs):
        if name == "长按1号球":
            clock.now += 3.1
        return resolve(name, *args, **kwargs)

    monkeypatch.setattr(context, "get_node_data", slow_resolve)
    with pytest.raises(CombatInputError, match="开始期限|budget"):
        role.do_perform()
    assert not controller.touches
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
    assert role.phase == "recover" and combat.input_scope.blocked


def test_stop_during_special_releases_without_starting_attack(monkeypatch):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()
    observations(monkeypatch, role, [{}])
    clock.on_sleep = lambda now: setattr(context.tasker, "stopping", now >= 0.1)
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 975, 510)]
    assert controller.posts[-1] == "up"
    assert combat.input_scope.owned_contact is None
    assert role.phase == "recover"


@pytest.mark.parametrize("ultimate, stop_at", [(False, 3.25), (True, 0.2)])
def test_external_stop_during_later_holds_releases_and_suppresses_followup(monkeypatch, ultimate, stop_at):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()
    observations(monkeypatch, role, [dict(ultimate_ready=YES) if ultimate else {}])
    clock.on_sleep = lambda now: setattr(context.tasker, "stopping", now >= stop_at)
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert [p for p in controller.posts if isinstance(p, tuple)] == (
        [("down", 1000, 650)] if ultimate else [("down", 975, 510), ("down", 1105, 500)]
    )
    assert controller.posts[-1] == "up" and controller.posts.count("up") == (1 if ultimate else 2)
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
    assert role.phase == "recover" and not context.actions


def test_failed_release_preserves_pending_settlement_and_blocks_next_cycle(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    context.recognition_map[module._SPECIAL_BALL_NODE] = make_hit()
    observations(monkeypatch, role, [{}])
    holds = []

    def hold(node, seconds, **_timing):
        holds.append(node)
        if node == "攻击":
            raise CombatInputError("release failed")

    monkeypatch.setattr(role, "_single_hold", hold)
    with pytest.raises(CombatInputError, match="release failed"):
        role.do_perform()
    assert holds == ["长按1号球", "攻击"]
    assert role._settlement_pending and role.phase == "recover"
    assert combat.input_scope.blocked


def test_unconfirmed_ultimate_consumption_has_a_finite_exit(monkeypatch):
    role, combat, _, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(ultimate_ready=YES)])
    role.do_perform()
    before = list(controller.posts)
    clock.now = role._stationary_deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        role.do_perform()
    assert controller.posts == before and combat.input_scope.blocked


def test_new_decision_frame_with_hidden_hud_waits_without_requiring_hp(monkeypatch):
    from action.combat.core import session

    role, combat, context, controller, _ = setup_role(monkeypatch)
    role.phase = "ultimate_finish"
    role._stationary_deadline = 15.0
    combat._last_in_combat_time = session.time.monotonic()
    context.recognition_map["战斗中"] = None
    context.recognition_map["检查血量"] = None
    role.do_perform()
    assert not combat.combat_ui_visible
    assert not combat.input_scope.blocked and not controller.touches
    assert role.phase == "ultimate_finish" and role._stationary_deadline == 15.0


def test_new_decision_frame_with_exit_overlay_finishes_without_requesting_stop(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    role.phase = "ultimate_finish"
    role._stationary_deadline = 15.0
    combat._in_combat = True
    context.recognition_map["检查血量"] = None
    monkeypatch.setattr(combat.combat_check, "match_exit_overlay", lambda *_: True)
    role.do_perform()
    assert not combat._in_combat and combat.out_of_combat_reason == "outer_interface"
    assert not combat.input_scope.blocked and not controller.touches
    assert not combat.input_scope.stop_unconfirmed


def test_prepare_hp_recognition_miss_waits_without_input_or_extending_budget(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    context.recognition_map["检查血量"] = None
    role.do_perform()
    assert not combat.input_scope.blocked and not controller.touches
    deadline = role._stationary_deadline
    role.do_perform()
    assert role._stationary_deadline == deadline and not controller.touches


@pytest.mark.parametrize("reason", ["hp_text_invalid", "hp_recognition_error", "hp_out_of_range"])
def test_single_still_rejects_invalid_hp_and_recognition_errors(monkeypatch, reason):
    from action.combat.core.provider import HpObservation

    role, combat, _, controller, _ = setup_role(monkeypatch)
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", lambda *_: HpObservation(None, None, reason))
    with pytest.raises(CombatInputError, match=f"hp_refused:{reason}"):
        role.do_perform()
    assert combat.input_scope.blocked and not controller.touches


def test_ultimate_cinematic_with_pause_icon_but_hidden_hp_waits_and_recovers(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(ultimate_ready=YES), dict(resource_appearance="empty")])
    role.do_perform()
    assert role.phase == "ultimate_finish"
    hp = context.recognition_map["检查血量"]
    context.recognition_map["检查血量"] = None
    deadline = role._stationary_deadline
    before = list(controller.touches)
    role.do_perform()
    assert combat.combat_ui_visible and not combat.input_scope.blocked
    assert role.phase == "ultimate_finish" and role._stationary_deadline == deadline
    assert controller.touches == before and combat.input_scope.owned_contact is None
    context.recognition_map["检查血量"] = hp
    role.do_perform()
    assert role.phase == "prepare" and controller.touches == before


def test_ultimate_hidden_hp_keeps_the_existing_consumption_deadline(monkeypatch):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(ultimate_ready=YES)])
    role.do_perform()
    context.recognition_map["检查血量"] = None
    before = list(controller.touches)
    clock.now = role._stationary_deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        role.do_perform()
    assert combat.input_scope.blocked and controller.touches == before


@pytest.mark.parametrize("reason,pending", [("stopped", False), ("capture_failed", False), ("stopped", True)])
def test_only_external_stop_with_released_input_is_recorded_as_stopped(monkeypatch, reason, pending):
    _, combat, context, _, _ = setup_role(monkeypatch)
    monkeypatch.setattr(combat, "_wait_in_combat", lambda: setattr(combat, "_in_combat", True))
    monkeypatch.setattr(combat, "load_team", lambda: True)
    monkeypatch.setattr(combat, "_check_combat_presence", lambda: "")

    def interrupted():
        context.tasker.stopping = True
        combat.input_scope.request_stop(reason)
        combat.input_scope.release_pending = pending
        raise CombatInputError(reason)

    monkeypatch.setattr(combat, "perform", interrupted)
    if reason == "stopped" and not pending:
        combat.combat_once()
        assert combat.outcome.exit_reason == "stopped"
        assert combat.outcome.callback_status == "COMPLETED"
        assert combat.outcome.exception_type is None
    else:
        with pytest.raises(CombatInputError, match=reason):
            combat.combat_once()
        assert combat.outcome.callback_status == "ERROR"
        assert combat.outcome.exception_type == "CombatInputError"
    assert combat.outcome.cleanup_reason == "input_stop_unconfirmed"


@pytest.mark.parametrize("appearance", ["warm_endpoint", "dark_endpoint", "interior_glow", "white_frame"])
def test_full_resource_endpoint_glow_does_not_accept_gaps_or_screen_flash(appearance):
    geometry = module._StationaryVisualGeometry(
        (1280, 720), (76, 62, 213, 3), (942, 594, 124, 126), (1004, 657),
    )
    frame = appearance_frame(geometry)
    if appearance == "warm_endpoint":
        frame[62:65, 284:289] = (220, 235, 255)
    elif appearance == "dark_endpoint":
        frame[62:65, 288] = (60, 60, 80)
    elif appearance == "interior_glow":
        frame[62:65, 175:180] = (220, 235, 255)
    else:
        frame[:] = 255
    expected = "full" if appearance == "warm_endpoint" else "unknown"
    assert module._stationary_resource_appearance(frame, geometry) == expected
