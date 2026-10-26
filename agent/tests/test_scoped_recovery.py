"""Scoped recovery must not turn a failed task into business input/success."""

from types import SimpleNamespace

import numpy as np
import pytest

from action.basics import ScopedRecovery as recovery
from test_support.fakes import FakeClock, FakeBestResult, FakeRecognitionResult, TimedFakeJob


GUILD, HOME, UNKNOWN, RESULT, DIALOG = range(1, 6)


def node(name, *, completed=True, success=True, action="DoNothing"):
    return SimpleNamespace(name=name, completed=completed, recognition=None,
                           action=None if action is None else SimpleNamespace(success=success, action=action))


class Tasker:
    def __init__(self, clock, controller):
        self.clock, self._controller = clock, controller
        self.controller_gets = 0
        self.stop_requests = 0
        self._stopping = False
        self.nodes = {1: node("指挥局"), 2: node(recovery.GUILD_NODE),
                      3: node("", completed=False, action=None)}

    @property
    def controller(self):
        self.controller_gets += 1
        return self._controller

    @property
    def stopping(self):
        return self._stopping

    def get_node_detail(self, node_id):
        return self.nodes.get(node_id)

    def post_stop(self):
        self.stop_requests += 1
        return TimedFakeJob(self.clock, finish_at=float("inf"), job_id=999)


class Controller:
    def __init__(self, clock, pages):
        self.clock, self.pages = clock, list(pages)
        self.jobs = []
        self.captures = 0

    def post_screencap(self):
        self.captures += 1
        if self.jobs:
            return self.jobs.pop(0)
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        image = np.full((720, 1280, 3), page, np.uint8)
        return TimedFakeJob(self.clock, image=image, job_id=100 + self.captures)


class Context:
    def __init__(self, clock, pages):
        self.clock = clock
        self.controller = Controller(clock, pages)
        self.tasker = Tasker(clock, self.controller)
        self.detail = SimpleNamespace(task_id=10, entry="指挥局", node_id_list=[1, 2, 3],
                                      status=SimpleNamespace(running=True))
        self.recognitions = []
        self.actions = []
        self.action_result = SimpleNamespace(success=True)
        self.recognize_hook = None
        self.task_hook = None
        self.task_reads = 0
        self.back_box = (30, 12, 40, 45)
        self.pipeline = {
            recovery.RECOVERY_NODE: {"action": {"type": "Custom", "param": {"custom_action": "ScopedRecovery"}},
                                     "next": [], "on_error": []},
            recovery.BACK_NODE: {"recognition": {"type": "TemplateMatch", "param": {
                                    "roi": [27, 10, 51, 53], "template": "指挥局/返回.png"}},
                                 "action": {"type": "Click"}},
        }

    def get_node_data(self, name):
        return self.pipeline.get(name)

    def get_task_job(self):
        self.task_reads += 1
        if self.task_hook:
            self.task_hook(self)
        return SimpleNamespace(get=lambda wait=False: self.detail)

    def run_recognition(self, name, image):
        self.recognitions.append(name)
        if self.recognize_hook:
            value = self.recognize_hook(self, name, image)
            if value is not ...:
                return value
        page = int(image[0, 0, 0])
        hit = ((name == recovery.HOME_NODE and page == HOME)
               or (name == recovery.GUILD_NODE and page in {GUILD, RESULT, DIALOG})
               or (name == "关闭奖励_指挥局" and page == RESULT)
               or (name == "确定" and page == DIALOG)
               or (name == recovery.BACK_NODE and page in {GUILD, RESULT, DIALOG}))
        return FakeRecognitionResult(hit=hit, best_result=FakeBestResult(self.back_box) if hit else None)

    def run_action_direct(self, kind, parameters, *, box):
        self.actions.append((kind, parameters, box))
        if isinstance(self.action_result, Exception):
            raise self.action_result
        return self.action_result


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(recovery, "LoggerComponent", lambda _: SimpleNamespace(
        logger=SimpleNamespace(warning=lambda *_: None)))
    clock = FakeClock()
    action = recovery.ScopedRecovery()
    action._monotonic, action._sleep = clock.monotonic, clock.sleep

    def create(*pages):
        context = Context(clock, pages or [UNKNOWN])
        return action, context, SimpleNamespace(node_name=recovery.RECOVERY_NODE), clock
    return create


def test_safe_guild_navigation_uses_one_native_click_and_one_controller(setup):
    action, context, argv, _ = setup(GUILD, HOME)
    assert action.run(context, argv).success is False
    assert len(context.actions) == 1
    kind, parameters, box = context.actions[0]
    assert kind == "Click" and parameters.target == box == context.back_box
    assert context.controller.captures == 2 and context.tasker.controller_gets == 1
    assert context.tasker.stop_requests == 0
    assert action.last_result["outcome"] == "home_original_task_failed"
    assert action.last_result["cleanup"]["confirmed"] is True
    assert action.last_result["original_failure_cleanup_verified"] is False


