"""Stationary consumer behavior; synthetic signals are not game acceptance."""

from dataclasses import replace

import pytest

from action.combat.config.inverse_crown_profile import InverseCrownProfile, InverseCrownUnavailable
from action.combat.core.provider import CombatCheck, HpObservation, parse_hp_fraction
from action.combat.core.session import CombatTask
from action.combat.input_control import CombatInputError, CombatInputScope
from action.combat.roles import inverse_crown as module
from action.combat.roles.inverse_crown import InverseCrown, _Signal, _StationarySignals
from test_support.fakes import (
    FakeBestResult, FakeClock, FakeContext, FakeController, FakeJob,
    FakeRecognitionResult, make_hit,
)

YES, NO, UNKNOWN = _Signal.YES, _Signal.NO, _Signal.UNKNOWN


class RecordingController(FakeController):
    def __init__(self):
        super().__init__()
        self.posts = []
        self.up_success = True

    def post_screencap(self):
        self.posts.append("capture")
        return super().post_screencap()

    def post_touch_down(self, x, y, contact=0, pressure=1):
        self.posts.append(("down", x, y))
        return super().post_touch_down(x, y, contact, pressure)

    def post_touch_up(self, contact=0):
        self.posts.append("up")
        super().post_touch_up(contact)
        return FakeJob(succeeded=self.up_success)


def setup_role(monkeypatch, *, enabled=True, scene="single"):
    clock = FakeClock()
    context = FakeContext(recognition_map={
        "战斗中": make_hit(),
        "检查血量": FakeRecognitionResult(hit=True, best_result=FakeBestResult(
            box=(19, 15, 141, 29), text="100/100")),
    })
    controller = RecordingController()
    context.tasker.controller = controller
    for node, point in (("长按1号球", [975, 510]), ("攻击", [1105, 500]), ("技能", [1000, 650])):
        context.node_data[node] = {"action": {"type": "Click", "param": {"target": point}}}
    combat = CombatTask(context, CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=enabled, scene=scene))
    if enabled:
        combat.input_scope = CombatInputScope(context, combat, generation=23,
            monotonic=clock.monotonic, sleep=clock.sleep, capture_timeout=0.1,
            input_timeout=0.1, release_timeout=0.1, poll_interval=0.01)
        combat.input_scope.capture_frame()
    role = InverseCrown(combat, "R", "InverseCrown")
    monkeypatch.setattr(module, "_STATIONARY_BUDGETS", {name: 0.08 for name in module._STATIONARY_BUDGETS})
    return role, combat, context, controller, clock


def observations(monkeypatch, role, values):
    remaining = iter(values)
    seen = []

    def read(image):
        assert image is role.combat.frame
        metadata = role.combat.input_scope.frame_metadata
        values = next(remaining, {})
        if callable(values):
            values = values()
        snapshot = _StationarySignals(metadata.generation, metadata.sequence, metadata.captured_at, **values)
        seen.append(snapshot)
        return snapshot

    monkeypatch.setattr(role, "_read_stationary_signals", read)
    return seen


def tick(role):
    role.combat.input_scope.capture_frame()
    role.do_perform()


def special_start(**updates):
    return dict(settlement_required=NO, special_activated=YES, special_color="blue", **updates)


@pytest.mark.parametrize("scene", ["single", "multi"])
def test_live_session_and_role_gates_remain_closed(monkeypatch, scene):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene=scene)
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        role.perform()
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        combat.combat_once()
    assert not controller.touches and not context.actions


def test_unknown_scene_never_enters_stationary_consumer(monkeypatch):
    role, _, context, controller, _ = setup_role(monkeypatch, scene="unknown")
    with pytest.raises(InverseCrownUnavailable, match="stationary_scene_required"):
        role.do_perform()
    assert not controller.touches and not context.actions


def test_unimplemented_detectors_keep_hits_unknown_without_new_controller(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    context.recognition_map.update({name: make_hit() for name in (
        "检查逆冕特殊球", "检查逆冕能量即将满", "检查逆冕能量空")})
    before = list(controller.posts)
    snapshot = role._read_stationary_signals(combat.frame)
    assert snapshot.special_activated is UNKNOWN
    assert snapshot.ultimate_ready is UNKNOWN
    assert snapshot.ultimate_charge_complete is UNKNOWN
    assert snapshot.ultimate_finished is UNKNOWN
    role.do_perform()
    assert role.phase == "prepare" and controller.posts == before
    assert not context.actions


@pytest.mark.parametrize("signal", [NO, UNKNOWN])
def test_inactive_or_unknown_special_does_not_down(monkeypatch, signal):
    role, _, _, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(settlement_required=NO, special_color="blue", special_activated=signal)])
    role.do_perform()
    assert not controller.touches and role.phase == "prepare"


