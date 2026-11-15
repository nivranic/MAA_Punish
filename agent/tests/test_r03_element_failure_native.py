"""A nested element failure must reach the parent task's retained failure history."""
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("failed_node", ["打开暗属性", "确认进入暗属性"])
def test_nested_filter_failure_is_visible_in_parent_history(tmp_path, language, failed_node):
    observed = []

    class Missing(CustomRecognition):
        def analyze(self, context, argv):
            return None if argv.node_name == failed_node else (10, 10, 10, 10)

    class Parent(CustomAction):
        def run(self, context, argv):
            selector = RoleSelection()
            selector._get_role_element_task = lambda *_: "打开暗属性"
            result = R03Recruitment(selector)._open_element(context, {}, "synthetic")
            observed.append(result)
            return result

    class Capture(Controller):
        def click(self, *_): raise AssertionError("Failure propagation sends no input")

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("MissingElement", Missing())
    assert resource.register_custom_action("Parent", Parent())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []
    def stop():
        expired.append(True)
        tasker.post_stop()
    watchdog = Timer(15, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        override = {
            name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "MissingElement"}},
                   "action": {"type": "DoNothing"}, "timeout": 30, "rate_limit": 1,
                   "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0}
            for name in ("打开暗属性", "确认进入暗属性")
        }
        override.update({
            "Parent": {"recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "Parent"}},
                       "on_error": ["任务失败优雅退出"], "next": [], "pre_delay": 0, "post_delay": 0},
            "任务失败优雅退出": {"recognition": "DirectHit", "action": {"type": "DoNothing"},
                         "next": ["FailureEnd"], "on_error": [], "pre_delay": 0, "post_delay": 0},
            "FailureEnd": {"recognition": "DirectHit", "action": {"type": "StopTask"}, "next": [], "on_error": []},
        })
        job = tasker.post_task("Parent", override).wait()
        assert not expired and observed == [False]
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(node_id).name for node_id in detail.node_id_list]
        # Android already treats this marker as failure, even when StopTask
        # gives the native task a Succeeded status. It must belong to the parent.
        assert "任务失败优雅退出" in names and names[-1] == "FailureEnd"
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
