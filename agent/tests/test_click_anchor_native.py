"""The real navigation anchor must report a rejected controller action to Maa."""

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.ClickAnchor import ClickAnchor
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("accepted", [True, False])
def test_open_battle_does_not_report_a_rejected_anchor_click_as_success(tmp_path, accepted):
    entry = "打开战斗"
    successor = "ClickAnchorTestNavigationSuccess"
    attempts = []

    class BattleMenu(CustomRecognition):
        def analyze(self, context, argv):
            return (100, 200, 41, 21)

    class Capture(Controller):
        def click(self, x, y):
            attempts.append((x, y))
            return accepted

    resource = load_resource(tmp_path)
    controller, tasker = Capture(), Tasker()
    assert resource.register_custom_action("ClickAnchor", ClickAnchor())
    assert resource.register_custom_recognition("ClickAnchorTestBattleMenu", BattleMenu())
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        # Leave the real custom action and its signed right-edge offset in place.
        # A synthetic, always-ready next node exposes a falsely successful result.
        job = tasker.post_task(entry, {
            "主界面_战斗节点": {
                "recognition": {
                    "type": "Custom",
                    "param": {"custom_recognition": "ClickAnchorTestBattleMenu"},
                },
            },
            entry: {
                "pre_delay": 0,
                "post_delay": 0,
                "post_wait_freezes": 0,
                "timeout": 1000,
                "next": [successor],
                "on_error": [],
            },
            successor: {"recognition": "DirectHit", "action": "DoNothing", "post_delay": 0},
        }).wait()
        details = tasker.get_task_detail(job.job_id)
        visited = [tasker.get_node_detail(node).name for node in details.node_id_list]
        assert attempts == [(121, 210)]
        assert job.succeeded is accepted
        assert (successor in visited) is accepted
    finally:
        stop_and_wait_idle(tasker)