def test_unknown_settlement_cannot_mean_clear_even_if_ultimate_ready(monkeypatch):
    role, _, _, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(ultimate_ready=YES, settlement_required=UNKNOWN)])
    role.do_perform()
    assert not controller.touches


def test_special_to_necessary_attack_is_immediate_and_early_ready_cannot_interrupt(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    seen = observations(monkeypatch, role, [
        special_start(),
        dict(abyss_phase=YES, settlement_required=YES, ultimate_ready=YES),
        dict(long_attack_complete=UNKNOWN, ultimate_ready=YES),
        dict(long_attack_complete=YES, ultimate_ready=YES),
    ])
    role.do_perform()
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 975, 510), ("down", 1105, 500)]
    first_up = controller.posts.index("up")
    assert controller.posts[first_up + 1] == ("down", 1105, 500)
    assert [s.sequence for s in seen] == [1, 2, 3, 4]
    assert role.phase == "prepare" and not role._settlement_pending
    assert combat.input_scope.owned_contact is None and not context.actions


def test_pending_attack_precedes_ready_and_cannot_be_erased_by_reset(monkeypatch):
    role, _, _, controller, _ = setup_role(monkeypatch)
    role._settlement_pending = True
    role._enter_stationary("attack_settlement")
    role.reset_state()
    assert role.phase == "attack_settlement" and role._settlement_pending
    observations(monkeypatch, role, [dict(abyss_phase=YES, settlement_required=NO, ultimate_ready=YES),
                                    dict(long_attack_complete=YES)])
    role.do_perform()
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 1105, 500)]


def test_required_attack_waits_for_positive_phase_and_keeps_its_budget(monkeypatch):
    role, _, _, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(settlement_required=YES, abyss_phase=UNKNOWN, ultimate_ready=YES)])
    role.do_perform()
    deadline = role._stationary_deadline
    assert role._settlement_pending and not controller.touches
    clock.now = deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        tick(role)
    assert role._settlement_pending and role.phase == "recover" and not controller.touches


def test_charge_completion_and_ultimate_finish_are_separate_without_qte_or_switch(monkeypatch):
    role, _, context, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [
        dict(settlement_required=NO, ultimate_ready=YES),
        dict(ultimate_charge_complete=NO, ultimate_ready=NO),
        dict(ultimate_charge_complete=YES, ultimate_finished=YES),
        dict(ultimate_finished=UNKNOWN),
        dict(ultimate_finished=NO),
        dict(ultimate_finished=YES),
    ])
    role.do_perform()
    assert role.phase == "ultimate_finish"
    for _ in range(2):
        tick(role)
        assert role.phase == "ultimate_finish"
    tick(role)
    assert role.phase == "prepare"
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 1000, 650)]
    assert controller.posts.count("up") == 1 and not context.actions
    assert not role._post_ult_qte_used


@pytest.mark.parametrize("stage", ["special", "attack", "ultimate"])
def test_hold_false_never_advances_and_releases(monkeypatch, stage):
    role, _, context, controller, _ = setup_role(monkeypatch)
    start = {"special": special_start(), "attack": dict(settlement_required=YES, abyss_phase=YES),
             "ultimate": dict(settlement_required=NO, ultimate_ready=YES)}[stage]
    observations(monkeypatch, role, [start])
    with pytest.raises(CombatInputError, match="not_completed|budget_exhausted"):
        role.do_perform()
    assert role.phase == "recover" and controller.posts.count("up") == 1
    assert len([p for p in controller.posts if isinstance(p, tuple)]) == 1
    assert not context.actions


@pytest.mark.parametrize("mutation", ["generation", "sequence", "expired", "future"])
def test_invalid_current_observation_refuses_down(monkeypatch, mutation):
    role, combat, _, controller, _ = setup_role(monkeypatch)
    original = role._read_stationary_signals

    def read(image):
        snapshot = original(image)
        values = {"generation": dict(generation=99), "sequence": dict(sequence=0),
                  "expired": dict(captured_at=-2), "future": dict(captured_at=1)}[mutation]
        return replace(snapshot, **values)

    monkeypatch.setattr(role, "_read_stationary_signals", read)
    with pytest.raises(CombatInputError, match="snapshot"):
        role.do_perform()
    assert not controller.touches and combat.input_scope.blocked


