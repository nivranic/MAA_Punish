"""Real recruitment-counter recognition and R02 exhaustion routing."""
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


@contextmanager
def native_counter(tmp_path, remaining, with_title=True, with_candidates=False):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE for the native replay")
    fixture = cv2.imread(str(EVIDENCE_DIR / "r02_recruitment_counts.png"))
    assert fixture is not None and fixture.shape == (242, 167, 3)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[536:657, 1083:1250] = fixture[remaining * 121:(remaining + 1) * 121]
    if with_title:
        title = cv2.imread(str(EVIDENCE_DIR / "r02_recruitment_title.png"))
        assert title is not None
        frame[48:95, 450:830] = title
    if with_candidates:
        names = cv2.imread(str(EVIDENCE_DIR / "r02_invitation_role_names.png"))
        refresh = cv2.imread(str(EVIDENCE_DIR / "r02_invitation_refresh_one.png"))
        assert names is not None and names.shape == (24, 806, 3)
        assert refresh is not None and refresh.shape == (133, 223, 3)
        frame[440:464, 242:1048] = names
        frame[39:172, 1050:1273] = refresh
    inputs = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r02-recruitment-counter"
        def get_features(self): return 0
        def screencap(self): return frame

        def click(self, x, y, contact=0, pressure=1):
            inputs.append((x, y))
            return False

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


@pytest.mark.parametrize(("remaining", "with_title"), [(0, True), (1, True), (0, False)])
def test_recruitment_exhaustion_requires_actual_zero_on_invitation_page(tmp_path, remaining, with_title):
    with native_counter(tmp_path, remaining, with_title) as (resource, tasker, frame, inputs, expired):
        hits = []

        class Probe(CustomAction):
            def run(self, context, argv):
                hits.append(context.run_recognition("招募次数0_厄愿潮声", frame).hit)
                return True

        assert resource.register_custom_action("R02CounterProbe", Probe())
        job = tasker.post_task("R02CounterProbe", {"R02CounterProbe": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "R02CounterProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired
        assert hits == [remaining == 0 and with_title] and not inputs


@pytest.mark.parametrize("entry", ["厄愿潮声", "邀约选择_厄愿潮声"])
def test_zero_recruitment_stops_before_advancing_or_selecting(tmp_path, entry):
    with native_counter(tmp_path, 0) as (_, tasker, frame, inputs, expired):
        # Start after entry setup/card selection, with the real exhausted page.
        # Production next order, counter recognition and StopTask stay intact.
        job = tasker.post_task(entry, {entry: {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "DoNothing"},
        }}).wait()
        assert job.succeeded and not expired, {"inputs": inputs, "watchdog": expired}
        assert not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names[-1] == "厄愿潮声_日耗尽停止"
        assert "招募次数0_厄愿潮声" in names
        assert "下一步_厄愿潮声" not in names
        assert "返回主菜单_err" not in names


@pytest.mark.parametrize("with_title", [True, False])
def test_open_recruitment_page_entry_requires_its_title(tmp_path, with_title):
    with native_counter(tmp_path, 1, with_title) as (resource, tasker, frame, inputs, expired):
        hits = []

        class Probe(CustomAction):
            def run(self, context, argv):
                hits.append(context.run_recognition("已打开招募页_厄愿潮声", frame).hit)
                return True

        assert resource.register_custom_action("R02OpenPageProbe", Probe())
        job = tasker.post_task("R02OpenPageProbe", {"R02OpenPageProbe": {
            "action": {"type": "Custom", "param": {"custom_action": "R02OpenPageProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired and not inputs
        assert hits == [with_title]


def test_root_resumes_noneligible_recruitment_page_at_refresh_without_advancing(tmp_path):
    with native_counter(tmp_path, 1, with_candidates=True) as (_, tasker, frame, inputs, expired):
        job = tasker.post_task("厄愿潮声", {
            "厄愿潮声": {"action": {"type": "DoNothing"}},
            "刷新人物_厄愿潮声": {"action": {"type": "StopTask"}, "post_delay": 0},
        }).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names[-3:] == ["已打开招募页_厄愿潮声", "处理招募页_厄愿潮声", "刷新人物_厄愿潮声"]
        assert not any(name.startswith("选择人物") for name in names)
        assert "下一步_厄愿潮声" not in names and "返回主菜单_err" not in names
