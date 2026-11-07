"""Replay the actual post-combat three-card reward through existing routing."""
import os
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer

import cv2
import pytest
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]


def test_combat_reward_routes_to_existing_card_picker(tmp_path):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE")
    frame = cv2.imread(str(EVIDENCE_DIR / "r03_reward_three_cards.png"))
    assert frame is not None and frame.shape == (720, 1280, 3)

    class Capture(CustomController):
        def __init__(self):
            super().__init__()
            self.inputs = []
        def connect(self): return True
        def request_uuid(self): return "offline-r03-post-combat-reward"
        def get_features(self): return 0
        def screencap(self): return frame
        def click(self, x, y, contact=0, pressure=1):
            self.inputs.append((x, y))
            return False

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu() and resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if ocr_bundle:
        assert resource.post_bundle(Path(ocr_bundle)).wait().succeeded
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []
    def stop():
        expired.append(True)
        tasker.post_stop()
    watchdog = Timer(20, stop)
    watchdog.start()
    try:
        job = tasker.post_task("战斗完成_矩阵循生", {
            "战斗完成_矩阵循生": {"action": {"type": "DoNothing"}},
            "点击藏品": {"action": {"type": "StopTask"}},
        }).wait()
        assert job.succeeded and not expired and not controller.inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names == ["战斗完成_矩阵循生", "领取藏品", "点击藏品"]
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