def test_same_frame_cannot_drive_another_tick(monkeypatch):
    role, _, _, controller, _ = setup_role(monkeypatch)
    role.do_perform()
    with pytest.raises(CombatInputError, match="repeated_snapshot"):
        role.do_perform()
    assert not controller.touches


def test_old_last_metadata_alone_cannot_authorize_a_new_hold(monkeypatch):
    role, combat, _, controller, _ = setup_role(monkeypatch)
    role._enter_stationary("special_hold")
    snapshot = role._observe_stationary(combat.frame)
    combat.input_scope.invalidate_frame()
    with pytest.raises(CombatInputError, match="current_start_frame_missing"):
        role._hold_stationary("长按1号球", snapshot, lambda _: True)
    assert not controller.touches


@pytest.mark.parametrize("mutation", ["new_frame", "old_last", "generation", "expired", "pending_input", "scope"])
def test_changed_binding_after_release_refuses_handoff(monkeypatch, mutation):
    role, combat, _, controller, clock = setup_role(monkeypatch)
    scope = combat.input_scope
    initial = scope.last_frame_metadata
    observations(monkeypatch, role, [special_start(), dict(abyss_phase=YES, settlement_required=YES)])
    original = role.action.hold_primary_input

    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        if mutation == "new_frame":
            scope.capture_frame()
        elif mutation == "old_last":
            scope.last_frame_metadata = initial
        elif mutation == "generation":
            scope.generation += 1
        elif mutation == "expired":
            clock.now += 1.1
        elif mutation == "pending_input":
            scope.owned_contact = 0
            scope.release_pending = True
        else:
            # Even a new object carrying identical metadata cannot inherit the
            # result of another scope's completed hold.
            other = CombatInputScope(combat.context, combat, generation=scope.generation,
                monotonic=clock.monotonic, sleep=clock.sleep)
            other.last_frame_metadata = scope.last_frame_metadata
            combat.input_scope = other
        return result

    monkeypatch.setattr(role.action, "hold_primary_input", hold)
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert role.phase == "recover"
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 975, 510)]
    combat.combat_end()


def test_stopping_during_observation_releases_without_handoff(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)

    def stopped():
        context.tasker.stopping = True
        return dict(abyss_phase=YES, settlement_required=YES)

    observations(monkeypatch, role, [special_start(), stopped])
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert controller.posts.count("up") == 1
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 975, 510)]
    assert combat.input_scope.owned_contact is None and role.phase == "recover"


def test_failed_up_cannot_start_necessary_attack(monkeypatch):
    role, combat, _, controller, _ = setup_role(monkeypatch)
    controller.up_success = False
    observations(monkeypatch, role, [special_start(), dict(abyss_phase=YES, settlement_required=YES)])
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 975, 510)]
    assert combat.input_scope.release_pending and combat.input_scope.stop_unconfirmed


def test_unexpected_slash_is_not_charged_or_reported_finished(monkeypatch):
    role, _, _, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [dict(slash_phase=YES, ultimate_ready=YES, settlement_required=NO)])
    with pytest.raises(CombatInputError, match="unexpected_slash"):
        role.do_perform()
    assert role.phase == "recover" and not controller.touches


def slash_start(**updates):
    values = dict(slash_phase=YES, settlement_required=NO)
    values.update(updates)
    return values


@pytest.mark.parametrize("phase", [NO, UNKNOWN])
def test_multi_without_positive_slash_refuses_entry_even_if_other_actions_ready(monkeypatch, phase):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene="multi")
    observations(monkeypatch, role, [slash_start(
        slash_phase=phase, ultimate_ready=YES, ultimate_charge_complete=NO,
        special_activated=YES, special_color="blue",
    )])
    with pytest.raises(InverseCrownUnavailable, match="multi_slash_entry_not_ready"):
        role.do_perform()
    assert role.phase == "recover" and combat.input_scope.blocked
    assert not controller.touches and not context.actions


