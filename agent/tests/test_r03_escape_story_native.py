"""The escape event must advance its opening before selecting the rat option."""
import re
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
EVENT = "逃出生天_矩阵循生"
ADVANCE = "逃出生天_对话2"
CHOICE = "逃出生天_对话"
CONFIRM = "决定_矩阵循生"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "missing_title", "missing_button", "unrelated_button"])
@pytest.mark.parametrize("companion", ["rat", "teammate"])
def test_escape_surrounds_then_uses_existing_choice_and_reward_flow(tmp_path, language, case, companion):
    resource = load_resource(tmp_path, language)
    state, clicks, expired = {"page": 0, "entered": False}, [], []
    patterns = resource.get_node_data(ADVANCE)["recognition"]["param"]["expected"]
    if isinstance(patterns, str):
        patterns = [patterns]
    surround = "環顧四周" if language == "zh_TW" else "环顾四周"
    choice_patterns = resource.get_node_data(CHOICE)["recognition"]["param"]["expected"]
    if isinstance(choice_patterns, str):
        choice_patterns = [choice_patterns]
    choice_texts = {"rat": ("给巨鼠些吃的", "給巨鼠些吃的"), "teammate": ("让她试试", "讓她試試")}
    choice_text = choice_texts[companion][language == "zh_TW"]
    rope_text = "用盡全力拉回繩子" if language == "zh_TW" else "用尽全力拉回绳子"
    choice_pages = {1, 3} if companion == "teammate" else {1}
    confirm_pages = {page + 1 for page in choice_pages}
    final_page = 6 if companion == "teammate" else 4
    result_text = ("安撫" if language == "zh_TW" else "安抚") if companion == "teammate" else "收下"

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] in choice_pages:
                assert 730 <= x < 900 and 360 <= y < 395
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
            if name == EVENT and page < final_page and case != "missing_title":
                return (670, 110, 150, 35)
            if name == ADVANCE and page in {0, final_page - 1} and case != "missing_button":
                text = "支付50" if case == "unrelated_button" else (surround if page == 0 else result_text)
                if any(re.search(pattern, text) for pattern in patterns):
                    return (990, 620, 60, 40)
            if name == CHOICE and page in choice_pages:
                text = choice_text if page == 1 else rope_text
                if any(re.search(pattern, text) for pattern in choice_patterns):
                    return (730, 360, 170, 35)
            if name == CONFIRM and page in confirm_pages:
                return (990, 620, 60, 40)
            if name == MAP and page == final_page:
                return (1003, 627, 163, 55)
            return None

    class Begin(CustomAction):
        def run(self, context, argv):
            state["entered"] = True
            return True

    assert resource.register_custom_recognition("EscapeStoryPage", Page())
    assert resource.register_custom_action("BeginEscapeStory", Begin())
    allowed = {EVENT, ADVANCE, CHOICE, CONFIRM, MAP}
    patch = {}
    for parent in (ENTRY, EVENT):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in allowed:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "EscapeStoryPage",
    }}, "action": {"type": "Custom", "param": {"custom_action": "BeginEscapeStory"}}}
    for name in allowed:
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "EscapeStoryPage",
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
            assert job.succeeded and state["page"] == final_page
            assert clicks == list(range(final_page))
        else:
            assert job.failed and clicks == [] and state["page"] == 0
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
