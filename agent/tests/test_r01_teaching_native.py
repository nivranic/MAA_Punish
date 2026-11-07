"""Replay the optional unowned-member tutorial prompt after R01 team selection."""
import os
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("prompt", [True, False])
def test_r01_join_team_cancels_optional_teaching_and_returns(tmp_path, prompt):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE for the native replay")
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    if prompt:
        crop = cv2.imread(str(EVIDENCE_DIR / "r01_unowned_teaching.png"))
        assert crop is not None
        frame[220:580, 320:980] = crop
    inputs = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r01-unowned-member-prompt"
        def get_features(self): return 0
        def screencap(self): return frame

        def click(self, x, y, contact=0, pressure=1):
            valid = 360 <= x <= 527 and 513 <= y <= 560
            inputs.append(valid)
            return valid

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if ocr_bundle:
        assert not (Path(ocr_bundle) / "pipeline").exists(), "Use a model-only bundle"
        assert resource.post_bundle(Path(ocr_bundle)).wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(20, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        # The join click has already produced the captured prompt. Keep its
        # production continuation and the shared prompt recognizer/action intact.
        job = tasker.post_task("宣叙编队_编入队伍_宣叙妄响", {
            "宣叙编队_编入队伍_宣叙妄响": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "DoNothing"},
            },
        }).wait()
        assert job.succeeded and not expired, {"inputs": inputs, "watchdog": expired}
        assert inputs == ([True] if prompt else [])
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert ("关闭未拥有人物提示" in names) == prompt
        assert "返回主菜单_err" not in names
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
