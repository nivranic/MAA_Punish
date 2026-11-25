"""Native navigation requires the full button; caller owns the budget check."""
import cv2
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("button,page,list_budget,team_budget,expected,nav_click", [
    ("DEPLOY", True, "5/5", "5/5", (5, 5), True),
    ("REMOVE", True, "5/5", "5/5", (5, 5), True),
    ("REPLACE", True, "5/5", "5/5", (5, 5), True),
    ("DEP", True, "5/5", "5/5", None, False),
    ("", True, "5/5", "5/5", None, False),
    ("DEPLOY", False, "5/5", "5/5", None, False),
    ("DEPLOY", True, "1/5", "5/5", None, True),
    ("DEPLOY", True, "15", "5/5", (5, 5), True),
    ("DEPLOY", True, "5/5", "", None, True),
])
def test_return_requires_full_button_and_checks_readable_budget_after_navigation(
    tmp_path, monkeypatch, language, button, page, list_budget, team_budget, expected, nav_click,
):
    monkeypatch.setattr(R03Recruitment, "_budget_cache", None)
    before = np.full((720, 1280, 3), 220, np.uint8)
    after = before.copy()
    cv2.putText(before, list_budget, (411, 648), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(after, team_budget, (111, 638), cv2.FONT_HERSHEY_SIMPLEX,
                .8, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.rectangle(before, (999, 625), (1209, 679), (130, 85, 40), -1)
    cv2.putText(before, button, (1015, 660), cv2.FONT_HERSHEY_SIMPLEX,
                .85, (255, 255, 255), 2, cv2.LINE_AA)
    # A matching label outside the button must not authorize list navigation.
    cv2.putText(before, "DEPLOY", (800, 450), cv2.FONT_HERSHEY_SIMPLEX,
                .85, (255, 255, 255), 2, cv2.LINE_AA)
    clicks, formations = [], []

    class Capture(Controller):
        def screencap(self):
            return after if clicks else before

        def click(self, x, y):
            clicks.append((x, y))
            return (x, y) == (98, 36) and len(clicks) == 1

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == "确认进入选人界面" and page and not clicks:
                return (90, 640, 100, 30)
            if argv.node_name == "矩阵循生_编队色位" and clicks:
                return (550, 45, 180, 75)
            return None

    class Probe(CustomAction):
        def run(self, context, argv):
            formations.append(R03Recruitment(None).return_to_formation(context))
            return True

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("RecruitPage", Page())
    assert resource.register_custom_action("ProbeRecruitReturn", Probe())
    overrides = {
        "ProbeRecruitReturn": {
            "recognition": "DirectHit",
            "action": {"type": "Custom", "param": {"custom_action": "ProbeRecruitReturn"}},
            "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0,
        },
        # Synthetic Latin glyphs keep real OCR, ROI and compound gates in play.
        "矩阵循生_招募操作文字": {"recognition": {"param": {
            "expected": ["^DEPLOY$", "^REMOVE$", "^REPLACE$"],
        }}},
        **{name: {"timeout": 250, "rate_limit": 10, "on_error": [],
                  "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0}
           for name in ("矩阵循生_招募列表就绪", "矩阵循生_返回编队", "矩阵循生_编队已返回")},
    }
    for name in ("确认进入选人界面", "矩阵循生_编队色位"):
        overrides[name] = {"recognition": {"type": "Custom", "param": {
            "custom_recognition": "RecruitPage",
        }}}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task("ProbeRecruitReturn", overrides).wait()
        assert job.succeeded and len(formations) == 1
        formation, = formations
        if expected is None:
            assert formation is None
        else:
            assert formation is not None and formation["budget"] == expected
        assert clicks == ([(98, 36)] if nav_click else [])
    finally:
        stop_and_wait_idle(tasker)
