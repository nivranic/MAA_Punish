"""Bounded primary input tests with pending/running/late native job stand-ins."""

from types import SimpleNamespace
import threading

import numpy as np
import pytest

from action.combat.actions.CombatActions import CombatActions
from action.combat.capture import CombatCaptureError, capture_frame
from action.combat.input_control import (
    CombatInputError,
    CombatInputScope,
    CombatInputTimeout,
    primary_input_position,
)
from test_support.fakes import FakeClock, FakeContext, FakeController, TimedFakeJob


class _Controller(FakeController):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.jobs = {"capture": [], "down": [], "up": []}
        self.posts = []
        self.next_id = 10

    def _post(self, kind):
        self.posts.append(kind)
        if self.jobs[kind]:
            result = self.jobs[kind].pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        self.next_id += 1
        return TimedFakeJob(self.clock, job_id=self.next_id)

    def post_screencap(self):
        return self._post("capture")

    def post_touch_down(self, x, y, contact=0, pressure=1):
        self.touches.append(("down", x, y, contact, pressure))
        return self._post("down")

    def post_touch_up(self, contact=0):
        self.touches.append(("up", contact))
        return self._post("up")


def _setup(**kwargs):
    clock = FakeClock()
    context = FakeContext()
    context.node_data["攻击"] = {
        "action": {"type": "Click", "param": {"target": [1200, 620]}}
    }
    controller = _Controller(clock)
    context.tasker.controller = controller
    combat = SimpleNamespace(
        frame=None, input_scope=None, observe_hold_state=lambda _: False,
        out_of_combat_reason="",
    )
    scope = CombatInputScope(
        context, combat, generation=11, monotonic=clock.monotonic,
        sleep=clock.sleep, capture_timeout=0.1, input_timeout=0.1,
        release_timeout=0.1, poll_interval=0.01, **kwargs,
    )
    combat.input_scope = scope
    return clock, context, controller, combat, scope


def test_capture_polls_pending_running_and_records_only_its_success():
    clock, context, controller, combat, scope = _setup()
    job = TimedFakeJob(clock, start_at=0.02, finish_at=0.05, job_id=42)
    controller.jobs["capture"].append(job)
    states = []
    clock.on_sleep = lambda _: states.append((job.pending, job.running))
    assert scope.capture_frame() is job.image
    metadata = scope.frame_metadata
    assert metadata.generation == 11 and metadata.sequence == 1 and metadata.job_id == 42
    assert metadata.captured_at == pytest.approx(0.05)
    assert (True, False) in states and (False, True) in states
    assert combat.frame is job.image and job.get_calls == 1
    assert scope.pending_capture is None and not scope.capture_in_flight
    assert scope.close() and scope.close()


@pytest.mark.parametrize("finish_at", [0.0, float("inf")], ids=["failed", "running-timeout"])
def test_capture_failure_invalidates_current_frame_preserves_success_metadata(finish_at):
    clock, context, controller, combat, scope = _setup()
    scope.capture_frame()
    previous = scope.last_frame_metadata
    failed = TimedFakeJob(clock, finish_at=finish_at, success=False)
    controller.jobs["capture"].append(failed)
    with pytest.raises(CombatCaptureError):
        scope.capture_frame()
    assert combat.frame is None and scope.frame_metadata is None
    assert scope.last_frame_metadata is previous and scope.capture_sequence == 1
    assert scope.blocked and scope.stop_unconfirmed and failed.get_calls == 0
    assert context.tasker.stop_requests == 1
    assert clock.now <= 0.100001
    before = list(controller.posts)
    with pytest.raises(CombatInputError):
        scope.capture_frame()
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=1, observe=lambda _: True)
    assert controller.posts == before
    assert not scope.close()


@pytest.mark.parametrize("query_delay", [0.1, 0.2])
def test_success_query_crossing_capture_deadline_never_reads_cache(query_delay):
    clock, _, controller, combat, scope = _setup()
    scope.capture_frame()
    previous = scope.last_frame_metadata

    class SlowSuccessJob(TimedFakeJob):
        @property
        def succeeded(self):
            clock.now += query_delay
            return super().succeeded

    job = SlowSuccessJob(clock)
    controller.jobs["capture"].append(job)
    with pytest.raises(CombatCaptureError):
        scope.capture_frame()
    assert job.get_calls == 0
    assert combat.frame is None and scope.frame_metadata is None
    assert scope.last_frame_metadata is previous and scope.capture_sequence == 1
    assert scope.blocked and scope.stop_unconfirmed
    assert controller.posts == ["capture", "capture"]


