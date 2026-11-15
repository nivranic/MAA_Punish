"""The waypoint recovery must click the detected pause button and then retreat."""

import jsonc
import cv2
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("pause_box", [(1150, 28, 48, 48), (1080, 20, 45, 55)])
def test_waypoint_uses_recognized_pause_box_and_exits(tmp_path, language, pause_box):
    state = {"page": "battle"}
    clicks = []
    entry = "失败2_宣叙妄响"
    done = "没打过_宣叙妄响_reco"

    class Capture(Controller):
        def click(self, x, y):
            page = state["page"]
            if page == "battle":
                bx, by, width, height = pause_box
                assert bx <= x < bx + width and by <= y < by + height
                state["page"] = "pause"
            elif page == "pause":
                assert x > 1000 and y > 600
                state["page"] = "confirm"
            elif page == "confirm":
                assert 700 <= x < 900 and 400 <= y < 500
                state["page"] = "finished"
            else:
                raise AssertionError((page, x, y))
            clicks.append(page)
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            page, name = state["page"], argv.node_name
            if name == entry + "_文字" and page in {"battle", "pause"}:
                return (60, 100, 150, 30)
            if name == "战斗中" and page == "battle":
                return pause_box
            if name == "撤退_宣叙妄响" and page == "pause":
                return (1020, 650, 100, 50)
            if name == "确定" and page == "confirm":
                return (700, 400, 200, 100)
            if name == done and page == "finished":
                return (500, 300, 200, 50)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R01WaypointPage", Page())
    names = [entry + "_文字", "战斗中", "撤退_宣叙妄响", "确定", done]
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01WaypointPage"}},
        "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
        "timeout": 500, "rate_limit": 1, "on_error": [],
    } for name in names}
    overrides[entry] = {"pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
                        "timeout": 500, "rate_limit": 1, "on_error": []}
    overrides[done].update(action={"type": "StopTask"}, next=[])
    nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc").read_text(encoding="utf-8"))
    # Re-enter through the normal task entry from a live waypoint HUD as well.
    for item in nodes["宣叙妄响"]["next"]:
        name = item.removeprefix("[JumpBack]")
        if name != entry and name not in overrides:
            overrides[name] = {"recognition": {"type": "Custom", "param": {"custom_recognition": "R01WaypointPage"}}}
    overrides["宣叙妄响"] = {"action": {"type": "DoNothing"}, "pre_delay": 0,
                              "post_delay": 0, "timeout": 500, "on_error": []}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        assert tasker.post_task("宣叙妄响", overrides).wait().succeeded
        assert state["page"] == "finished"
        assert clicks == ["battle", "pause", "confirm"]
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("x,expected", [(982, True), (1100, True), (800, False)])
def test_retreat_ocr_keeps_left_edge_of_button_text(tmp_path, x, expected):
    frame = np.zeros((720, 1280, 3), np.uint8)
    cv2.putText(frame, "EXIT", (x, 688), cv2.FONT_HERSHEY_SIMPLEX,
                .7, (255, 255, 255), 2, cv2.LINE_AA)

    class Capture(Controller):
        def screencap(self): return frame

    resource, controller, tasker = load_resource(tmp_path), Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task("撤退_宣叙妄响", {
            "撤退_宣叙妄响": {
                "recognition": {"param": {"expected": "EXIT"}},
                "action": {"type": "DoNothing"}, "next": [], "on_error": [],
                "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
                "timeout": 100,
            },
        }).wait()
        assert job.succeeded is expected
    finally:
        stop_and_wait_idle(tasker)
