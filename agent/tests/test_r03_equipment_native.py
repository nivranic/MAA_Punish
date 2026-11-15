"""Equipment routing after confirmed recruitment; synthetic UI, no device pixels."""
from threading import Timer

import jsonc
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle

ENTRY = "确认装备_矩阵循生"
SELECT = "矩阵循生_装备选择红位"
CONFIRM = "矩阵循生_红位装备确认"
OTHER = "矩阵循生_非装备确认"
TITLE = "选择装备_矩阵循生"
LEFT = "矩阵循生_装备红位左角"
RIGHT = "矩阵循生_装备红位右角"
NUMBER = "矩阵循生_装备红位编号"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("layout", ["three", "compressed", "red_full", "single_remaining", "single_remaining_one"])
@pytest.mark.parametrize("case", ["select_red", "already_red", "selection_failed", "other_dialog", "new_round", "missing_number"])
def test_only_confirm_the_intended_slot_or_the_original_non_equipment_dialog(tmp_path, language, layout, case):
    state = {"red": case == "already_red", "closed": False, "settling": 0}
    clicks = []
    number_y = 295 if layout in {"three", "single_remaining", "single_remaining_one"} else (155 if layout == "red_full" else 156)
    remaining = layout in {"red_full", "single_remaining", "single_remaining_one"}
    remaining_left, remaining_right = "矩阵循生_装备余位左角", "矩阵循生_装备余位右角"
    top_one, bottom_three, middle_three = "矩阵循生_装备1号上部", "矩阵循生_装备3号下部", "矩阵循生_装备余位单卡"

    class Capture(Controller):
        def screencap(self):
            if state["settling"]:
                state["settling"] -= 1
                if not state["settling"]:
                    state["red"] = True
            return np.zeros((720, 1280, 3), np.uint8)

        def click(self, x, y):
            left, width, height = (1130, 30, 50) if layout == "red_full" else (1134, 32, 53)
            if left <= x < left + width and number_y + 20 <= y < number_y + 20 + height:
                assert not state["red"] and not state["closed"]
                clicks.append("red")
                if case != "selection_failed":
                    state["settling"] = 3
            elif 1096 <= x < 1142 and 653 <= y < 680:
                assert case in {"other_dialog", "new_round"} or state["red"]
                assert not state["closed"]
                clicks.append("confirm")
                state["closed"] = True
            else:
                clicks.append(("wrong_slot", x, y))
                return False
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if state["closed"]:
                return None
            if argv.node_name == TITLE and case != "other_dialog":
                return (69, 68, 169, 39)
            if argv.node_name == NUMBER and not remaining and case not in {"other_dialog", "missing_number"}:
                return (1194, number_y, 32, 53)
            if remaining and case not in {"other_dialog", "missing_number"}:
                if layout == "red_full" and argv.node_name == top_one:
                    return (1190, 155, 30, 50)
                if layout == "red_full" and argv.node_name == bottom_three:
                    return (1194, 425, 32, 53)
                if layout == "single_remaining" and argv.node_name == middle_three:
                    return (1194, 295, 32, 53)
                if layout == "single_remaining_one" and argv.node_name == "矩阵循生_装备1号中部":
                    return (1190, 290, 30, 50)
            if argv.node_name in ({remaining_left, remaining_right} if remaining else {LEFT, RIGHT}) and state["red"]:
                return (1080 if argv.node_name == LEFT else 1205, number_y - 9, 14, 16)
            if argv.node_name == "确定_文字":
                return (1096, 653, 46, 27)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R03EquipmentPage", Page())
    patch = R03Recruitment.red_equipment_patch()
    if case == "new_round":
        nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_3/Roguelike_3.jsonc").read_text("utf-8"))
        patch = {ENTRY: nodes["矩阵循生"]["action"]["param"]["custom_action_param"][ENTRY]}
    for name in (TITLE, NUMBER, LEFT, RIGHT, remaining_left, remaining_right, top_one, bottom_three, middle_three, "矩阵循生_装备1号中部", "确定_文字"):
        patch[name] = {"recognition": {"type": "Custom", "param": {"custom_recognition": "R03EquipmentPage"}}}
    for name in (ENTRY, SELECT, CONFIRM, OTHER, "矩阵循生_原始确认", "矩阵循生_余位装备确认", "矩阵循生_装备选择余位"):
        patch.setdefault(name, {}).update(pre_delay=0, post_delay=0, rate_limit=1, timeout=250, on_error=[])
    patch[SELECT]["post_wait_freezes"] = 0
    patch["矩阵循生_装备选择余位"]["post_wait_freezes"] = 0
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(15, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(ENTRY, patch).wait()
        assert not expired
        if case == "missing_number":
            assert job.failed and clicks == [] and not state["closed"]
        elif case == "selection_failed":
            assert job.failed and clicks == ["red"] and not state["closed"]
        else:
            assert job.succeeded and state["closed"]
            assert clicks == (["red", "confirm"] if case == "select_red" else ["confirm"])
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