def test_timed_out_capture_cannot_read_late_cache_or_publish_into_new_session():
    clock, context, controller, combat, scope = _setup()
    job = TimedFakeJob(clock, finish_at=0.2)
    controller.jobs["capture"].append(job)
    with pytest.raises(CombatCaptureError):
        scope.capture_frame()
    assert scope.pending_capture is job
    clock.now = 1
    new_frame = np.ones((2, 2, 3), dtype=np.uint8)
    combat.input_scope = object()
    combat.frame = new_frame
    with pytest.raises(CombatInputError):
        scope.capture_frame()
    assert combat.frame is new_frame and job.get_calls == 0


def test_stopping_during_capture_blocks_followup_and_never_reads_cache():
    clock, context, controller, combat, scope = _setup()
    job = TimedFakeJob(clock, finish_at=0.05)
    controller.jobs["capture"].append(job)
    clock.on_sleep = lambda _: setattr(context.tasker, "stopping", True)
    with pytest.raises(CombatCaptureError):
        scope.capture_frame()
    assert clock.now == pytest.approx(0.01)
    assert scope.blocked and scope.stop_unconfirmed and job.get_calls == 0
    assert combat.frame is None and controller.posts == ["capture"]


def test_only_one_pending_capture_can_be_posted():
    _, _, controller, _, scope = _setup()
    scope.capture_in_flight = True
    with pytest.raises(CombatCaptureError, match="已有未完成"):
        scope.capture_frame()
    assert controller.posts == []


def test_capture_replaced_during_poll_cannot_publish_to_new_scope():
    clock, _, controller, combat, scope = _setup()
    job = TimedFakeJob(clock, finish_at=0.05)
    controller.jobs["capture"].append(job)
    marker = object()

    def replaced(_):
        combat.input_scope = object()
        combat.frame = marker

    clock.on_sleep = replaced
    with pytest.raises(CombatCaptureError):
        scope.capture_frame()
    assert combat.frame is marker and job.get_calls == 0 and scope.stop_unconfirmed


def test_successful_hold_uses_fresh_frames_and_releases_once():
    clock, context, controller, combat, scope = _setup()
    controller.jobs["down"].append(TimedFakeJob(clock, finish_at=0.02))
    observed = []

    def observe(image):
        observed.append(scope.frame_metadata.sequence)
        assert image is combat.frame
        return len(observed) == 2

    assert scope.hold_semantic("攻击", duration=0.2, observe=observe)
    assert observed == [1, 2]
    assert controller.posts == ["down", "capture", "capture", "up"]
    assert context.actions == [] and controller.clicks == []
    assert combat.frame is None and scope.frame_metadata is None
    assert scope.owned_contact is None and not scope.release_pending
    assert scope.close() and scope.close()
    assert controller.posts.count("up") == 1


def test_position_validator_receives_only_resolved_immutable_point_consumed_by_down():
    _, context, controller, _, scope = _setup()
    original = context.get_node_data
    reads, validated = [], []

    def current_node(name):
        reads.append(name)
        return original(name)

    def validate(position):
        assert type(position) is tuple and position == (1200, 620)
        assert scope.owned_contact is None and not scope.release_pending
        assert controller.posts == []
        with pytest.raises(TypeError):
            position[0] = 0
        validated.append(position)
        # A later Context value must not replace the exact validated point.
        context.node_data["攻击"]["action"]["param"]["target"] = [111, 222]
        return True

    context.get_node_data = current_node
    assert scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True,
                               position_validator=validate)
    assert reads == ["攻击"] and validated == [(1200, 620)]
    assert controller.touches == [("down", 1200, 620, 0, 1), ("up", 0)]


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_position_validator_requires_literal_true_without_input_or_stop(value):
    _, context, controller, _, scope = _setup()
    with pytest.raises(CombatInputError, match="坐标校验未确认"):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True,
                            position_validator=lambda _: value)
    assert controller.posts == [] and context.tasker.stop_requests == 0
    assert scope.owned_contact is None and not scope.release_pending
    assert not scope.blocked and not scope.stop_unconfirmed and scope.close()


def test_position_validator_exception_is_preserved_without_input_or_stop():
    _, context, controller, _, scope = _setup()
    error = ValueError("layout drift")

    def reject(_):
        raise error

    with pytest.raises(ValueError) as raised:
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True,
                            position_validator=reject)
    assert raised.value is error
    assert controller.posts == [] and context.tasker.stop_requests == 0
    assert scope.owned_contact is None and not scope.release_pending
    assert not scope.blocked and not scope.stop_unconfirmed and scope.close()