@pytest.mark.parametrize("pending,required", [(True, NO), (False, YES), (False, UNKNOWN)])
def test_multi_slash_never_discards_uncleared_settlement(monkeypatch, pending, required):
    role, _, context, controller, _ = setup_role(monkeypatch, scene="multi")
    role._settlement_pending = pending
    observations(monkeypatch, role, [slash_start(settlement_required=required)])
    with pytest.raises(InverseCrownUnavailable, match="multi_slash_settlement_not_clear"):
        role.do_perform()
    role.reset_state()
    assert role.phase == "recover" and role._settlement_pending is pending
    assert not controller.touches and not context.actions


def test_multi_holds_attack_until_positive_slash_exit_then_waits_for_fresh_finish(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene="multi")
    seen = observations(monkeypatch, role, [
        slash_start(ultimate_ready=YES),
        slash_start(ultimate_finished=YES),
        slash_start(slash_phase=NO, ultimate_finished=YES),
        slash_start(slash_phase=NO, ultimate_finished=YES),
    ])
    role.do_perform()
    assert role.phase == "ultimate_finish" and len(seen) == 3
    assert controller.posts == ["capture", ("down", 1105, 500), "capture", "capture", "up"]
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
    role.reset_state()
    assert role.phase == "ultimate_finish"
    tick(role)
    assert role.phase == "prepare" and [s.sequence for s in seen] == [1, 2, 3, 4]
    assert controller.posts.count(("down", 1105, 500)) == 1 and not context.actions
    assert not role._post_ult_qte_used


@pytest.mark.parametrize("update", [
    dict(slash_phase=UNKNOWN), dict(settlement_required=YES), dict(settlement_required=UNKNOWN),
])
def test_multi_slash_uncertain_or_conflicting_observation_releases_without_tail_input(monkeypatch, update):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene="multi")
    observations(monkeypatch, role, [slash_start(), slash_start(**update)])
    with pytest.raises(CombatInputError, match="multi_slash_"):
        role.do_perform()
    role.reset_state()
    assert role.phase == "recover" and combat.input_scope.blocked
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
    assert controller.posts == ["capture", ("down", 1105, 500), "capture", "up"]
    assert not context.actions


def test_multi_slash_stopping_releases_without_waiting_for_exit(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene="multi")

    def stopped():
        context.tasker.stopping = True
        return slash_start()

    observations(monkeypatch, role, [slash_start(), stopped])
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert role.phase == "recover" and combat.input_scope.owned_contact is None
    assert controller.posts == ["capture", ("down", 1105, 500), "capture", "up"]
    assert not context.actions


def test_multi_slash_failed_release_cannot_advance_to_ultimate_finish(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene="multi")
    controller.up_success = False
    observations(monkeypatch, role, [slash_start(), slash_start(slash_phase=NO)])
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert role.phase == "recover" and combat.input_scope.release_pending
    assert combat.input_scope.stop_unconfirmed
    assert controller.posts.count(("down", 1105, 500)) == 1 and not context.actions


def test_multi_persistent_slash_is_bounded_and_timeout_is_not_phase_exit(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch, scene="multi")
    observations(monkeypatch, role, [slash_start()] * 30)
    with pytest.raises(CombatInputError, match="not_completed|budget_exhausted"):
        role.do_perform()
    assert role.phase == "recover" and combat.input_scope.blocked
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
    assert controller.posts.count("up") == 1 and not context.actions


@pytest.mark.parametrize("updates", [
    dict(slash_phase=YES), dict(slash_phase=UNKNOWN),
    dict(ultimate_finished=NO), dict(ultimate_finished=UNKNOWN),
    dict(settlement_required=YES), dict(settlement_required=UNKNOWN),
])
def test_multi_finish_requires_clear_settlement_positive_exit_and_fresh_finish(monkeypatch, updates):
    role, _, _, controller, clock = setup_role(monkeypatch, scene="multi")
    role._enter_stationary("ultimate_finish")
    deadline = role._stationary_deadline
    current = slash_start(slash_phase=NO, ultimate_finished=YES)
    current.update(updates)
    observations(monkeypatch, role, [current])
    role.do_perform()
    role.reset_state()
    assert role.phase == "ultimate_finish" and role._stationary_deadline == deadline
    assert not controller.touches
    clock.now = deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        tick(role)
    assert role.phase == "recover" and not controller.touches


