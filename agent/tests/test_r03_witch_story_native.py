"""The witch story advances only observed choices and can resume its result page."""
import re
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
EVENT = "树下女巫_矩阵循生"
TITLE = "树下女巫_标题"
OPTION = "树下女巫_好意选项"
SELECT = "树下女巫_接受好意"
LEAVE_TEXT = "树下女巫_推进文字"
LEAVE = "树下女巫_推进"
CONFIRM = "决定_矩阵循生"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "missing_title", "missing_option", "other_option", "missing_button", "resume_result", "resume_reward"])
def test_witch_story_uses_observed_choice_and_resumes_result(tmp_path, language, case):
    resource = load_resource(tmp_path, language)
    assert resource.get_node_data(EVENT) is not None
    state, clicks, expired = {"page": 4 if case == "resume_reward" else (3 if case == "resume_result" else 0), "entered": False}, [], []
    traditional = language == "zh_TW"
    visible = {
        TITLE: "樹下女巫 怪誕的信念" if traditional else "树下女巫 怪诞的信念",
        OPTION: "接受少女的好意",
        LEAVE_TEXT: "離開" if traditional else "离开",
    }
    patterns = {}
    for node in visible:
        value = resource.get_node_data(node)["recognition"]["param"]["expected"]
        patterns[node] = [value] if isinstance(value, str) else value

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] == 1:
                assert 730 <= x < 980 and 360 <= y < 395
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
            if name == TITLE and page < 5 and case != "missing_title":
                text, box = visible[name], (670, 110, 190, 35)
            elif name == OPTION and page == 1 and case != "missing_option":
                text = "掀开少女的面纱" if case == "other_option" else visible[name]
                box = (730, 360, 250, 35)
            elif name == LEAVE_TEXT and page in {0, 3, 4} and case != "missing_button":
                text, box = ("上前" if page == 0 else (("繼續前行" if traditional else "继续前行") if page == 4 else visible[name])), (990, 620, 60, 40)
            elif name == CONFIRM and page == 2:
                return (990, 620, 60, 40)
            elif name == MAP and page == 5:
                return (1003, 627, 163, 55)
            else:
                return None
            return box if any(re.search(pattern, text) for pattern in patterns[name]) else None

    class Begin(CustomAction):
        def run(self, context, argv):
            state["entered"] = True
            return True

    assert resource.register_custom_recognition("WitchStoryPage", Page())
    assert resource.register_custom_action("BeginWitchStoryPage", Begin())
    allowed = {EVENT, SELECT, LEAVE, MAP}
    patch = {}
    for parent in (ENTRY, EVENT):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in allowed:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "WitchStoryPage",
    }}, "action": {"type": "Custom", "param": {"custom_action": "BeginWitchStoryPage"}}}
    for name in (EVENT, SELECT, LEAVE):
        patch[name] = dict(timing)
    for name in (TITLE, OPTION, LEAVE_TEXT, CONFIRM, MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "WitchStoryPage",
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
        if case in {"valid", "resume_result", "resume_reward"}:
            assert job.succeeded and state["page"] == 5
            assert clicks == ([4] if case == "resume_reward" else ([3, 4] if case == "resume_result" else [0, 1, 2, 3, 4]))
        elif case in {"missing_option", "other_option"}:
            assert job.failed and clicks == [0] and state["page"] == 1
        else:
            assert job.failed and clicks == [] and state["page"] == 0
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
