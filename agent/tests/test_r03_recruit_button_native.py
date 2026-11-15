"""List navigation reads the full button while retaining page and budget gates."""
import cv2
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("button,page,budget,expected", [
    ("DEPLOY", True, "5/5", True),
    ("REMOVE", True, "5/5", True),
    ("REPLACE", True, "5/5", True),
    ("DEP", True, "5/5", False),
    ("", True, "5/5", False),
    ("DEPLOY", False, "5/5", False),
    ("DEPLOY", True, "1/5", False),
])
def test_return_requires_complete_button_and_unchanged_list_budget(
    tmp_path, language, button, page, budget, expected,
):
    frame = np.full((720, 1280, 3), 220, np.uint8)
    cv2.putText(frame, budget, (411, 648), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.rectangle(frame, (999, 625), (1209, 679), (130, 85, 40), -1)
    cv2.putText(frame, button, (1015, 660), cv2.FONT_HERSHEY_SIMPLEX,
                .85, (255, 255, 255), 2, cv2.LINE_AA)
    # A matching label outside the button must not authorize list navigation.
    cv2.putText(frame, "DEPLOY", (800, 450), cv2.FONT_HERSHEY_SIMPLEX,
                .85, (255, 255, 255), 2, cv2.LINE_AA)
    clicks = []

    class Capture(Controller):
        def screencap(self): return frame
        def click(self, x, y):
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == "确认进入选人界面" and page:
                return (90, 640, 100, 30)
            if argv.node_name == "矩阵循生_编队已返回" and clicks:
                return (550, 45, 180, 75)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("RecruitPage", Page())
    overrides = {
        **R03Recruitment._list_budget_overrides(5, 5),
        # Portable synthetic Latin glyphs exercise native OCR without a system
        # font dependency. Production ROI, only_rec and compound gates remain.
        "矩阵循生_招募操作文字": {"recognition": {"param": {
            "expected": ["^DEPLOY$", "^REMOVE$", "^REPLACE$"],
        }}},
        "矩阵循生_返回编队": {
            "timeout": 100, "rate_limit": 1, "on_error": [],
            "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
        },
    }
    for name in ("确认进入选人界面", "矩阵循生_编队已返回"):
        overrides[name] = {"recognition": {"type": "Custom", "param": {
            "custom_recognition": "RecruitPage",
        }}, "pre_delay": 0, "post_delay": 0, "next": [], "on_error": []}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task("矩阵循生_返回编队", overrides).wait()
        assert job.succeeded is expected
        assert clicks == ([(98, 36)] if expected else [])
    finally:
        stop_and_wait_idle(tasker)