@pytest.mark.parametrize("phase", ["prepare", "ultimate_finish"])
def test_persistent_unknown_exhausts_observation_budget_without_success(monkeypatch, phase):
    role, _, _, controller, clock = setup_role(monkeypatch)
    role._enter_stationary(phase)
    role.do_perform()
    assert role.phase == phase and not controller.touches
    clock.now = role._stationary_deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        tick(role)
    assert role.phase == "recover" and not controller.touches


@pytest.mark.parametrize("phase,method", [
    ("idle", "_phase_idle"), ("ball_dump", "_phase_ball_dump"),
    ("special_press", "_phase_special_press"), ("special_attack", "_phase_special_attack"),
    ("skill", "_phase_skill"), ("farm", "_phase_farm"), ("unexpected", "_phase_idle"),
])
def test_ordinary_dispatch_unchanged(monkeypatch, phase, method):
    role, _, _, _, _ = setup_role(monkeypatch, enabled=False)
    called = []
    role.phase = phase
    monkeypatch.setattr(role, method, lambda: called.append(method))
    monkeypatch.setattr(role, "_perform_stationary", lambda: pytest.fail("ordinary entered opt-in"))
    role.do_perform()
    assert called == [method]


def test_ordinary_finish_keeps_existing_auxiliary_and_switch_contract(monkeypatch):
    role, _, _, _, _ = setup_role(monkeypatch, enabled=False)
    called = []
    monkeypatch.setattr(role.action, "auxiliary_machine", lambda: called.append("aux"))
    monkeypatch.setattr(role.action, "use_qte", lambda: called.append("qte"))
    role._skill_switch_after = True
    role._finish_skill()
    assert role.phase == "switch" and called == ["aux", "qte"]


def test_boolean_cannot_silently_replace_three_value_signal():
    with pytest.raises(ValueError, match="three-valued"):
        _StationarySignals(1, 1, 0.0, ultimate_ready=True)


@pytest.mark.parametrize("text,reason", [
    ("0/100", "dead"), ("10509/105", "hp_out_of_range"),
    ("10509/", "hp_text_invalid"), (None, "hp_not_recognized"),
])
def test_strict_hp_refuses_new_down_before_any_semantic_detection(monkeypatch, text, reason):
    role, _, context, controller, _ = setup_role(monkeypatch)
    context.recognition_map["检查血量"] = FakeRecognitionResult(hit=text is not None,
        best_result=FakeBestResult(box=(19, 15, 141, 29), text=text or ""))
    monkeypatch.setattr(role, "_read_stationary_signals", lambda *_: pytest.fail("HP failed before signals"))
    before = list(controller.posts)
    with pytest.raises(CombatInputError, match=reason):
        role.do_perform()
    assert controller.posts == before and not controller.touches and role.phase == "recover"


def test_strict_hp_ocr_exception_refuses_new_down(monkeypatch):
    role, _, context, controller, _ = setup_role(monkeypatch)

    def unavailable(*_):
        raise RuntimeError("OCR unavailable")

    monkeypatch.setattr(context, "run_recognition", unavailable)
    with pytest.raises(CombatInputError, match="hp_recognition_error"):
        role.do_perform()
    assert not controller.touches and role.phase == "recover"


@pytest.mark.parametrize("failure", [
    parse_hp_fraction("0/100"), parse_hp_fraction("10509/105"),
    HpObservation(None, None, "hp_not_recognized"),
    HpObservation(None, None, "hp_recognition_error", error_type="RuntimeError"),
])
def test_strict_holding_hp_failure_releases_and_never_hands_off(monkeypatch, failure):
    role, combat, _, controller, _ = setup_role(monkeypatch)
    reads = iter([parse_hp_fraction("100/100"), failure])
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", lambda *_: next(reads))
    observations(monkeypatch, role, [special_start(), dict(abyss_phase=YES, settlement_required=YES)])
    with pytest.raises(CombatInputError, match=failure.reason):
        role.do_perform()
    assert [p for p in controller.posts if isinstance(p, tuple)] == [("down", 975, 510)]
    assert controller.posts.count("up") == 1 and combat.input_scope.owned_contact is None
    assert role.phase == "recover" and combat.input_scope.blocked


