"""Actual Maa 5.14.2 control flow with memory images/input endpoints only."""

import json
from pathlib import Path
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from action.basics import ScopedRecovery as recovery


ROOT = Path(__file__).resolve().parents[2]


class MemoryController(CustomController):
    def __init__(self, state):
        super().__init__()
        self.state, self.inputs = state, []
        self.image = np.zeros((720, 1280, 3), np.uint8)
        data = (ROOT / "resource/base/image/指挥局/返回.png").read_bytes()
        template = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert template is not None
        height, width = template.shape[:2]
        assert width <= 51 and height <= 53
        self.image[10:10 + height, 27:27 + width] = template

    def connect(self): return True
    def request_uuid(self): return "scoped-recovery-memory-only"
    def get_features(self): return 0
    def screencap(self): return self.image

    def click(self, x, y):
        self.inputs.append(("Click", x, y))
        if self.state["case"] == "business-failed":
            return False
        self.state["page"] = "home"
        return True

    def swipe(self, *args):
        self.inputs.append(("Swipe", *args)); return False
    def stop_app(self, *args):
        self.inputs.append(("StopApp", *args)); return False
    def start_app(self, *args):
        self.inputs.append(("StartApp", *args)); return False
    def touch_down(self, *args):
        self.inputs.append(("TouchDown", *args)); return False
    def touch_up(self, *args):
        self.inputs.append(("TouchUp", *args)); return False
    def click_key(self, *args):
        self.inputs.append(("ClickKey", *args)); return False


class PageRecognition(CustomRecognition):
    def __init__(self, state):
        self.state = state
        super().__init__()

    def analyze(self, context, argv):
        name, page = argv.node_name, self.state["page"]
        if name == "指挥局": return (1, 1, 1, 1)
        if name == recovery.HOME_NODE and page == "home": return (1000, 250, 60, 60)
        if name == "空任务" and page == "home": return (1, 1, 1, 1)
        if name == recovery.GUILD_NODE and page in {"guild", "result", "dialog"}:
            return (282, 0, 300, 75)
        if name == "检查指定按钮" and self.state["case"] in {"business-failed", "normal"}:
            return (900, 300, 30, 30)
        if name == "关闭奖励_指挥局" and page == "result": return (600, 570, 76, 70)
        if name == "确定" and page == "dialog": return (500, 400, 100, 50)
        # These old cross-mode candidates deliberately remain matchable. The
        # new default handler must never traverse them after the D03 timeout.
        if name in {"选择宝箱奖励_厄愿潮声", "领取藏品_厄愿潮声"} and page == "rogue-reward":
            return (500, 400, 100, 50)
        if name in {"从战斗中返回", "退出_肉鸽"} and page == "battle": return (500, 400, 100, 50)
        if name in {"从选人界面返回_厄愿潮声", "确定"} and page == "rogue-confirm":
            return (500, 400, 100, 50)
        return None


@pytest.mark.parametrize("case,page,expected_inputs,expected_outcome", [
    ("guild-navigation", "guild", 1, "home_original_task_failed"),
    ("already-home", "home", 0, "home_original_task_failed"),
    ("unknown", "unknown", 0, "stopped_unknown"),
    ("rogue-reward", "rogue-reward", 0, "stopped_unknown"),
    ("battle", "battle", 0, "stopped_unknown"),
    ("rogue-confirm", "rogue-confirm", 0, "stopped_unknown"),
    ("result", "result", 0, "stopped_unknown"),
    ("dialog", "dialog", 0, "stopped_unknown"),
    ("business-failed", "guild", 1, "stopped_unknown"),
    ("normal", "guild", 1, None),
])
def test_real_default_entry_is_scoped_and_normal_d03_is_preserved(
        tmp_path, monkeypatch, case, page, expected_inputs, expected_outcome):
    Tasker.set_log_dir(tmp_path / "native-logs")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    monkeypatch.setattr(recovery, "LoggerComponent", lambda _: SimpleNamespace(
        logger=SimpleNamespace(warning=lambda *_: None)))
    state = {"case": case, "page": page}
    resource = Resource()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    recognizer = PageRecognition(state)
    action = recovery.ScopedRecovery()
    assert resource.register_custom_recognition("RecoveryPageFixture", recognizer)
    assert resource.register_custom_action("ScopedRecovery", action)
    controller = MemoryController(state)
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    patch = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "RecoveryPageFixture"}},
        "timeout": 15, "rate_limit": 0, "pre_delay": 0, "post_delay": 0,
        "pre_wait_freezes": {"time": 0}, "post_wait_freezes": {"time": 0},
    } for name in resource.node_list if name not in {recovery.RECOVERY_NODE, recovery.BACK_NODE}}
    # Keep the actual return-icon TemplateMatch, target ROI and image. Only its
    # surrounding page recognition is a scripted endpoint, never live OCR.
    patch[recovery.BACK_NODE] = {"pre_delay": 0, "post_delay": 0}
    job = tasker.post_task("指挥局", patch)
    job.wait()
    if case != "normal":
        until = time.monotonic() + 2
        while action.last_result is None and time.monotonic() < until:
            time.sleep(.005)
        assert action.last_result is not None
        assert action.last_result["business_success"] is False
        assert action.last_result["outcome"] == expected_outcome
        assert action.last_result["returns"] == (1 if case == "guild-navigation" else 0)
        assert action.last_result["original_failure_cleanup_verified"] is False
    else:
        assert job.succeeded and action.last_result is None
    tasker.post_stop().wait()
    assert len(controller.inputs) == expected_inputs
    assert all(item[0] == "Click" for item in controller.inputs)
    if case == "guild-navigation":
        assert 27 <= controller.inputs[0][1] < 78 and 10 <= controller.inputs[0][2] < 63
    if case in {"guild-navigation", "already-home"}:
        assert action.last_result["cleanup"]["confirmed"] is True
        assert action.last_result["stop_requested"] is False
