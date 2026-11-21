"""The abyss-echo boss story only advances through observed turn, option, nod and forward clicks."""
import re
from threading import Timer

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "BOSS战_矩阵循生"
EVENT = "深渊余音_矩阵循生"
TITLE = "深渊余音_标题"
TURN = "深渊余音_转身按钮"
OPTION = "深渊余音_牵手选项"
NOD = "深渊余音_点头按钮"
FORWARD = "深渊余音_前往按钮"
SELECT = "深渊余音_选择"
CONFIRM = "深渊余音_确认"
GOFORWARD = "深渊余音_前往"
BATTLE = "矩阵循生_战斗就绪"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "missing_title", "missing_turn", "missing_option", "other_option", "missing_nod", "missing_forward"])
def test_abyss_echo_advances_stage_by_stage(tmp_path, language, case):
    resource = load_resource(tmp_path, language)
    for node in (EVENT, CONFIRM, GOFORWARD):
        assert resource.get_node_data(node) is not None
    # page = number of clicks performed: 0 map, 1 turn scene, 2/3 nod dialogue,
    # 4 the zero countdown page, 5 battle UI. The dialogue stays visually
    # identical until the nod lands, so pages 2 and 3 both expose the option
    # row and the nod button.
    state, clicks = {"page": 0}, []
    traditional = language == "zh_TW"
    title_text = "深淵餘音" if traditional else "深渊余音"
    turn_text = "轉身" if traditional else "转身"
    option_text = "握緊潔塔薇的手" if traditional else "握紧洁塔薇的手"
    nod_text = "點頭" if traditional else "点头"
    forward_text = "前往" if traditional else "前■"
    patterns = {}
    for node in (TITLE, TURN, OPTION, NOD, FORWARD):
        value = resource.get_node_data(node)["recognition"]["param"]["expected"]
        patterns[node] = [value] if isinstance(value, str) else value

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] == 0:
                # The boss node template click lands inside the BOSS战 recognition roi.
                assert 285 <= x < 1197 and 130 <= y < 241
            elif state["page"] == 2:
                # The page-2 click is the option row selection (选择 fires here).
                assert 700 <= x < 950 and 398 <= y < 438
            else:
                assert 960 <= x < 1080 and 605 <= y < 675
            clicks.append(state["page"])
            state["page"] += 1
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == ENTRY and page == 0:
                return (596, 186, 34, 40)
            if name == BATTLE and page == 5:
                return (1003, 627, 163, 55)
            text = None
            if name == TITLE and page in {1, 2, 3, 4} and case != "missing_title":
                text, box = title_text, (665, 106, 159, 41)
            elif name == TURN and page == 1 and case != "missing_turn":
                text, box = turn_text, (990, 624, 52, 31)
            elif name == OPTION and page in {2, 3} and case != "missing_option":
                text, box = (option_text if case != "other_option" else "离开"), (727, 407, 76, 27)
            elif name == NOD and page in {2, 3} and case != "missing_nod":
                text, box = nod_text, (992, 624, 51, 31)
            elif name == FORWARD and page == 4 and case != "missing_forward":
                text, box = forward_text, (992, 626, 47, 29)
            else:
                return None
            return box if any(re.search(pattern, text) for pattern in patterns[name]) else None

    assert resource.register_custom_recognition("AbyssEchoPage", Page())
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch = {}
    for parent in (ENTRY, BATTLE):
        for child in resource.get_node_data(parent)["next"]:
            patch.setdefault(child["name"], {"enabled": False})
    # Keep the real boss-node Click; only replace the template recognition.
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "AbyssEchoPage",
    }}}
    for name in (EVENT, SELECT, CONFIRM, GOFORWARD):
        patch[name] = dict(timing)
    for name in (TITLE, TURN, OPTION, NOD, FORWARD, BATTLE):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "AbyssEchoPage",
        }}}
    patch[BATTLE].update(action="StopTask", next=[])
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
            assert job.succeeded and state["page"] == 5 and clicks == [0, 1, 2, 3, 4]
        elif case == "missing_nod":
            # Whether the failed chain bubbles up or the parent list resumes, the task fails.
            assert job.failed and len(clicks) >= 2 and clicks[:2] == [0, 1] and state["page"] >= 2
        elif case in {"missing_title", "missing_turn"}:
            assert job.failed and clicks == [0] and state["page"] == 1
        elif case in {"missing_option", "other_option"}:
            assert job.failed and clicks == [0, 1] and state["page"] == 2
        else:
            assert job.failed and clicks == [0, 1, 2, 3] and state["page"] == 4
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)


def test_abyss_echo_edges_are_wired_into_the_boss_battle_chain(tmp_path):
    resource = load_resource(tmp_path, "base")
    nxt = [child["name"] for child in resource.get_node_data(ENTRY)["next"]]
    assert "深渊余音_矩阵循生" in nxt and "深渊余音_选择" in nxt and "深渊余音_前往" in nxt
    # The toast evidence forbids nodding without selecting: 确认 must only be
    # reachable through the 选择 chain, never from the parallel list.
    assert "深渊余音_确认" not in nxt
    assert [child["name"] for child in resource.get_node_data(SELECT)["next"]] == ["深渊余音_确认"]
    for edge in ("深渊余音_矩阵循生", "深渊余音_选择", "深渊余音_前往"):
        assert edge not in [child["name"] for child in resource.get_node_data("战斗事件_矩阵循生")["next"]]