@pytest.mark.parametrize("explicit_none", [False, True])
def test_default_position_validator_preserves_existing_hold_path(explicit_none):
    _, context, controller, _, scope = _setup()
    kwargs = {"position_validator": None} if explicit_none else {}
    assert scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True, **kwargs)
    assert controller.posts == ["down", "capture", "up"]
    assert controller.touches == [("down", 1200, 620, 0, 1), ("up", 0)]
    assert context.tasker.stop_requests == 0


def test_stop_check_after_position_validation_cannot_be_bypassed():
    _, context, controller, _, scope = _setup()
    stopped = False

    def validate(_):
        nonlocal stopped
        stopped = True
        return True

    with pytest.raises(CombatInputError, match="共享观察要求停止"):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True,
                            position_validator=validate, should_stop=lambda: stopped)
    assert controller.posts == [] and context.tasker.stop_requests == 1
    assert scope.stop_unconfirmed and scope.blocked


@pytest.mark.parametrize("boundary", ["target_parse", "position_validator", "should_stop"])
@pytest.mark.parametrize("route", ["tasker_stopping", "scope_request_stop"])
def test_stop_at_final_pre_down_boundary_never_registers_or_posts_contact(boundary, route):
    _, context, controller, _, scope = _setup()

    def stop():
        assert scope.owned_contact is None and not scope.release_pending
        if route == "tasker_stopping":
            context.tasker.stopping = True
        else:
            scope.request_stop("pre_down_callback_stop")

    kwargs = {}
    if boundary == "target_parse":
        original = context.get_node_data

        def stopped_node(name):
            result = original(name)
            stop()
            return result

        # The default path has neither optional callback.
        context.get_node_data = stopped_node
    elif boundary == "position_validator":
        def validator(position):
            assert position == (1200, 620)
            stop()
            return True

        # Deliberately omit should_stop: shared stop state is independent.
        kwargs["position_validator"] = validator
    else:
        def should_stop():
            stop()
            return False

        kwargs["should_stop"] = should_stop

    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True, **kwargs)
    assert controller.posts == [] and controller.touches == []
    assert scope.owned_contact is None and not scope.release_pending
    assert not scope._down_post_uncertain and scope._down_job is None
    assert scope.blocked and scope.stop_unconfirmed
    assert context.tasker.stop_requests == int(route == "scope_request_stop")
    assert not scope.close() and controller.posts == []


def test_hold_stage_budget_expires_with_release_and_no_new_auxiliary_input():
    clock, _, controller, _, scope = _setup()
    assert not scope.hold_semantic("攻击", duration=0.025, observe=lambda _: None)
    assert clock.now == pytest.approx(0.025)
    assert controller.posts == ["down", "capture", "capture", "capture", "up"]
    assert not scope.blocked and not scope.release_pending


@pytest.mark.parametrize("failure", ["post", "failed", "timeout"])
def test_down_failure_always_attempts_up_and_keeps_uncertain_ownership(failure):
    clock, _, controller, _, scope = _setup()
    down = (
        OSError("post may have reached native controller") if failure == "post"
        else TimedFakeJob(clock, finish_at=1 if failure == "timeout" else 0, success=False)
    )
    controller.jobs["down"].append(down)
    with pytest.raises((CombatInputError, OSError)):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert controller.posts == ["down", "up"]
    assert scope.blocked and scope.stop_unconfirmed
    assert scope.release_pending == (failure != "failed")
    assert (scope.owned_contact == 0) == (failure != "failed")


@pytest.mark.parametrize("failure", ["post", "failed", "timeout"])
def test_up_failure_is_not_reported_as_success_and_no_followup_down(failure):
    clock, _, controller, _, scope = _setup()
    up = OSError("up post error") if failure == "post" else TimedFakeJob(
        clock, finish_at=1 if failure == "timeout" else 0, success=False
    )
    controller.jobs["up"].append(up)
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert scope.release_pending and scope.owned_contact == 0
    assert scope.stop_unconfirmed and scope.blocked
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert controller.posts.count("down") == 1
    assert clock.now <= 0.100001


