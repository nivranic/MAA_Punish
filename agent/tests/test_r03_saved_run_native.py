"""A saved matrix run is resumed without clicking the destructive end button."""
from threading import Timer

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.PPOverride import PPOverride
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


RESUME = "矩阵循生_继续演算"
BUTTON = "矩阵循生_继续演算按钮"
END = "终止_矩阵循生"
MARKER = "矩阵循生_未完演算标记"
START = "启程_矩阵循生"
PARENTS = ("矩阵循生", "迭变投影_矩阵循生", "事件完成_结束_二次确认_矩阵循生")


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", PARENTS)
@pytest.mark.parametrize("continue_visible,end_visible", [(True, True), (False, True), (True, False)])
def test_saved_run_requires_both_home_controls_and_never_terminates(
    tmp_path, language, entry, continue_visible, end_visible,
):
    _run_home(tmp_path, language, entry, continue_visible, end_visible, False)


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
def test_fresh_run_still_uses_departure_button(tmp_path, language):
    _run_home(tmp_path, language, "矩阵循生", False, False, True)


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("complete", [True, False])
def test_unknown_event_preserves_save_and_visits_failure_cleanup(tmp_path, language, complete):
    entry, preserve = "未知事件_矩阵循生", "矩阵循生_保留未完演算退出"
    clicks = []

    class Capture(Controller):
        def click(self, *point):
            clicks.append(point)
            return False

    class Page(CustomRecognition):
        def analyze(self, ctx, argv):
            if argv.node_name in {END, MARKER}:
                return (940, 625, 70, 60)
            if argv.node_name == BUTTON and complete:
                return (1120, 625, 130, 60)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("UnknownSavedPage", Page())
    patch = {}
    for item in resource.get_node_data(entry)["next"]:
        if item["name"] not in {END, preserve}:
            patch[item["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=120, on_error=[])
    # Keep the production action: replacing it masked the unregistered
    # PrintInfo call before the save-preservation branch could run.
    patch[entry] = {**timing, "recognition": "DirectHit"}
    for name in (BUTTON, END, MARKER):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {"custom_recognition": "UnknownSavedPage"}}}
    if resource.get_node_data(preserve) is not None:
        patch[preserve] = timing
    patch["任务失败优雅退出"] = {**timing, "recognition": "DirectHit", "action": "StopTask", "next": []}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    timer = Timer(10, tasker.post_stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, patch).wait()
        assert not clicks
        assert job.succeeded is complete
        history = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in history.node_id_list]
        assert ("任务失败优雅退出" in names) is complete
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)


def _run_home(tmp_path, language, entry, continue_visible, end_visible, fresh):
    clicked, wrong, expired = [], [], []
    state = {"home": True}
    destination = (1120, 625, 130, 60)

    class Capture(Controller):
        def click(self, x, y):
            if not (destination[0] <= x < destination[0] + destination[2]
                    and destination[1] <= y < destination[1] + destination[3]):
                wrong.append((x, y))
                return False
            clicked.append("depart" if fresh else "resume")
            state["home"] = False
            return True

    class Page(CustomRecognition):
        def analyze(self, ctx, argv):
            if not state["home"]:
                return (10, 10, 10, 10) if argv.node_name == "SavedRunEnd" else None
            if argv.node_name == BUTTON and continue_visible:
                return destination
            if argv.node_name in {END, MARKER} and end_visible:
                return (940, 625, 70, 60)
            if argv.node_name == START and fresh:
                return destination
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_action("PPOverride", PPOverride())
    assert resource.register_custom_recognition("SavedRunPage", Page())
    root = resource.get_node_data(entry)
    keep = {RESUME, END, START}
    patch = {}
    for item in root["next"]:
        name = (item if isinstance(item, str) else item["name"]).removeprefix("[JumpBack]")
        if name not in keep:
            patch[name] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=120, on_error=[])
    patch[entry] = {**timing, "recognition": "DirectHit"}
    for name in (BUTTON, END, MARKER, START, "SavedRunEnd"):
        patch.setdefault(name, {}).update(
            **timing, recognition={"type": "Custom", "param": {"custom_recognition": "SavedRunPage"}},
        )
    patch["SavedRunEnd"].update(action="StopTask", next=[])
    for name in (RESUME, START):
        if resource.get_node_data(name) is not None:
            patch.setdefault(name, {}).update(**timing, next=["SavedRunEnd"])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(10, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, patch).wait()
        succeeds = fresh or (continue_visible and end_visible)
        assert not wrong and not expired, wrong
        assert job.succeeded is succeeds
        assert clicked == (["depart" if fresh else "resume"] if succeeds else [])
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
