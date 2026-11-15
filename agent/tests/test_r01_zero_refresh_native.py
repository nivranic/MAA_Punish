"""A recruitment refresh limit is not completion of the roguelike task."""

from pathlib import Path

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
ZERO = "刷新次数为0_宣叙妄响"
REMAINING = "开始招募_宣叙妄响"
BUTTONS = [f"{slot}号位_招募按钮_宣叙妄响" for slot in "一二三"]


def load_resource(tmp_path, language="base"):
    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if language != "base":
        assert resource.post_bundle(ROOT / "resource" / language).wait().succeeded
    return resource


class Controller(CustomController):
    def connect(self): return True
    def request_uuid(self): return "r01-zero-refresh-regression"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), np.uint8)


@pytest.mark.parametrize("text,expected", [("0/3", True), ("0/5", True),
                                          ("3/3", False), ("2/5", False),
                                          ("0/0", False), ("3", False),
                                          ("10/3", False)])
def test_zero_refresh_recognizes_the_full_fraction(tmp_path, text, expected):
    frame = np.zeros((720, 1280, 3), np.uint8)
    cv2.putText(frame, text, (999, 120), cv2.FONT_HERSHEY_SIMPLEX,
                .85, (255, 220, 80), 2, cv2.LINE_AA)

    class Capture(Controller):
        def screencap(self): return frame

    resource, controller, tasker = load_resource(tmp_path), Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(ZERO, {
            ZERO: {"next": [], "on_error": [], "timeout": 100,
                   "pre_delay": 0, "post_delay": 0},
        }).wait()
        assert job.succeeded is expected
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("available,remaining", [(0, True), (1, True), (2, True),
                                               (None, True), (0, False)])
def test_zero_refresh_recruits_available_candidate_instead_of_stopping(
    tmp_path, language, available, remaining
):
    clicks = []
    state = {"selected": False}

    class Capture(Controller):
        def click(self, x, y):
            clicks.append((x, y))
            state["selected"] = True
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == ZERO:
                return (999, 91, 58, 39)
            if argv.node_name in {REMAINING, "开始招募_倒序_宣叙妄响"} and remaining:
                return (1120, 560, 140, 45)
            if available is not None and argv.node_name == BUTTONS[available]:
                return (262 + available * 340, 335, 80, 60)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R01ZeroPage", Page())
    names = [ZERO, REMAINING, "开始招募_倒序_宣叙妄响", "招募完成_宣叙妄响", *BUTTONS]
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01ZeroPage"}},
        "pre_delay": 0, "post_delay": 0, "timeout": 100, "on_error": [],
    } for name in names}
    for name in [f"零刷新补位{slot}号_宣叙妄响" for slot in "一二三"]:
        overrides[name] = {"pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0}
    # Observe through the real candidate click, then stop before opening a modal.
    for name in (REMAINING, "开始招募_倒序_宣叙妄响"):
        overrides[name].update(action={"type": "StopTask"}, next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(ZERO, overrides).wait()
        selectable = available is not None and remaining
        assert job.succeeded is selectable
        assert len(clicks) == int(selectable)
        if selectable:
            x, y = clicks[0]
            assert 262 + available * 340 <= x < 342 + available * 340
            assert 335 <= y < 395
        detail = tasker.get_task_detail(job.job_id)
        visited = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert "宣叙妄响_日耗尽停止" not in visited
    finally:
        stop_and_wait_idle(tasker)