@pytest.mark.parametrize("during_hold", [False, True])
@pytest.mark.parametrize("limit", ["stage", "frame_age"])
def test_positive_hp_that_outlives_budget_or_snapshot_cannot_authorize_input(monkeypatch, during_hold, limit):
    role, combat, _, controller, clock = setup_role(monkeypatch)
    if limit == "frame_age":
        monkeypatch.setattr(module, "_STATIONARY_BUDGETS", {name: 5.0 for name in module._STATIONARY_BUDGETS})
    original = combat.combat_check.read_hp_from_frame
    calls = 0

    def slow_read(context, image):
        nonlocal calls
        calls += 1
        result = original(context, image)
        if calls == (2 if during_hold else 1):
            clock.now = role._stationary_deadline if limit == "stage" else clock.now + 1.01
        return result

    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", slow_read)
    observations(monkeypatch, role, [special_start(), dict(abyss_phase=YES, settlement_required=YES)])
    with pytest.raises(CombatInputError, match="budget_exhausted|expired_snapshot"):
        role.do_perform()
    assert len([p for p in controller.posts if isinstance(p, tuple)]) == int(during_hold)
    assert controller.posts.count("up") == int(during_hold) and role.phase == "recover"


@pytest.mark.parametrize("mutation", ["image", "metadata", "scope"])
def test_hp_result_must_keep_exact_supplied_frame_and_scope_binding(monkeypatch, mutation):
    role, combat, _, controller, _ = setup_role(monkeypatch)
    original = combat.combat_check.read_hp_from_frame
    source_image = combat.frame

    def changed(context, image):
        assert image is source_image
        result = original(context, image)
        if mutation == "image":
            combat.frame = object()
        elif mutation == "metadata":
            combat.input_scope.frame_metadata = replace(combat.input_scope.frame_metadata)
        else:
            combat.input_scope = object()
        return result

    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", changed)
    with pytest.raises(CombatInputError, match="hp_frame_replaced|observation_scope_replaced"):
        role.do_perform()
    assert not controller.touches


def test_alive_handoff_carries_same_frame_result_without_an_extra_hp_read(monkeypatch):
    role, combat, _, controller, _ = setup_role(monkeypatch)
    original = combat.combat_check.read_hp_from_frame
    images = []
    confirmed = []

    def read(context, image):
        assert image is combat.frame
        images.append((combat.input_scope.frame_metadata.sequence, image))
        controller.posts.append(("strict_hp", combat.input_scope.frame_metadata.sequence))
        return original(context, image)

    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", read)
    hold = role._hold_stationary

    def record(*args, **kwargs):
        result = hold(*args, **kwargs)
        assert result.hp.alive and result.hp.current == 100
        confirmed.append(result)
        return result

    monkeypatch.setattr(role, "_hold_stationary", record)
    observations(monkeypatch, role, [special_start(), dict(abyss_phase=YES, settlement_required=YES),
                                    dict(long_attack_complete=YES)])
    role.do_perform()
    assert [sequence for sequence, _ in images] == [1, 2, 3]
    first_up = controller.posts.index("up")
    assert controller.posts[first_up + 1] == ("down", 1105, 500)
    assert [snapshot.sequence for snapshot in confirmed] == [2, 3]
    assert role.phase == "prepare" and not role._settlement_pending


def resource_start(**updates):
    values = dict(settlement_required=NO, abyss_phase=NO, slash_phase=NO,
                  special_activated=NO, ultimate_ready=NO)
    values.update(updates)
    return values


def test_resource_rebuild_taps_once_per_fresh_tick_without_auxiliary_input(monkeypatch):
    role, combat, context, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [resource_start(), resource_start()])
    role.do_perform()
    deadline = role._stationary_deadline
    assert controller.posts == ["capture", ("down", 1105, 500), "up"]
    assert combat.frame is None and combat.input_scope.frame_metadata is None
    tick(role)
    assert controller.posts == ["capture", ("down", 1105, 500), "up",
                                "capture", ("down", 1105, 500), "up"]
    assert role.phase == "prepare" and role._stationary_deadline == deadline
    assert not context.actions and combat.input_scope.owned_contact is None


@pytest.mark.parametrize("signal", ["settlement_required", "abyss_phase", "slash_phase",
                                    "special_activated", "ultimate_ready"])
def test_resource_rebuild_requires_every_signal_to_be_known_no(monkeypatch, signal):
    role, _, _, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [resource_start(**{signal: UNKNOWN})])
    role.do_perform()
    assert not controller.touches and role.phase == "prepare"