def test_failed_up_is_retried_by_close_without_clearing_stop_unconfirmed():
    clock, _, controller, _, scope = _setup()
    controller.jobs["up"].append(TimedFakeJob(clock, success=False))
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert scope.release_pending and scope.owned_contact == 0
    assert not scope.close()
    assert controller.posts == ["down", "capture", "up", "up"]
    assert not scope.release_pending and scope.owned_contact is None
    assert scope.blocked and scope.closed and scope.stop_unconfirmed
    assert not scope.close()
    assert controller.posts.count("up") == 2
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)


@pytest.mark.parametrize("start_at", [0, float("inf")], ids=["running", "pending"])
def test_close_reuses_pending_or_running_up_without_reposting(start_at):
    clock, _, controller, _, scope = _setup()
    up = TimedFakeJob(clock, start_at=start_at, finish_at=float("inf"))
    controller.jobs["up"].append(up)
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert not scope.close()
    assert not scope.close()
    assert controller.posts == ["down", "capture", "up"]
    assert scope.release_pending and scope.owned_contact == 0
    assert scope.stop_unconfirmed


def test_observer_exception_is_preserved_and_finally_releases_contact():
    _, context, controller, _, scope = _setup()
    error = ValueError("recognition exception")

    def observe(_):
        raise error

    with pytest.raises(ValueError) as raised:
        scope.hold_semantic("攻击", duration=0.2, observe=observe)
    assert raised.value is error
    assert controller.posts == ["down", "capture", "up"]
    assert not scope.release_pending and scope.blocked and scope.stop_unconfirmed
    assert context.tasker.stop_requests == 1


def test_up_before_late_down_is_never_reused_as_a_final_release_receipt():
    clock, _, controller, _, scope = _setup()
    down = TimedFakeJob(clock, finish_at=0.2)
    controller.jobs["down"].append(down)
    with pytest.raises(CombatInputTimeout):
        scope.hold_semantic("攻击", duration=0.3, observe=lambda _: True)
    assert scope.release_pending and scope.owned_contact == 0
    assert controller.posts == ["down", "up"]
    clock.now = 0.3
    controller.jobs["up"].append(TimedFakeJob(clock, finish_at=float("inf")))
    assert not scope.close()
    assert controller.posts == ["down", "up", "up"]
    assert scope.release_pending and scope.owned_contact == 0
    assert scope.stop_unconfirmed


def test_polling_both_jobs_done_cannot_hide_up_before_down_completion():
    clock, _, controller, _, scope = _setup()
    controller.jobs["down"].append(TimedFakeJob(clock, finish_at=0.105))
    controller.jobs["up"].append(TimedFakeJob(clock, finish_at=0.102))
    with pytest.raises(CombatInputTimeout):
        scope.hold_semantic("攻击", duration=0.3, observe=lambda _: True)
    # First release polling wakes at .11: both are now done, but Up was first.
    assert clock.now == pytest.approx(0.11)
    assert scope.release_pending and scope.owned_contact == 0
    assert not scope.close()
    assert controller.posts == ["down", "up", "up"]
    assert scope.stop_unconfirmed and not scope.release_pending


def test_late_up_can_release_contact_but_cannot_certify_native_stop_or_restart():
    clock, context, controller, _, scope = _setup()
    up = TimedFakeJob(clock, finish_at=0.2)
    controller.jobs["up"].append(up)
    stop_job = TimedFakeJob(clock, finish_at=float("inf"))
    context.tasker.post_stop = lambda: stop_job
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.3, observe=lambda _: True)
    assert scope.release_pending and scope.owned_contact == 0
    assert not scope.close()
    assert scope.release_pending
    clock.now = 0.3
    assert not scope.close()
    assert not scope.release_pending and scope.owned_contact is None
    assert scope.stop_unconfirmed and scope.closed and scope.blocked
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.3, observe=lambda _: True)
    assert controller.posts.count("up") == 1


def test_observer_finishing_after_stage_deadline_cannot_report_completion():
    clock, _, controller, _, scope = _setup()

    def observe(_):
        clock.now += 1
        return True

    with pytest.raises(CombatInputTimeout):
        scope.hold_semantic("攻击", duration=0.2, observe=observe)
    assert controller.posts == ["down", "capture", "up"]
    assert not scope.release_pending and scope.stop_unconfirmed and scope.blocked