def test_home_is_no_input_but_never_original_business_success(setup):
    action, context, argv, _ = setup(HOME)
    context.detail.entry = "历战映射"
    assert action.run(context, argv).success is False
    assert not context.actions and context.tasker.stop_requests == 0
    assert action.last_result["business_success"] is False


def test_page_transition_observes_without_blind_second_back(setup):
    action, context, argv, _ = setup(GUILD, GUILD, UNKNOWN, HOME)
    assert action.run(context, argv).success is False
    assert len(context.actions) == 1 and context.controller.captures == 4
    assert action.last_result["outcome"] == "home_original_task_failed"


def test_no_home_after_return_is_bounded_and_stops(setup):
    action, context, argv, _ = setup(GUILD)
    action.run(context, argv)
    assert len(context.actions) == 1 and context.controller.captures == 4
    assert context.tasker.stop_requests == 1
    assert action.last_result["reason"] == "home_after_return_unconfirmed"


@pytest.mark.parametrize("entry,page", [("指挥局", UNKNOWN), ("指挥局", RESULT), ("指挥局", DIALOG),
                                        ("厄愿潮声", GUILD), ("历战映射", UNKNOWN)])
def test_unknown_or_cross_mode_result_never_dispatches_business_input(setup, entry, page):
    action, context, argv, _ = setup(page)
    context.detail.entry = entry
    assert action.run(context, argv).success is False
    assert not context.actions and context.tasker.stop_requests == 1
    assert set(context.recognitions) <= {recovery.HOME_NODE, recovery.GUILD_NODE,
                                      recovery.BACK_NODE, "关闭奖励_指挥局", "确定"}


@pytest.mark.parametrize("name", ["检查指定按钮", "关闭奖励_指挥局", "返回", "返回_图标",
                                "确定", "领取藏品_厄愿潮声", "重启游戏"])
@pytest.mark.parametrize("completed,success", [(True, True), (False, False)])
def test_attempted_business_or_unknown_history_is_not_retried(setup, name, completed, success):
    action, context, argv, _ = setup(GUILD)
    context.tasker.nodes[2] = node(name, completed=completed, success=success)
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1


@pytest.mark.parametrize("fault", ["missing", "empty", "duplicate", "large", "unfinished", "failed", "placeholder-action", "blank-middle", "action-type"])
def test_incomplete_history_cannot_authorize_a_return(setup, fault):
    action, context, argv, _ = setup(GUILD)
    if fault == "missing": context.tasker.nodes.pop(2)
    if fault == "empty": context.detail.node_id_list = []
    if fault == "duplicate": context.detail.node_id_list = [1, 1]
    if fault == "large": context.detail.node_id_list = list(range(1, 514))
    if fault == "unfinished": context.tasker.nodes[2].completed = False
    if fault == "failed": context.tasker.nodes[2].action.success = False
    if fault == "placeholder-action": context.tasker.nodes[3].action = SimpleNamespace(success=True)
    if fault == "blank-middle": context.detail.node_id_list = [1, 3, 2]
    if fault == "action-type": context.tasker.nodes[2].action.action = "StopApp"
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1


@pytest.mark.parametrize("fault", ["missing", "wrong-node", "recovery-loop", "binding", "terminal"])
def test_invalid_binding_or_entry_does_not_get_controller(setup, fault):
    action, context, argv, _ = setup(GUILD)
    if fault == "missing": context.detail = None
    if fault == "wrong-node": argv.node_name = "重启游戏"
    if fault == "recovery-loop": context.pipeline[recovery.RECOVERY_NODE]["on_error"] = [recovery.RECOVERY_NODE]
    if fault == "binding": context.detail.task_id = 0
    if fault == "terminal": context.detail.status.running = False
    action.run(context, argv)
    assert not context.actions and context.tasker.controller_gets == 0
    assert context.tasker.stop_requests == 1


@pytest.mark.parametrize("fault", [None, False, "raise"])
def test_failed_native_return_is_not_followed_by_capture_or_more_input(setup, fault):
    action, context, argv, _ = setup(GUILD, HOME)
    context.action_result = (RuntimeError("native action failed") if fault == "raise" else
                             None if fault is None else SimpleNamespace(success=False))
    action.run(context, argv)
    assert len(context.actions) == 1 and context.controller.captures == 1
    assert context.tasker.stop_requests == 1
    assert action.last_result["cleanup"]["action_unconfirmed"] is True
    assert action.last_result["cleanup"]["confirmed"] is False and action._retained is not None