@pytest.mark.parametrize("signal", ["settlement_required", "abyss_phase", "special_activated"])
def test_resource_rebuild_does_not_tap_when_an_incompatible_phase_is_yes(monkeypatch, signal):
    role, _, _, controller, _ = setup_role(monkeypatch)
    observations(monkeypatch, role, [resource_start(**{signal: YES})])
    role.do_perform()
    assert not controller.touches


@pytest.mark.parametrize("route", ["pending", "ultimate", "special"])
def test_existing_actions_precede_resource_rebuild(monkeypatch, route):
    role, _, _, controller, _ = setup_role(monkeypatch)
    monkeypatch.setattr(role.action, "tap_primary_input", lambda *_a, **_k: pytest.fail("resource tap"))
    holds = []

    def hold(node, snapshot, completed, **kwargs):
        holds.append(node)
        return replace(snapshot, settlement_required=NO, ultimate_ready=YES)

    monkeypatch.setattr(role, "_hold_stationary", hold)
    if route == "pending":
        role._settlement_pending = True
        role._enter_stationary("attack_settlement")
        start = resource_start(abyss_phase=YES)
    elif route == "ultimate":
        start = resource_start(ultimate_ready=YES)
    else:
        start = resource_start(special_activated=YES, special_color="blue")
    observations(monkeypatch, role, [start])
    role.do_perform()
    assert holds == [{"pending": "攻击", "ultimate": "技能", "special": "长按1号球"}[route]]
    assert not controller.touches


@pytest.mark.parametrize("boundary", ["target", "shared_observation"])
@pytest.mark.parametrize("mutation", ["age", "budget", "image", "metadata", "scope", "context"])
def test_resource_tap_rechecks_binding_and_time_after_synchronous_calls(monkeypatch, boundary, mutation):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    monkeypatch.setattr(module, "_STATIONARY_BUDGETS", {name: 5.0 for name in module._STATIONARY_BUDGETS})
    observations(monkeypatch, role, [resource_start()])

    def mutate():
        if mutation == "age":
            clock.now += 1.01
        elif mutation == "budget":
            clock.now = role._stationary_deadline
        elif mutation == "image":
            combat.frame = object()
        elif mutation == "metadata":
            combat.input_scope.frame_metadata = replace(combat.input_scope.frame_metadata)
        elif mutation == "scope":
            combat.input_scope = object()
        else:
            combat.context = FakeContext()

    if boundary == "target":
        original = context.get_node_data

        def read(name):
            result = original(name)
            if name == "攻击":
                mutate()
            return result

        monkeypatch.setattr(context, "get_node_data", read)
    else:
        def observe(_):
            mutate()
            return False

        monkeypatch.setattr(combat, "observe_hold_state", observe)
    with pytest.raises(CombatInputError):
        role.do_perform()
    assert not controller.touches and role.phase == "recover"


def test_resource_prepare_deadline_survives_repeated_taps_and_external_reset(monkeypatch):
    role, _, _, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [resource_start(), resource_start()])
    role.do_perform()
    deadline = role._stationary_deadline
    clock.now = deadline / 2
    role.reset_state()
    assert role.phase == "prepare" and role._stationary_deadline == deadline
    tick(role)
    assert role._stationary_deadline == deadline
    clock.now = deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        tick(role)
    assert role.phase == "recover" and controller.posts.count(("down", 1105, 500)) == 2


def test_confirmed_self_sss_plus_cycle_rebuilds_empty_resource_with_original_prepare_budget(monkeypatch):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    role.inverse_crown_profile = combat.inverse_crown_profile = replace(
        role.inverse_crown_profile, source="self", rank="SSS+", signature_weapon_confirmed=True,
    )
    observations(monkeypatch, role, [
        dict(settlement_required=NO, ultimate_ready=YES),
        dict(ultimate_charge_complete=YES, ultimate_finished=YES),
        dict(ultimate_finished=YES),
        resource_start(resource_appearance="empty"),
        resource_start(resource_appearance="empty"),
    ])
    role.do_perform()
    assert role.phase == "ultimate_finish"
    tick(role)
    assert role.phase == "prepare"
    prepare_deadline = role._stationary_deadline
    tick(role)
    role.reset_state()
    tick(role)
    assert role.phase == "prepare" and role._stationary_deadline == prepare_deadline
    assert [p for p in controller.posts if isinstance(p, tuple)] == [
        ("down", 1000, 650), ("down", 1105, 500), ("down", 1105, 500),
    ]
    assert controller.posts.count("up") == 3 and combat.input_scope.owned_contact is None
    assert not context.actions and not role._settlement_pending
    clock.now = prepare_deadline
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        tick(role)
    assert role.phase == "recover" and controller.posts.count("up") == 3