def test_combat_actions_runs_shared_exit_check_before_role_observation():
    _, context, controller, combat, scope = _setup()
    combat.observe_hold_state = lambda _: True
    combat.out_of_combat_reason = "hp_zero"
    actions = CombatActions(context)
    actions._role = SimpleNamespace(combat=combat)
    observed = []
    with pytest.raises(CombatInputError):
        actions.hold_primary_input("攻击", duration=0.1, observe=lambda _: observed.append(True))
    assert observed == [] and controller.posts == ["down", "capture", "up"]
    assert scope.failure_reason == "hp_zero" and scope.stop_unconfirmed


def test_shared_stop_after_observation_releases_without_a_second_frame():
    _, _, controller, _, scope = _setup()
    stopped = False

    def observe(_):
        nonlocal stopped
        stopped = True

    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=observe, should_stop=lambda: stopped)
    assert controller.posts == ["down", "capture", "up"]
    assert not scope.release_pending and scope.stop_unconfirmed


@pytest.mark.parametrize("target, offset, expected", [
    ([1200, 620], [-60, 30, 0, 0], (1140, 650)),
    ([1180, 600, 40, 40], [-60, 30, 0, 0], (1140, 650)),
    ([1200, 620, 0, 0], [0, 0, 0, 0], (1200, 620)),
])
def test_semantic_target_uses_current_context_offset_once(target, offset, expected):
    _, context, controller, _, scope = _setup()
    context.node_data["攻击"]["action"]["param"].update(target=target, target_offset=offset)
    assert primary_input_position(context, "攻击") == expected
    assert scope.hold_semantic("攻击", duration=0.1, observe=lambda _: True)
    assert controller.touches[0] == ("down", *expected, 0, 1)
    assert context.node_data["攻击"]["action"]["param"]["target"] is target


@pytest.mark.parametrize("patch", [
    {"target": True}, {"target": "识别攻击键"}, {"target": [-1, 1]},
    {"target": [1, 2, -1, 4]}, {"target": [1.0, 2]},
    {"target_offset": [1, 2]}, {"target_offset": [1, 2, 1, 0]},
    {"target_offset": [True, 0, 0, 0]}, {"contact": 1},
])
def test_dynamic_or_invalid_layout_fails_before_any_input(patch):
    _, context, controller, _, scope = _setup()
    context.node_data["攻击"]["action"]["param"].update(patch)
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.1, observe=lambda _: True)
    assert controller.posts == []


@pytest.mark.parametrize("node_name, action_type", [("攻击", "ClickKey"), ("qte1", "Click"), ("辅助机", "Click")])
def test_keyboard_and_unverified_auxiliary_nodes_are_rejected(node_name, action_type):
    _, context, controller, _, scope = _setup()
    context.node_data[node_name] = {"action": {"type": action_type, "param": {"target": [1, 2]}}}
    with pytest.raises(CombatInputError):
        scope.hold_semantic(node_name, duration=0.1, observe=lambda _: True)
    assert controller.posts == []


def test_scope_rejects_another_thread_without_controller_calls():
    _, _, controller, _, scope = _setup()
    errors = []

    def other_thread():
        try:
            scope.capture_frame()
        except CombatInputError as error:
            errors.append(str(error))

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    assert len(errors) == 1 and controller.posts == []


def test_combat_actions_requires_scope_and_rejects_blocked_cached_frame():
    _, context, controller, combat, scope = _setup()
    actions = CombatActions(context)
    with pytest.raises(CombatInputError):
        actions.hold_primary_input("攻击", duration=0.1, observe=lambda _: True)
    actions._role = SimpleNamespace(combat=combat)
    assert actions.hold_primary_input("攻击", duration=0.1, observe=lambda _: True)
    scope.request_stop("test")
    combat.frame = np.zeros((2, 2, 3), dtype=np.uint8)
    with pytest.raises(CombatInputError):
        actions._get_image()


def test_ordinary_capture_without_scope_preserves_legacy_contract():
    context = FakeContext()
    combat = SimpleNamespace(frame=None, input_scope=None)
    assert capture_frame(context, combat) is context.tasker.controller.image
    assert combat.frame is context.tasker.controller.image
    assert context.tasker.stop_requests == 0


class _ReplacingControllerTasker:
    """Model RemoteTasker: each getter destroys the previous native wrapper."""

    def __init__(self, tasker):
        self.original = tasker
        self.backend = tasker.controller
        self.getter_calls = 0

    def __getattr__(self, name):
        return getattr(self.original, name)

    @property
    def controller(self):
        self.getter_calls += 1
        owner = self
        generation = self.getter_calls

        def check_alive():
            if owner.getter_calls != generation:
                raise RuntimeError("remote Controller was destroyed by a later getter")

        class BoundJob:
            def __init__(self, job):
                self.job = job

            def __getattr__(self, name):
                check_alive()
                return getattr(self.job, name)

        class ControllerLease:
            def __getattr__(self, name):
                check_alive()
                method = getattr(owner.backend, name)

                def post(*args, **kwargs):
                    check_alive()
                    return BoundJob(method(*args, **kwargs))

                return post

        return ControllerLease()


