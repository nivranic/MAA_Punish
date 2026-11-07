"""Replay the real R03 hub after the inheritance event, without device input."""
from contextlib import contextmanager
import os
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]
CONFIRMATIONS = ("物归新主完成_矩阵循生", "物归新主完成_二次确认_矩阵循生")
EVENT_CONFIRMATIONS = (
    "事件完成_矩阵循生", "事件完成_二次确认_矩阵循生",
    "事件完成_结束_矩阵循生", "事件完成_结束_二次确认_矩阵循生",
)


@contextmanager
def native_hub(tmp_path, visible=True, *, accept_inputs=False):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE for the native replay")
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    if visible:
        fixture = cv2.imread(str(EVIDENCE_DIR / "r03_hub_controls.png"))
        assert fixture is not None and fixture.shape == (55, 466, 3)
        frame[627:682, 700:1166] = fixture
    inputs = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r03-hub-recruitment"
        def get_features(self): return 0
        def screencap(self): return frame
        def click(self, x, y, contact=0, pressure=1):
            inputs.append((x, y))
            return accept_inputs

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
        yield resource, tasker, frame, inputs, expired
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("visible", [True, False])
def test_inheritance_confirmations_recognize_the_actual_hub(tmp_path, visible):
    with native_hub(tmp_path, visible) as (resource, tasker, frame, inputs, expired):
        hits = []

        class Probe(CustomAction):
            def run(self, context, argv):
                hits.extend(context.run_recognition(name, frame).hit
                            for name in (*CONFIRMATIONS, *EVENT_CONFIRMATIONS))
                return True

        assert resource.register_custom_action("R03HubProbe", Probe())
        job = tasker.post_task("R03HubProbe", {"R03HubProbe": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "R03HubProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired
        assert hits == [visible] * 6 and not inputs


def test_inheritance_hub_enters_recruitment_before_the_board_fallback(tmp_path):
    with native_hub(tmp_path) as (_, tasker, frame, inputs, expired):
        # Begin after the event has returned to the hub. Keep production next
        # order and OCR, stopping immediately before the first recruitment click.
        job = tasker.post_task("物归新主_矩阵循生", {
            "物归新主_矩阵循生": {"recognition": {"type": "DirectHit"}},
            **{name: {"post_delay": 0} for name in CONFIRMATIONS},
            "更换人物": {"action": {"type": "StopTask"}, "post_wait_freezes": 0},
        }).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names[-3:] == [*CONFIRMATIONS, "更换人物"]
        assert "肉鸽内部" not in names
        assert "返回主菜单_err" not in names


def test_recruitment_return_to_hub_exits_before_role_swipe(tmp_path):
    with native_hub(tmp_path) as (_, tasker, frame, inputs, expired):
        # Resume after formation confirmation has returned to the board. Keep
        # production OCR and next order; use the swipe as a wrong-route sink.
        job = tasker.post_task("更换人物", {
            "更换人物": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "DoNothing"},
                "post_delay": 0,
                "post_wait_freezes": 0,
            },
            **{name: {"post_delay": 0} for name in EVENT_CONFIRMATIONS},
            "滑动_选人": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "StopTask"}, "post_wait_freezes": 0,
            },
        }).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names == ["更换人物", *EVENT_CONFIRMATIONS[:2]]
        assert "返回主菜单_err" not in names