def test_resource_up_crossing_prepare_deadline_fails_after_release(monkeypatch):
    role, combat, _, controller, clock = setup_role(monkeypatch)
    observations(monkeypatch, role, [resource_start()])
    original = controller.post_touch_up

    def slow_up(contact=0):
        clock.now += 0.09
        return original(contact)

    monkeypatch.setattr(controller, "post_touch_up", slow_up)
    with pytest.raises(CombatInputError, match="budget_exhausted"):
        role.do_perform()
    assert role.phase == "recover" and combat.input_scope.blocked
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
    assert controller.posts == ["capture", ("down", 1105, 500), "up"]


def test_resource_snapshot_is_rechecked_after_slow_controller_acquisition(monkeypatch):
    role, combat, _, controller, clock = setup_role(monkeypatch)
    monkeypatch.setattr(module, "_STATIONARY_BUDGETS", {name: 5.0 for name in module._STATIONARY_BUDGETS})
    observations(monkeypatch, role, [resource_start()])
    scope = combat.input_scope
    original = scope.controller_for_jobs

    def slow_controller():
        result = original()
        if scope.owned_contact is None:
            clock.now += 1.01
        return result

    monkeypatch.setattr(scope, "controller_for_jobs", slow_controller)
    with pytest.raises(CombatInputError, match="expired_snapshot"):
        role.do_perform()
    assert not controller.touches and role.phase == "recover"


@pytest.mark.parametrize("stage_budget", [0.08, 5.0])
def test_resource_tap_passes_the_earlier_snapshot_and_prepare_start_deadline(monkeypatch, stage_budget):
    role, combat, _, _, _ = setup_role(monkeypatch)
    monkeypatch.setattr(module, "_STATIONARY_BUDGETS", {
        name: stage_budget for name in module._STATIONARY_BUDGETS
    })
    observations(monkeypatch, role, [resource_start()])
    scope = combat.input_scope
    seen = []
    original = scope.tap_semantic

    def tap(node_name, **kwargs):
        expected = min(scope.frame_metadata.captured_at + module._SNAPSHOT_MAX_AGE,
                       role._stationary_deadline)
        assert kwargs["start_deadline"] == expected
        seen.append(kwargs["start_deadline"])
        return original(node_name, **kwargs)

    monkeypatch.setattr(scope, "tap_semantic", tap)
    role.do_perform()
    assert seen == [min(1.0, stage_budget)]


@pytest.mark.parametrize("latency,stage_budget", [(1.01, 5.0), (0.09, 0.08)],
                         ids=["expired-frame", "expired-prepare"])
def test_resource_tap_last_sdk_query_cannot_outlive_its_start_deadline(monkeypatch, latency, stage_budget):
    role, combat, context, controller, clock = setup_role(monkeypatch)
    monkeypatch.setattr(module, "_STATIONARY_BUDGETS", {
        name: stage_budget for name in module._STATIONARY_BUDGETS
    })
    observations(monkeypatch, role, [resource_start()])
    original_tasker = context.tasker

    class SlowTasker:
        armed = False

        def __getattr__(self, name):
            return getattr(original_tasker, name)

        @property
        def stopping(self):
            if self.armed:
                self.armed = False
                clock.now += latency
            return original_tasker.stopping

    tasker = context.tasker = SlowTasker()
    original_validate = role._validate_stationary_snapshot
    retained_checks = 0

    def validate(snapshot, *, retained=False):
        nonlocal retained_checks
        result = original_validate(snapshot, retained=retained)
        if retained:
            retained_checks += 1
            if retained_checks == 2:
                tasker.armed = True
        return result

    monkeypatch.setattr(role, "_validate_stationary_snapshot", validate)
    with pytest.raises(CombatInputError, match="点按开始期限已到"):
        role.do_perform()
    assert clock.now == latency and controller.posts == ["capture"]
    assert not controller.touches and role.phase == "recover"
    assert combat.input_scope.owned_contact is None and not combat.input_scope.release_pending
