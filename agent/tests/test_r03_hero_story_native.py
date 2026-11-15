"""Observed short stories keep their own choices and resume their reward pages."""
import re
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
CONFIRM = "决定_矩阵循生"
MAP = "肉鸽内部"
STORIES = {
    "hero": {
        "prefix": "英雄假象", "option": "请求选项", "select": "答应请求",
        "title": ("英雄假象 戏谑的厄运", "英雄假象 戲謔的厄運"),
        "choice": ("答应他的请求", "答應他的請求"),
        "opening": ("跟随他", "跟隨他"), "other_option": "拒绝他的请求",
    },
    "knowledge": {
        "prefix": "知识假象", "option": "芯片选项", "select": "调查芯片",
        "title": ("知识假象 虚伪的天空", "知識假象 虛偽的天空"),
        "choice": ("调查机器人的芯片", "調查機器人的晶片"),
        "opening": ("继续", "繼續"), "other_option": "调查观星台的系统",
    },
}


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "missing_title", "other_title", "missing_option", "other_option", "missing_button", "resume_result"])
@pytest.mark.parametrize("story", STORIES)
def test_short_story_uses_observed_choice_and_resumes_reward(tmp_path, language, case, story):
    spec = STORIES[story]
    prefix = spec["prefix"]
    EVENT, TITLE = prefix + "_矩阵循生", prefix + "_标题"
    OPTION, SELECT = prefix + "_" + spec["option"], prefix + "_" + spec["select"]
    LEAVE_TEXT, LEAVE = prefix + "_推进文字", prefix + "_推进"
    resource = load_resource(tmp_path, language)
    assert resource.get_node_data(EVENT) is not None
    state, clicks, expired = {"page": 3 if case == "resume_result" else 0, "entered": False}, [], []
    traditional = language == "zh_TW"
    visible = {
        TITLE: spec["title"][traditional],
        OPTION: spec["choice"][traditional],
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
            if name == TITLE and page < 4 and case != "missing_title":
                text, box = visible[name], (670, 110, 190, 35)
                if case == "other_title":
                    text = STORIES["knowledge" if story == "hero" else "hero"]["title"][traditional]
            elif name == OPTION and page == 1 and case != "missing_option":
                text = spec["other_option"] if case == "other_option" else visible[name]
                box = (730, 360, 250, 35)
            elif name == LEAVE_TEXT and page in {0, 3} and case != "missing_button":
                text, box = (spec["opening"][traditional] if page == 0 else visible[name]), (990, 620, 60, 40)
            elif name == CONFIRM and page == 2:
                return (990, 620, 60, 40)
            elif name == MAP and page == 4:
                return (1003, 627, 163, 55)
            else:
                return None
            return box if any(re.search(pattern, text) for pattern in patterns[name]) else None

    class Begin(CustomAction):
        def run(self, context, argv):
            state["entered"] = True
            return True

    assert resource.register_custom_recognition("HeroStoryPage", Page())
    assert resource.register_custom_action("BeginHeroStoryPage", Begin())
    allowed = {EVENT, SELECT, LEAVE, MAP}
    patch = {}
    for parent in (ENTRY, EVENT):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in allowed:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "HeroStoryPage",
    }}, "action": {"type": "Custom", "param": {"custom_action": "BeginHeroStoryPage"}}}
    for name in (EVENT, SELECT, LEAVE):
        patch[name] = dict(timing)
    for name in (TITLE, OPTION, LEAVE_TEXT, CONFIRM, MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "HeroStoryPage",
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
        if case in {"valid", "resume_result"}:
            assert job.succeeded and state["page"] == 4
            assert clicks == ([3] if case == "resume_result" else [0, 1, 2, 3])
        elif case in {"missing_option", "other_option"}:
            assert job.failed and clicks == [0] and state["page"] == 1
        else:
            assert job.failed and clicks == [] and state["page"] == 0
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
