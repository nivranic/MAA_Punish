"""Cache-refresh member details: real OCR, stale selection and failed clicks."""
import os
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from action.basics.RoleSelection import RoleSelection
from test_support.fakes import FakeContext
from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("model", "click_ok", "switch", "expected"), [
    ("夙星", True, True, 701),
    ("逆冕", True, False, 8159),
    ("夙星", True, False, 0),
    ("夙星", False, False, 0),
])
def test_native_member_power_belongs_to_the_selected_model(
    tmp_path, model, click_ok, switch, expected,
):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE for the native replay")
    frames = []
    for name in ("inverse", "astral"):
        fixture = cv2.imread(str(EVIDENCE_DIR / f"member_detail_{name}.png"))
        assert fixture is not None and fixture.shape == (130, 420, 3)
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[100:230, 820:1240] = fixture
        frames.append(frame)

    class Capture(CustomController):
        def __init__(self):
            super().__init__()
            self.inputs = []
            self.selected = 0

        def connect(self): return True
        def request_uuid(self): return "offline-member-cache-power"
        def get_features(self): return 0
        def screencap(self): return frames[self.selected]
        def click(self, x, y, contact=0, pressure=1):
            self.inputs.append((x, y))
            if click_ok and switch:
                self.selected = 1
            return click_ok

    values = []

    class Probe(CustomAction):
        def run(self, context, argv):
            values.append(RoleSelection()._read_member_power(
                context, f"成员·{model}", {"name": model}, (135, 85, 90, 90),
            ))
            return True

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if ocr_bundle:
        assert not (Path(ocr_bundle) / "pipeline").exists(), "Use a model-only bundle"
        assert resource.post_bundle(Path(ocr_bundle)).wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    assert resource.register_custom_action("MemberPowerProbe", Probe())
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(20, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task("MemberPowerProbe", {"MemberPowerProbe": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "MemberPowerProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired
        assert values == [expected]
        assert controller.inputs == [(180, 130)]
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


def test_stopped_member_scan_does_not_click():
    context = FakeContext()
    context.tasker.stopping = True
    assert RoleSelection()._read_member_power(context, "成员·夙星", {"name": "夙星"}, (135, 85, 90, 90)) == 0
    assert not context.run_task_calls and not context.tasker.controller.clicks


@pytest.mark.parametrize(("mode", "member_details"), [("cache_refresh", True), ("standard", False), ("roguelike_1", False)])
def test_member_detail_clicks_are_exclusive_to_cache_refresh(tmp_path, monkeypatch, mode, member_details):
    selector = RoleSelection()
    monkeypatch.setattr(selector, "_cache_path", lambda: tmp_path / "role_cache.json")
    monkeypatch.setattr(selector, "_load_cache", lambda frequency: None)
    selector.send_msg = Mock()
    selector.recognize_role = Mock(return_value={})
    context = FakeContext(node_data={"scan": {"attach": {"max_try": 1}}})
    assert not selector.run(context, SimpleNamespace(
        node_name="scan", custom_action_param=f'{{"selection_mode":"{mode}"}}',
    )).success
    assert selector.recognize_role.call_args.args[-1] is member_details