def test_remote_controller_survives_down_captures_and_release_status_queries():
    _, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)
    frames = []

    def observe(image):
        frames.append(image)
        return len(frames) == 2

    assert scope.hold_semantic("攻击", duration=0.2, observe=observe)
    assert controller.posts == ["down", "capture", "capture", "up"]
    assert tasker.getter_calls == 1
    assert scope.owned_contact is None and not scope.release_pending
    assert scope.close()


def test_remote_controller_is_reacquired_after_terminal_operation_and_external_getter():
    _, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)
    scope.capture_frame()
    tasker.controller  # A normal combat action between scoped operations.
    assert scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    tasker.controller
    assert scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert tasker.getter_calls == 5
    assert controller.posts == ["capture", "down", "capture", "up", "down", "capture", "up"]
    assert scope.close()


def test_remote_controller_is_retained_for_pending_up_and_late_cleanup():
    clock, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)
    controller.jobs["up"].append(TimedFakeJob(clock, finish_at=0.3))
    with pytest.raises(CombatInputError):
        scope.hold_semantic("攻击", duration=0.2, observe=lambda _: True)
    assert not scope.close()
    assert tasker.getter_calls == 1 and scope.release_pending
    clock.now = 0.4
    assert not scope.close()  # Up completes, but outer stop is still unconfirmed.
    assert tasker.getter_calls == 1 and not scope.release_pending
    assert controller.posts == ["down", "capture", "up"]


def test_remote_controller_is_retained_for_pending_capture_on_failure():
    clock, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)
    controller.jobs["capture"].append(TimedFakeJob(clock, finish_at=0.5))
    with pytest.raises(CombatCaptureError):
        scope.capture_frame()
    assert not scope.close()
    assert tasker.getter_calls == 1 and scope.capture_in_flight
    clock.now = 0.6
    assert scope.pending_capture.done
    assert controller.posts == ["capture"]


def test_tap_releases_immediately_without_capture_delay_or_ordinary_actions():
    clock, context, controller, combat, scope = _setup()
    scope.capture_frame()
    context.node_data["攻击"]["action"]["param"]["target_offset"] = [3, -2, 0, 0]
    observed = []

    def before_down():
        assert combat.frame is not None and scope.frame_metadata is not None
        observed.append(True)
        return False

    assert scope.tap_semantic("攻击", should_stop=before_down)
    assert observed == [True] and clock.now == 0
    assert controller.posts == ["capture", "down", "up"]
    assert controller.touches == [("down", 1203, 618, 0, 1), ("up", 0)]
    assert not context.actions and not controller.clicks
    assert combat.frame is None and scope.frame_metadata is None
    assert scope.owned_contact is None and not scope.release_pending and scope.close()


@pytest.mark.parametrize("failure", ["post", "failed", "timeout"])
def test_tap_down_failure_uses_existing_release_and_stop_ownership(failure):
    clock, context, controller, _, scope = _setup()
    controller.jobs["down"].append(
        OSError("post uncertain") if failure == "post" else TimedFakeJob(
            clock, finish_at=1 if failure == "timeout" else 0, success=False)
    )
    with pytest.raises((CombatInputError, OSError)):
        scope.tap_semantic("攻击")
    assert controller.posts == ["down", "up"]
    assert scope.blocked and scope.stop_unconfirmed and context.tasker.stop_requests == 1
    assert scope.release_pending is (failure != "failed")
    assert (scope.owned_contact == 0) is (failure != "failed")
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert controller.posts.count("down") == 1


@pytest.mark.parametrize("failure", ["post", "failed", "timeout"])
def test_tap_up_failure_never_reports_success_or_allows_another_down(failure):
    clock, _, controller, _, scope = _setup()
    controller.jobs["up"].append(
        OSError("up uncertain") if failure == "post" else TimedFakeJob(
            clock, finish_at=1 if failure == "timeout" else 0, success=False)
    )
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert controller.posts == ["down", "up"]
    assert scope.owned_contact == 0 and scope.release_pending
    assert scope.blocked and scope.stop_unconfirmed
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert controller.posts.count("down") == 1