@pytest.mark.parametrize("fault", ["dtype", "empty", "failed", "pending"])
def test_bad_capture_never_uses_the_cached_image_or_replaces_controller(setup, fault):
    action, context, argv, clock = setup(GUILD)
    image = np.zeros((720, 1280, 3), np.float32 if fault == "dtype" else np.uint8)
    if fault == "empty": image = image[:0]
    job = TimedFakeJob(clock, image=image, success=fault != "failed",
                       finish_at=float("inf") if fault == "pending" else 0, job_id=200)
    context.controller.jobs = [job]
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1
    assert context.tasker.controller_gets == 1
    if fault in {"failed", "pending"}: assert job.get_calls == 0
    if fault == "pending":
        assert action.last_result["cleanup"]["pending_capture"] is True
        assert action._retained[2].pending_capture is job


@pytest.mark.parametrize("fault", ["stop", "late", "changed", "ids", "names"])
def test_last_sdk_read_cannot_authorize_late_or_stopped_return(setup, fault):
    action, context, argv, clock = setup(GUILD)
    def after_task(ctx):
        if ctx.task_reads == 2:
            if fault == "stop": ctx.tasker._stopping = True
            if fault == "late": clock.now += 1.1
            if fault == "changed": ctx.detail = SimpleNamespace(task_id=11, entry="厄愿潮声", node_id_list=[1, 2],
                                                               status=SimpleNamespace(running=True))
            if fault == "ids": ctx.detail = SimpleNamespace(task_id=10, entry="指挥局", node_id_list=[1, 2],
                                                           status=SimpleNamespace(running=True))
            if fault == "names": ctx.tasker.nodes[2] = node("检查指定按钮")
    context.task_hook = after_task
    action.run(context, argv)
    assert not context.actions
    assert context.tasker.stop_requests <= 1


@pytest.mark.parametrize("box", [(100, 200, 40, 40), (27, 10, 100, 10), (30, 12, 0, 20),
                               (30, 12, True, 20), "bad"])
def test_untrusted_return_box_does_not_touch_another_button(setup, box):
    action, context, argv, _ = setup(GUILD)
    context.back_box = box
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1


@pytest.mark.parametrize("roi", [[0, 0, 1280, 720], [26, 10, 52, 53],
                               [27, 10, 51, True], None])
def test_changed_return_roi_does_not_expand_the_proven_navigation_policy(setup, roi):
    action, context, argv, _ = setup(GUILD)
    context.pipeline[recovery.BACK_NODE]["recognition"]["param"]["roi"] = roi
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1
    assert action.last_result["reason"] == "back_roi_unknown"


@pytest.mark.parametrize("template", ["指挥局/返回.png", ["指挥局/返回.png"]])
def test_original_return_template_accepts_source_and_sdk_normalized_forms(setup, template):
    action, context, argv, _ = setup(GUILD, HOME)
    context.pipeline[recovery.BACK_NODE]["recognition"]["param"]["template"] = template
    action.run(context, argv)
    assert len(context.actions) == 1 and context.tasker.stop_requests == 0
    assert action.last_result["business_success"] is False


@pytest.mark.parametrize("template", ["other.png", ["other.png"],
                                     ["指挥局/返回.png", "other.png"], [], None, True,
                                     {"template": "指挥局/返回.png"}, ("指挥局/返回.png",)])
def test_replaced_mixed_or_malformed_return_template_never_authorizes_input(setup, template):
    action, context, argv, _ = setup(GUILD)
    context.pipeline[recovery.BACK_NODE]["recognition"]["param"]["template"] = template
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1
    assert action.last_result["reason"] == "back_template_changed"


def test_missing_recognition_result_is_unknown_not_a_negative(setup):
    action, context, argv, _ = setup(GUILD)
    context.recognize_hook = lambda ctx, name, image: None if name == "关闭奖励_指挥局" else ...
    action.run(context, argv)
    assert not context.actions and context.tasker.stop_requests == 1


def test_old_pending_marker_is_unchanged_and_uncertain_controller_retained(setup, tmp_path):
    action, context, argv, _ = setup(UNKNOWN)
    marker = tmp_path / "existing-purchase-pending.json"
    original = b'{"pending":true,"amount":1}\n'
    marker.write_bytes(original)
    action.run(context, argv)
    retained = action._retained
    assert marker.read_bytes() == original and retained is not None
    other = Context(context.clock, [GUILD, HOME])
    assert action.run(other, argv).success is False
    assert action._retained is retained and other.tasker.controller_gets == 0
    assert not other.actions
