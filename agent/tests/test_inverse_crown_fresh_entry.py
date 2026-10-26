"""Same-call entry contract; fakes do not establish Android/game acceptance."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from action.combat.config.inverse_crown_profile import (
    InverseCrownProfile, InverseCrownProfileError, InverseCrownUnavailable,
)
from action.combat.core.fresh_entry import (
    PREPARE_NODE, START_NODE, begin_from_preparation, preparation_entry_requested,
)
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.entry import runner
from action.combat.input_control import CombatInputError, CombatInputScope
from action.combat.roles.inverse_crown import InverseCrown, _Signal, _StationaryVisualGeometry
from test_support.fakes import FakeClock, FakeContext, FakeController, make_hit, make_miss


class EntryContext(FakeContext):
    def run_action(self, entry, *, box):
        self.actions.append((entry, box))
        if isinstance(self.action_result, BaseException):
            raise self.action_result
        return self.action_result


@pytest.fixture
def setup(monkeypatch):
    clock = FakeClock()
    context = EntryContext(node_data={
        PREPARE_NODE: {"recognition": {"type": "And", "param": {
            "all_of": ["作战开始识别", "作战开始背景颜色识别"]}}},
        START_NODE: {"action": {"type": "Click", "param": {"target": True}}},
    }, recognition_map={
        PREPARE_NODE: make_hit((1000, 640, 100, 40)),
        "战斗中": make_miss(), "战斗重启1": make_miss(),
        "检查血量": make_hit(),
    })
    context.recognition_map["检查血量"].best_result.text = "100/100"
    context.tasker.controller = FakeController(image=np.zeros((720, 1280, 3), np.uint8))
    context.action_result = SimpleNamespace(success=True, action_id=100, name=START_NODE,
                                           action="Click", box=(1000, 640, 100, 40))
    profile = InverseCrownProfile(enabled=True, scene="single", source="self",
                                 signature_weapon_confirmed=True)
    combat = CombatTask(context, CombatCheck(), inverse_crown_profile=profile)
    combat.input_scope = CombatInputScope(context, combat, generation=23,
        monotonic=clock.monotonic, sleep=clock.sleep)
    return SimpleNamespace(combat=combat, context=context, clock=clock)


def bind(case, monkeypatch):
    combat, context = case.combat, case.context
    combat.fresh_entry = begin_from_preparation(combat)
    assert combat.fresh_entry.role is None  # Click success is still a candidate.
    context.recognition_map["战斗中"] = make_hit()
    combat._capture_frame()
    combat.combat_ui_visible = True
    combat.field_identity = "confirmed"
    role = InverseCrown(combat, "R", "InverseCrown")
    combat.team = TeamSnapshot.solo("InverseCrown")
    combat.roles = {"R": role}
    combat.fresh_entry.bind_first_hud(combat)
    role._enter_stationary("prepare")
    geometry = _StationaryVisualGeometry((1280, 720), (76, 62, 213, 3),
                                        (942, 594, 124, 126), (1004, 657), (493, 602, 294, 50))
    monkeypatch.setattr(role, "_stationary_visual_geometry", lambda _image: geometry)
    return role, combat.fresh_entry


@pytest.mark.parametrize("node,param", [
    ("战斗中", {"entry_mode": "preparation"}), (PREPARE_NODE, {}),
    (PREPARE_NODE, {"entry_mode": "restart"}), ("战斗中", []),
    ("战斗中", False), (PREPARE_NODE, "invalid"),
])
def test_only_explicit_preparation_node_can_request_start(node, param):
    with pytest.raises(InverseCrownProfileError):
        preparation_entry_requested(node, param)


def test_default_takeover_never_requests_start():
    for raw in ("", "{}", {}, None):
        assert preparation_entry_requested("战斗中", raw) is False
    assert preparation_entry_requested(PREPARE_NODE, '{"entry_mode":"preparation"}') is True


@pytest.mark.parametrize("start", [False, True])
def test_existing_real_gate_prevents_all_capture_and_start(setup, monkeypatch, start):
    monkeypatch.setattr(setup.combat, "_capture_frame", lambda: pytest.fail("guard must run first"))
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        setup.combat.combat_once(from_preparation=start)
    assert setup.context.actions == [] and setup.combat.fresh_entry is None


def test_actual_runner_preparation_route_keeps_existing_gate(setup, monkeypatch):
    context = setup.context
    context.node_data[PREPARE_NODE]["attach"] = {"inverse_crown_profile": {
        "enabled": True, "scene": "single", "source": "self", "signature_weapon_confirmed": True}}
    monkeypatch.setattr(runner, "require_inverse_crown_layout", lambda _context: None)
    action = runner.CombatRunner()
    assert action.run(context, SimpleNamespace(node_name=PREPARE_NODE,
        custom_action_param='{"entry_mode":"preparation"}')).success is False
    assert action.last_outcome.exit_reason == "strategy_not_ready"
    assert context.actions == []


@pytest.mark.parametrize("field,value", [("source", "trial"), ("source", "borrowed"),
    ("source", "unknown"), ("scene", "multi"), ("signature_weapon_confirmed", False)])
def test_account_facts_never_authorize_other_battle_sources(setup, field, value):
    setup.combat.inverse_crown_profile = replace(setup.combat.inverse_crown_profile, **{field: value})
    with pytest.raises(InverseCrownUnavailable, match="battle_facts_required"):
        begin_from_preparation(setup.combat)
    assert setup.context.actions == []


@pytest.mark.parametrize("node,result", [
    ("战斗中", make_hit()), ("战斗中", None), ("战斗重启1", make_hit()),
    (PREPARE_NODE, make_miss()), (PREPARE_NODE, None),
    (PREPARE_NODE, make_hit((0, 0, 100, 100))),
])
def test_hud_restart_unknown_and_bad_box_never_click(setup, node, result):
    setup.context.recognition_map[node] = result
    with pytest.raises(InverseCrownUnavailable):
        begin_from_preparation(setup.combat)
    assert setup.context.actions == []


@pytest.mark.parametrize("result", [None, False, SimpleNamespace(success=False, action_id=100),
    SimpleNamespace(success=True, action_id=None), RuntimeError("native failed")])
def test_one_start_attempt_is_not_retried_on_failed_or_uncertain_result(setup, result):
    setup.context.action_result = result
    with pytest.raises((InverseCrownUnavailable, RuntimeError)):
        begin_from_preparation(setup.combat)
    assert setup.context.actions == [(START_NODE, (1000, 640, 100, 40))]
    assert setup.combat.fresh_entry is None
    with pytest.raises(InverseCrownUnavailable, match="start_already_attempted"):
        begin_from_preparation(setup.combat)
    assert len(setup.context.actions) == 1


@pytest.mark.parametrize("field,value", [("name", "战斗中"), ("action", "DoNothing"),
    ("box", (1000, 640, 99, 40)), ("action_id", -1)])
def test_unrelated_successful_action_detail_cannot_become_entry(setup, field, value):
    setattr(setup.context.action_result, field, value)
    with pytest.raises(InverseCrownUnavailable):
        begin_from_preparation(setup.combat)
    assert len(setup.context.actions) == 1 and setup.combat.fresh_entry is None


def test_current_fresh_frame_is_used_and_slow_recognition_refuses(setup):
    original = setup.context.run_recognition
    frames = []
    def recognize(node, image):
        frames.append(image)
        result = original(node, image)
        setup.clock.now += .3
        return result
    setup.context.run_recognition = recognize
    with pytest.raises(InverseCrownUnavailable, match="preparation_changed"):
        begin_from_preparation(setup.combat)
    assert len(frames) == 3 and all(image is frames[0] for image in frames)
    assert setup.context.actions == []


@pytest.mark.parametrize("when,change", [("before", "stop"), ("before", "profile"),
    ("before", "generation"), ("after", "stop"), ("after", "profile"),
    ("after", "generation"), ("after", "scope"), ("after", "context")])
def test_start_checks_stop_and_owner_before_and_after_single_native_call(setup, when, change):
    combat, context = setup.combat, setup.context
    def mutate():
        if change == "stop": context.tasker.stopping = True
        if change == "profile": combat.inverse_crown_profile = replace(combat.inverse_crown_profile, source="trial")
        if change == "generation": combat.input_scope.generation += 1
        if change == "scope": combat.input_scope = CombatInputScope(context, combat, generation=99)
        if change == "context": combat.context = FakeContext()
    if when == "before":
        original = context.run_recognition
        def recognize(node, image):
            result = original(node, image)
            if node == PREPARE_NODE: mutate()
            return result
        context.run_recognition = recognize
    else:
        original = context.run_action
        def action(entry, **kwargs):
            result = original(entry, **kwargs)
            mutate()
            return result
        context.run_action = action
    with pytest.raises((InverseCrownUnavailable, CombatInputError)):
        begin_from_preparation(combat)
    assert len(context.actions) == (0 if when == "before" else 1)
    assert combat.fresh_entry is None


@pytest.mark.parametrize("patch", [
    {"action": {"type": "Click", "param": {"target": False}}},
    {"repeat": 2}, {"pre_delay": 1000},
    {"action": {"type": "Click", "param": {"target": True, "target_offset": [2, 0, 0, 0]}}},
])
def test_native_start_definition_cannot_add_retry_delay_or_changed_target(setup, patch):
    setup.context.node_data[START_NODE].update(patch)
    with pytest.raises(InverseCrownUnavailable, match="start_action_invalid"):
        begin_from_preparation(setup.combat)
    assert setup.context.actions == []


def test_first_hud_requires_strict_hp_and_current_hud(setup, monkeypatch):
    setup.context.recognition_map["检查血量"].best_result.text = "100100"
    with pytest.raises(InverseCrownUnavailable, match="first_hud_unconfirmed"):
        bind(setup, monkeypatch)
    assert setup.combat.fresh_entry.retired


def test_same_session_flow_binds_only_after_hud_and_retires_at_end(setup, monkeypatch):
    combat, context = setup.combat, setup.context
    order, observed = [], []
    monkeypatch.setattr(combat, "ensure_strategy_ready", lambda *_: order.append("guard_fixture_only"))
    original_action = context.run_action
    def start(entry, **kwargs):
        assert order == ["guard_fixture_only"]
        result = original_action(entry, **kwargs)
        order.append("start")
        context.recognition_map["战斗中"] = make_hit()
        return result
    context.run_action = start
    def load():
        assert combat.fresh_entry.role is None
        order.append("identity")
        combat.team = TeamSnapshot.solo("InverseCrown")
        role = InverseCrown(combat, "R", "InverseCrown")
        combat.roles = {"R": role}
        combat.field_identity = "confirmed"
        return True
    monkeypatch.setattr(combat, "load_team", load)
    def observe():
        observed.append(combat.fresh_entry)
        assert combat.fresh_entry.role is combat.get_current_role()
        context.tasker.stopping = True
    monkeypatch.setattr(combat, "perform", observe)
    combat.combat_once(from_preparation=True)
    assert order == ["guard_fixture_only", "start", "identity"]
    assert len(context.actions) == 1 and len(observed) == 1 and observed[0].retired


def test_start_success_is_not_hud_or_initial_no(setup):
    candidate = begin_from_preparation(setup.combat)
    assert candidate.role is None
    with pytest.raises(InverseCrownUnavailable, match="first_hud_unconfirmed"):
        candidate.bind_first_hud(setup.combat)
    assert candidate.retired


@pytest.mark.parametrize("change", ["scope", "generation", "profile", "role", "identity", "stop"])
def test_bound_opportunity_rejects_changed_owner_and_facts(setup, monkeypatch, change):
    role, candidate = bind(setup, monkeypatch)
    combat = setup.combat
    if change == "scope": combat.input_scope = CombatInputScope(setup.context, combat, generation=24)
    if change == "generation": combat.input_scope.generation += 1
    if change == "profile": combat.inverse_crown_profile = replace(combat.inverse_crown_profile, source="trial")
    if change == "role": combat.roles["R"] = InverseCrown(combat, "R", "InverseCrown")
    if change == "identity": combat.field_identity = "unknown"
    if change == "stop": setup.context.tasker.stopping = True
    assert candidate.available(combat, role) is False and candidate.retired


def test_first_opportunity_has_explicit_rule_source_and_reset_cannot_rearm(setup, monkeypatch):
    role, candidate = bind(setup, monkeypatch)
    snapshot = role._observe_stationary(setup.combat.frame)
    assert snapshot.settlement_required is _Signal.NO and snapshot.special_activated is _Signal.YES
    assert snapshot.initial_basis == "same_call_entry_rule_inference"
    assert snapshot.abyss_phase is _Signal.UNKNOWN and snapshot.long_attack_complete is _Signal.UNKNOWN
    role.reset_state()
    assert candidate.retired
    setup.combat._capture_frame()
    snapshot = role._observe_stationary(setup.combat.frame)
    assert snapshot.settlement_required is _Signal.UNKNOWN and snapshot.special_activated is _Signal.UNKNOWN
    assert snapshot.initial_basis == "unknown"


def test_first_input_request_retires_before_native_call_even_if_it_raises(setup, monkeypatch):
    role, candidate = bind(setup, monkeypatch)
    snapshot = role._observe_stationary(setup.combat.frame)
    calls = []
    def hold(*args, **kwargs):
        calls.append(candidate.retired)
        raise RuntimeError("before any confirmed down")
    monkeypatch.setattr(role.action, "hold_primary_input", hold)
    role._enter_stationary("special_hold")
    with pytest.raises(RuntimeError):
        role._hold_stationary("长按1号球", snapshot, lambda _s: False)
    assert calls == [True]
    role._enter_stationary("prepare")
    assert candidate.available(setup.combat, role) is False


def test_layout_loss_and_cleanup_retire_instead_of_rearming(setup, monkeypatch):
    role, candidate = bind(setup, monkeypatch)
    monkeypatch.setattr(role, "_stationary_visual_geometry", lambda _image: None)
    snapshot = role._observe_stationary(setup.combat.frame)
    assert candidate.retired and snapshot.initial_basis == "unknown"
    setup.combat.combat_end()
    assert candidate.retired


def test_unrelated_or_nested_history_cannot_create_initial_facts(setup, monkeypatch):
    combat = setup.combat
    combat._capture_frame()
    combat.context.get_task_job = lambda: pytest.fail("history cannot authorize entry")
    role = InverseCrown(combat, "R", "InverseCrown")
    role._enter_stationary("prepare")
    monkeypatch.setattr(role, "_stationary_visual_geometry", lambda _image: None)
    snapshot = role._observe_stationary(combat.frame)
    assert snapshot.settlement_required is _Signal.UNKNOWN
    assert snapshot.special_activated is _Signal.UNKNOWN and snapshot.initial_basis == "unknown"


def test_native_start_node_returns_real_action_detail_and_one_memory_click(tmp_path):
    # This is the real SDK and current resource, but only an in-memory
    # Controller. It verifies the run_action ABI/target, not a game transition.
    from pathlib import Path
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from test_inverse_crown_task_option import _MemoryController

    class Controller(_MemoryController):
        def click(self, *args):
            self.inputs.append(args)
            return True

    class Inspect(CustomAction):
        detail = None
        def run(self, context, argv):
            self.detail = context.run_action(START_NODE, box=(1000, 640, 100, 40))
            return CustomAction.RunResult(success=self.detail is not None and self.detail.success)

    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.post_bundle(Path(__file__).resolve().parents[2] / "resource/base").wait().succeeded
    inspect, controller = Inspect(), Controller()
    assert resource.register_custom_action("InspectStartAction", inspect)
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    job = tasker.post_task("InspectStartAction", {"InspectStartAction": {
        "action": {"type": "Custom", "param": {"custom_action": "InspectStartAction"}},
        "pre_delay": 0, "post_delay": 0}})
    assert job.wait().succeeded
    detail = inspect.detail
    assert detail.action_id > 0 and detail.success
    assert detail.name == START_NODE and detail.action == "Click"
    assert (detail.box.x, detail.box.y, detail.box.w, detail.box.h) == (1000, 640, 100, 40)
    assert len(controller.inputs) == 1
    x, y = controller.inputs[0][:2]
    assert 1000 <= x <= 1100 and 640 <= y <= 680