def test_tap_late_down_cannot_reuse_the_earlier_up_as_final_release():
    clock, _, controller, _, scope = _setup()
    controller.jobs["down"].append(TimedFakeJob(clock, finish_at=0.105))
    controller.jobs["up"].append(TimedFakeJob(clock, finish_at=0.102))
    with pytest.raises(CombatInputTimeout):
        scope.tap_semantic("攻击")
    assert clock.now == pytest.approx(0.11)
    assert scope.release_pending and scope.owned_contact == 0
    assert not scope.close()
    assert controller.posts == ["down", "up", "up"]
    assert scope.stop_unconfirmed and not scope.release_pending


@pytest.mark.parametrize("kind", ["down", "up"])
def test_tap_stop_during_success_status_query_cannot_return_success(kind):
    clock, context, controller, _, scope = _setup()

    class StoppingSuccess(TimedFakeJob):
        @property
        def succeeded(self):
            context.tasker.stopping = True
            return super().succeeded

    controller.jobs[kind].append(StoppingSuccess(clock))
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert controller.posts == ["down", "up"]
    assert scope.blocked and scope.stop_unconfirmed
    assert not scope.release_pending and scope.owned_contact is None


@pytest.mark.parametrize("boundary", ["target", "validator", "guard"])
def test_tap_stop_before_down_never_registers_or_posts_a_contact(boundary):
    _, context, controller, _, scope = _setup()

    def stop():
        context.tasker.stopping = True

    kwargs = {}
    if boundary == "target":
        original = context.get_node_data

        def read(name):
            result = original(name)
            stop()
            return result

        context.get_node_data = read
    elif boundary == "validator":
        def validator(_):
            stop()
            return True

        kwargs["position_validator"] = validator
    else:
        def guard():
            stop()
            return False

        kwargs["should_stop"] = guard
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击", **kwargs)
    assert not controller.posts and not controller.touches
    assert scope.owned_contact is None and not scope.release_pending
    assert not scope._down_post_uncertain


@pytest.mark.parametrize("pending", ["release", "capture", "capture_job"])
def test_tap_refuses_existing_pending_work(pending):
    clock, _, controller, _, scope = _setup()
    if pending == "release":
        scope.release_pending = True
    elif pending == "capture":
        scope.capture_in_flight = True
    else:
        scope.pending_capture = TimedFakeJob(clock, finish_at=1)
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert not controller.posts


def test_tap_keeps_one_remote_controller_for_down_and_up_and_reacquires_when_idle():
    _, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)
    assert scope.tap_semantic("攻击")
    assert tasker.getter_calls == 1
    tasker.controller
    assert scope.tap_semantic("攻击")
    assert tasker.getter_calls == 3
    assert controller.posts == ["down", "up", "down", "up"]
    assert scope.close()


def test_tap_wrapper_requires_current_scope_frame_and_runs_shared_check_before_down():
    _, context, controller, combat, scope = _setup()
    actions = CombatActions(context)
    with pytest.raises(CombatInputError):
        actions.tap_primary_input("攻击")
    actions._role = SimpleNamespace(combat=combat)
    with pytest.raises(CombatInputError):
        actions.tap_primary_input("攻击")
    image = scope.capture_frame()
    shared = []
    combat.observe_hold_state = lambda frame: shared.append(frame) or False
    assert actions.tap_primary_input("攻击")
    assert shared == [image] and controller.posts == ["capture", "down", "up"]
    assert not context.actions and not controller.clicks


def test_tap_wrapper_shared_exit_refuses_down():
    _, context, controller, combat, scope = _setup()
    scope.capture_frame()
    combat.observe_hold_state = lambda _: True
    combat.out_of_combat_reason = "combat_ui_lost"
    actions = CombatActions(context)
    actions._role = SimpleNamespace(combat=combat)
    with pytest.raises(CombatInputError):
        actions.tap_primary_input("攻击")
    assert controller.posts == ["capture"] and not controller.touches
    assert scope.failure_reason == "combat_ui_lost" and scope.stop_unconfirmed


@pytest.mark.parametrize("boundary", ["validator", "guard"])
def test_rejected_tap_drops_idle_controller_before_a_later_external_getter(boundary):
    _, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)

    def reject(*_):
        raise ValueError("pre-Down refusal")

    key = "position_validator" if boundary == "validator" else "should_stop"
    with pytest.raises(ValueError):
        scope.tap_semantic("攻击", **{key: reject})
    assert not controller.posts and not scope.blocked
    tasker.controller
    assert scope.tap_semantic("攻击")
    assert controller.posts == ["down", "up"] and tasker.getter_calls == 3


