"""The rest event enters its observed button without treating unknown pages as rest."""
import re
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
EVENT = "休憩之地_矩阵循生"
TITLE = "休憩之地_标题"
BUTTON = "休憩之地_前往文字"
ADVANCE = "休憩之地_前往"
OPTION = "休憩之地_直接出发文字"
SELECT = "休憩之地_直接出发"
CONFIRM = "决定_矩阵循生"
TRANSITION = "转场_矩阵循生"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("missing", [None, "title", "button", "option", "confirm", "resume_result"])
@pytest.mark.parametrize("chapter", [False, True])
def test_rest_enters_only_after_title_and_button_then_hands_back_to_map(tmp_path, language, missing, chapter):
    resource = load_resource(tmp_path, language)
    assert resource.get_node_data(EVENT) is not None
    state, clicks, expired = {"page": 3 if missing == "resume_result" else 0, "entered": False}, [], []

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] == 4:
                assert (x, y) == (600, 10)
            elif state["page"] == 1:
                assert 730 <= x < 980 and 450 <= y < 485
            else:
                assert state["page"] in {0, 2, 3} and 990 <= x < 1050 and 620 <= y < 660
            state["page"] += 1
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == ENTRY and not state["entered"]:
                return (990, 620, 60, 40)
            if argv.node_name == TITLE and state["page"] < 4 and missing != "title":
                return (670, 110, 150, 35)
            if argv.node_name == BUTTON and state["page"] in {0, 3} and missing != "button":
                text = "前往" if state["page"] == 0 else ("出發" if language == "zh_TW" else "出发")
                patterns = resource.get_node_data(BUTTON)["recognition"]["param"]["expected"]
                patterns = [patterns] if isinstance(patterns, str) else patterns
                if any(re.search(pattern, text) for pattern in patterns):
                    return (990, 620, 60, 40)
            if argv.node_name == OPTION and state["page"] == 1 and missing != "option":
                return (730, 450, 250, 35)
            if argv.node_name == CONFIRM and state["page"] == 2 and missing != "confirm":
                return (990, 620, 60, 40)
            if argv.node_name == TRANSITION and state["page"] == 4 and chapter:
                return (1020, 650, 180, 45)
            if argv.node_name == MAP and state["page"] == (5 if chapter else 4):
                return (1003, 627, 163, 55)
            return None

    class Begin(CustomAction):
        def run(self, context, argv):
            state["entered"] = True
            return True

    assert resource.register_custom_recognition("RestStoryPage", Page())
    assert resource.register_custom_action("BeginRestStory", Begin())
    allowed = {EVENT, ADVANCE, SELECT, CONFIRM, TRANSITION, MAP}
    patch = {}
    for parent in (ENTRY, EVENT):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in allowed:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "RestStoryPage",
    }}, "action": {"type": "Custom", "param": {"custom_action": "BeginRestStory"}}}
    for name in (EVENT, ADVANCE, SELECT):
        patch[name] = dict(timing)
    for name in (TITLE, BUTTON, OPTION, CONFIRM, TRANSITION, MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "RestStoryPage",
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
        assert job.succeeded is (missing in {None, "resume_result"})
        assert len(clicks) == {None: 4 + chapter, "title": 0, "button": 0, "option": 1, "confirm": 2, "resume_result": 1 + chapter}[missing]
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
