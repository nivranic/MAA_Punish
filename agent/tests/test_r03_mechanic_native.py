"""An observed mechanic event can decline an exchange and continue the run."""
import re
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
EVENT = "机械维修师_矩阵循生"
TITLE = "机械维修师_标题"
OPTION = "机械维修师_拒绝选项"
SELECT = "机械维修师_拒绝交换"
LEAVE_TEXT = "机械维修师_离开文字"
LEAVE = "机械维修师_离开"
CONFIRM = "决定_矩阵循生"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "missing_title", "missing_option", "purchase_only", "missing_confirm"])
def test_mechanic_declines_only_explicit_option_and_returns_to_map(tmp_path, language, case):
    resource = load_resource(tmp_path, language)
    assert resource.get_node_data(EVENT) is not None
    state, clicks, expired = {"page": 0, "entered": False}, [], []
    traditional = language == "zh_TW"
    visible = {
        TITLE: "機械維修師" if traditional else "机械维修师",
        OPTION: "拒絕交換，就此離開" if traditional else "拒绝交换，就此离开",
        LEAVE_TEXT: "離開" if traditional else "离开",
    }
    patterns = {}
    for node in visible:
        value = resource.get_node_data(node)["recognition"]["param"]["expected"]
        patterns[node] = [value] if isinstance(value, str) else value

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] == 0:
                assert 730 <= x < 980 and 450 <= y < 485
            else:
                assert 990 <= x < 1050 and 620 <= y < 660
            clicks.append(state["page"])
            state["page"] += 1
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == ENTRY and not state["entered"]:
                return (990, 620, 60, 40)
            if name == TITLE and page < 3 and case != "missing_title":
                text, box = visible[name], (670, 110, 190, 35)
            elif name == OPTION and page == 0 and case != "missing_option":
                text = "拿出一些算力单元给他" if case == "purchase_only" else visible[name]
                box = (730, 450, 250, 35)
            elif name == LEAVE_TEXT and page == 2:
                text, box = visible[name], (990, 620, 60, 40)
            elif name == CONFIRM and page == 1 and case != "missing_confirm":
                return (990, 620, 60, 40)
            elif name == MAP and page == 3:
                return (1003, 627, 163, 55)
            else:
                return None
            return box if any(re.search(pattern, text) for pattern in patterns[name]) else None

    class Begin(CustomAction):
        def run(self, context, argv):
            state["entered"] = True
            return True

    assert resource.register_custom_recognition("MechanicPage", Page())
    assert resource.register_custom_action("BeginMechanicPage", Begin())
    allowed = {EVENT, SELECT, LEAVE, MAP}
    patch = {}
    for parent in (ENTRY, EVENT):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in allowed:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "MechanicPage",
    }}, "action": {"type": "Custom", "param": {"custom_action": "BeginMechanicPage"}}}
    for name in (EVENT, SELECT, LEAVE):
        patch[name] = dict(timing)
    for name in (TITLE, OPTION, LEAVE_TEXT, CONFIRM, MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "MechanicPage",
        }}}
    patch[MAP].update(action="StopTask", next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(10, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(ENTRY, patch).wait()
        assert not expired
        if case == "valid":
            assert job.succeeded and state["page"] == 3
            assert clicks == [0, 1, 2]
        elif case == "missing_confirm":
            assert job.failed and clicks == [0] and state["page"] == 1
        else:
            assert job.failed and clicks == [] and state["page"] == 0
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