def test_tap_stop_during_down_wait_retains_late_down_responsibility():
    clock, context, controller, _, scope = _setup()
    controller.jobs["down"].append(TimedFakeJob(clock, finish_at=0.05))
    clock.on_sleep = lambda _: setattr(context.tasker, "stopping", True)
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert controller.posts == ["down", "up"]
    assert scope.owned_contact == 0 and scope.release_pending and scope.stop_unconfirmed
    clock.now = 0.2
    assert not scope.close()
    assert controller.posts == ["down", "up", "up"]
    assert scope.owned_contact is None and not scope.release_pending


def test_tap_pending_up_retains_its_controller_and_job_through_cleanup():
    clock, context, controller, _, scope = _setup()
    tasker = context.tasker = _ReplacingControllerTasker(context.tasker)
    controller.jobs["up"].append(TimedFakeJob(clock, finish_at=0.3))
    with pytest.raises(CombatInputError):
        scope.tap_semantic("攻击")
    assert not scope.close()
    assert tasker.getter_calls == 1 and controller.posts == ["down", "up"]
    assert scope.release_pending and scope.owned_contact == 0
    clock.now = 0.4
    assert not scope.close()
    assert tasker.getter_calls == 1 and controller.posts == ["down", "up"]
    assert not scope.release_pending and scope.stop_unconfirmed


@pytest.mark.parametrize("value", [
    True, False, "1", [], {}, float("nan"), float("inf"), -float("inf"),
    pytest.param(10 ** 1000, id="overflowing-int"),
])
def test_tap_start_deadline_rejects_non_finite_or_non_numeric_values(value):
    _, _, controller, _, scope = _setup()
    with pytest.raises(ValueError, match="开始期限必须为有限数值"):
        scope.tap_semantic("攻击", start_deadline=value)
    assert not controller.posts and not controller.touches
    assert scope.owned_contact is None and not scope.release_pending and not scope.blocked


@pytest.mark.parametrize("start_deadline", [None, 1, 1.0])
def test_tap_optional_or_future_start_deadline_preserves_immediate_input(start_deadline):
    _, _, controller, _, scope = _setup()
    assert scope.tap_semantic("攻击", start_deadline=start_deadline)
    assert controller.posts == ["down", "up"]


def test_tap_equal_start_deadline_refuses_contact_before_post():
    clock, _, controller, _, scope = _setup()
    with pytest.raises(CombatInputTimeout, match="点按开始期限已到"):
        scope.tap_semantic("攻击", start_deadline=clock.now)
    assert not controller.posts and scope.owned_contact is None
    assert not scope.release_pending and not scope.blocked


def test_tap_checks_deadline_locally_after_its_last_synchronous_stopping_query():
    clock, context, controller, _, scope = _setup()
    original_tasker = context.tasker

    class SlowTasker:
        armed = False
        queries_after_guard = 0

        def __getattr__(self, name):
            return getattr(original_tasker, name)

        @property
        def stopping(self):
            if self.armed:
                self.queries_after_guard += 1
                clock.now += 0.01
            return original_tasker.stopping

    tasker = context.tasker = SlowTasker()

    def checked_snapshot():
        tasker.armed = True
        return False

    with pytest.raises(CombatInputTimeout, match="点按开始期限已到"):
        scope.tap_semantic("攻击", start_deadline=0.01, should_stop=checked_snapshot)
    assert tasker.queries_after_guard == 1 and clock.now == pytest.approx(0.01)
    assert not controller.posts and not controller.touches
    assert scope.owned_contact is None and not scope.release_pending


def test_tap_wrapper_forwards_start_deadline_without_repeating_shared_observation(monkeypatch):
    _, context, _, combat, scope = _setup()
    image = scope.capture_frame()
    seen, shared = [], []
    original = scope.tap_semantic

    def tap(node_name, **kwargs):
        seen.append(kwargs["start_deadline"])
        return original(node_name, **kwargs)

    monkeypatch.setattr(scope, "tap_semantic", tap)
    combat.observe_hold_state = lambda frame: shared.append(frame) or False
    actions = CombatActions(context)
    actions._role = SimpleNamespace(combat=combat)
    assert actions.tap_primary_input("攻击", start_deadline=1.0)
    assert seen == [1.0] and shared == [image]
