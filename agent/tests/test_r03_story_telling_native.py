"""The story-telling event only advances through the observed option and exit texts."""
import re
from threading import Timer

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
EVENT = "洁塔薇讲故事_矩阵循生"
TITLE = "洁塔薇讲故事_标题"
OPTION = "洁塔薇讲故事_选项"
FORWARD = "洁塔薇讲故事_推进"
SELECT = "洁塔薇讲故事_接受选项"
LEAVE = "洁塔薇讲故事_推进动作"
CONFIRM = "决定_矩阵循生"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "missing_title", "missing_option", "other_option"])
def test_story_telling_uses_observed_option_then_decide(tmp_path, language, case):
    resource = load_resource(tmp_path, language)
    assert resource.get_node_data(EVENT) is not None
    state, clicks = {"page": 0}, []
    traditional = language == "zh_TW"
    visible = {
        TITLE: "潔塔薇講故事" if traditional else "洁塔薇讲故事",
        OPTION: "關於旅行者的故事" if traditional else "关于旅行者的故事",
    }
    patterns = {}
    for node in (TITLE, OPTION, FORWARD):
        value = resource.get_node_data(node)["recognition"]["param"]["expected"]
        patterns[node] = [value] if isinstance(value, str) else value

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] == 1:
                assert 650 <= x < 1150 and 290 <= y < 550
            else:
                assert 960 <= x < 1200 and 600 <= y < 690
            clicks.append(state["page"])
            state["page"] += 1
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == ENTRY and state["page"] == 0:
                return (990, 620, 60, 40)
            if name == TITLE and page < 4 and case != "missing_title":
                text, box = visible[name], (670, 110, 190, 35)
            elif name == OPTION and page == 1 and case != "missing_option":
                text = "离开夜风" if case == "other_option" else visible[name]
                box = (700, 300, 250, 35)
            elif name == FORWARD and page == 3:
                text, box = ("離開" if traditional else "离开"), (990, 620, 60, 40)
            elif name == CONFIRM and page == 2:
                return (990, 620, 60, 40)
            elif name == MAP and page == 4:
                return (1003, 627, 163, 55)
            else:
                return None
            return box if any(re.search(pattern, text) for pattern in patterns.get(name, [])) else None

    assert resource.register_custom_recognition("StoryTellingPage", Page())
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch = {}
    for parent in (ENTRY, EVENT):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in {EVENT, SELECT, LEAVE, MAP}:
                patch[child["name"]] = {"enabled": False}
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "StoryTellingPage",
    }}}
    # The chain (option → decide → exit → map) crosses 决定's post_wait_freezes,
    # which polls real frames for ~1-2s, so the dispatcher window gets 8000ms.
    for name in (SELECT, LEAVE):
        patch[name] = dict(timing)
    patch[EVENT] = {**timing, "timeout": 8000}
    for name in (TITLE, OPTION, FORWARD, CONFIRM, MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "StoryTellingPage",
        }}}
    patch[MAP].update(action="StopTask", next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        tasker.post_stop()

    timer = Timer(10, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(ENTRY, patch).wait()
        assert not timer.finished.is_set()
        if case == "valid":
            assert job.succeeded and state["page"] == 4, f"clicks={clicks} page={state['page']}"
            assert clicks == [0, 1, 2, 3], f"clicks={clicks}"
        elif case in {"missing_option", "other_option"}:
            assert job.failed and clicks == [0] and state["page"] == 1
        else:
            assert job.failed and clicks == [0] and state["page"] == 1
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)


def test_story_telling_is_wired_before_the_unknown_fallback(tmp_path):
    resource = load_resource(tmp_path, "base")
    dispatch = [child["name"] for child in resource.get_node_data(ENTRY)["next"]]
    assert "洁塔薇讲故事_矩阵循生" in dispatch and "未知事件_矩阵循生" in dispatch
    assert dispatch.index("洁塔薇讲故事_矩阵循生") < dispatch.index("未知事件_矩阵循生")
    assert "洁塔薇讲故事_矩阵循生" not in [
        child["name"] for child in resource.get_node_data("战斗事件_矩阵循生")["next"]
    ]
