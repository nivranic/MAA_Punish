"""The automation workshop keeps equipment and leaves via the existing transfer flow."""
import re
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ENTRY = "事件确定_矩阵循生"
EVENT = "自动化工坊_矩阵循生"
TITLE = "自动化工坊_标题"
OPTION = "自动化工坊_休整选项"
SELECT = "自动化工坊_原地休整"
TEXT = "自动化工坊_推进文字"
ADVANCE = "自动化工坊_推进"
TRANSFER = "矩阵循生_套装调整"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["valid", "resume_result", "missing_title", "other_title",
                                  "missing_button", "other_option", "missing_confirm",
                                  "missing_transfer_prompt", "transfer_popup_stays"])
def test_workshop_rest_keeps_equipment_and_returns_to_map(tmp_path, language, case):
    resource = load_resource(tmp_path, language)
    assert resource.get_node_data(EVENT) is not None
    state = {"page": 3 if case == "resume_result" else 0, "entered": False}
    clicks, expired = [], []
    traditional = language == "zh_TW"
    visible = {
        TITLE: "自動化工坊" if traditional else "自动化工坊",
        OPTION: "原地休整（裝備位置調整）" if traditional else "原地休整（装备位置调整）",
        TEXT: "繼續" if traditional else "继续",
    }
    patterns = {}
    for name in visible:
        value = resource.get_node_data(name)["recognition"]["param"]["expected"]
        patterns[name] = [value] if isinstance(value, str) else value

    class Capture(Controller):
        def click(self, x, y):
            page = state["page"]
            region = {1: (730, 450, 250, 35), 4: (130, 20, 60, 40),
                      5: (710, 465, 60, 35)}.get(page, (990, 620, 60, 40))
            assert region[0] <= x < region[0] + region[2]
            assert region[1] <= y < region[1] + region[3]
            clicks.append(page)
            if not (case == "transfer_popup_stays" and page == 5):
                state["page"] += 1
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == ENTRY and not state["entered"]:
                return (990, 620, 60, 40)
            if name == TITLE and page < 4 and case != "missing_title":
                text = "隐秘工坊" if case == "other_title" else visible[name]
                box = (670, 110, 190, 35)
            elif name == OPTION and page == 1:
                text = "进入工坊（装备定向重铸）" if case == "other_option" else visible[name]
                box = (730, 450, 250, 35)
            elif name == TEXT and page in {0, 3} and case != "missing_button":
                text, box = (visible[name] if page == 0 else "休整"), (990, 620, 60, 40)
            elif name == "决定_矩阵循生" and page == 2 and case != "missing_confirm":
                return (990, 620, 60, 40)
            elif name == "矩阵循生_套装调整标题" and page in {4, 5}:
                return (65, 75, 390, 45)
            elif name == "返回_文字" and page in {4, 5}:
                return (130, 20, 60, 40)
            elif name == "矩阵循生_套装调整提示标题" and page == 5:
                return (615, 215, 60, 30)
            elif name == "矩阵循生_套装调整离开提示" and page == 5 and case != "missing_transfer_prompt":
                return (420, 330, 430, 40)
            elif name == "矩阵循生_离开演算确认文字" and page == 5:
                return (710, 465, 60, 35)
            elif name == MAP and page == 6:
                return (1003, 627, 163, 55)
            else:
                return None
            return box if any(re.search(pattern, text) for pattern in patterns[name]) else None

    class Begin(CustomAction):
        def run(self, context, argv):
            state["entered"] = True
            return True

    assert resource.register_custom_recognition("WorkshopPage", Page())
    assert resource.register_custom_action("BeginWorkshop", Begin())
    patch = {}
    allowed = {EVENT, ADVANCE, SELECT, TRANSFER, MAP,
               "矩阵循生_确认退出套装调整", "矩阵循生_退出套装调整"}
    for parent in (ENTRY, EVENT, TRANSFER):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in allowed:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    patch[ENTRY] = {**timing, "recognition": {"type": "Custom", "param": {
        "custom_recognition": "WorkshopPage",
    }}, "action": {"type": "Custom", "param": {"custom_action": "BeginWorkshop"}}}
    for name in (EVENT, ADVANCE, SELECT, TRANSFER, "矩阵循生_确认退出套装调整",
                 "矩阵循生_退出套装调整", "矩阵循生_套装调整返回前检查",
                 "矩阵循生_套装调整点击返回", "矩阵循生_套装调整提示已关闭"):
        patch[name] = dict(timing)
    for name in (TITLE, OPTION, TEXT, "决定_矩阵循生", "矩阵循生_套装调整标题",
                 "返回_文字", "矩阵循生_套装调整提示标题", "矩阵循生_套装调整离开提示",
                 "矩阵循生_离开演算确认文字", MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "WorkshopPage",
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
        expected = {"valid": list(range(6)), "resume_result": [3, 4, 5],
                    "missing_title": [], "other_title": [], "missing_button": [],
                    "other_option": [0], "missing_confirm": [0, 1],
                    "missing_transfer_prompt": list(range(5)),
                    "transfer_popup_stays": list(range(6))}[case]
        assert clicks == expected
        assert job.succeeded is (case in {"valid", "resume_result"})
        if job.succeeded:
            assert state["page"] == 6
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
