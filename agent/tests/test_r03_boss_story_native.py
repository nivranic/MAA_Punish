"""Replay observed boss-story pages through strict R03 entry and combat routing."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
import os
from threading import Timer

import cv2
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ("approach", "rush", "selected", "break", "face", "prepare")


def load_samples():
    frames = [cv2.imread(str(EVIDENCE_DIR / f"r03_boss_story_{name}.png"))
              for name in SAMPLES]
    assert all(frame is not None and frame.shape == (720, 1280, 3) for frame in frames)
    return frames


@pytest.mark.parametrize("entry", ["矩阵循生", "BOSS战_矩阵循生"])
def test_actual_boss_story_sequence_reaches_existing_sortie_without_other_input(tmp_path, entry):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle:
        pytest.skip("Set MAA_TEST_OCR_BUNDLE for the model-only native replay")
    frames, inputs, expired = load_samples(), [], []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r03-boss-story"
        def get_features(self): return 0
        def screencap(self): return frames[min(len(inputs), len(frames) - 1)]
        def click(self, x, y, contact=0, pressure=1):
            inputs.append((x, y))
            return True

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu() and resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert not (Path(ocr_bundle) / "pipeline").exists()
    assert resource.post_bundle(Path(ocr_bundle)).wait().succeeded
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(25, stop)
    watchdog.start()
    try:
        job = tasker.post_task(entry, {
            # Begin after normal entry/card selection, retaining real story OCR.
            entry: {"recognition": {"type": "DirectHit"},
                    "action": {"type": "DoNothing"}, "timeout": 800},
            "覆盖布局菜单": {"enabled": False},
            "出击_矩阵循生": {"action": {"type": "StopTask"}},
            "返回主菜单_err": {"action": {"type": "StopTask"}, "next": [], "on_error": []},
        }).wait()
        assert job.succeeded and not expired
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names[-1] == "出击_矩阵循生", names
        assert "返回主菜单_err" not in names
        assert names.count("厄运连锁_剧情推进") == 4
        assert names.count("厄运连锁_呼喊躲开") == 1
        assert len(inputs) == 5
        for index, (x, y) in enumerate(inputs):
            if index == 1:
                assert 690 <= x < 1244 and 390 <= y < 455
            else:
                assert 970 <= x < 1190 and 610 <= y < 680
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("missing", ["title", "button"])
def test_boss_story_rejects_incomplete_page_without_input(tmp_path, missing):
    from test_r03_recruitment_native import native_hub

    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        frame[:] = load_samples()[0]
        if missing == "title":
            frame[96:158, 650:954] = 0
        else:
            frame[610:680, 970:1190] = 0
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    observations.append(context.run_recognition("厄运连锁_剧情推进", frame).hit)
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("BossStoryProbe", Probe())
        job = tasker.post_task("BossStoryProbe", {"BossStoryProbe": {
            "action": {"type": "Custom", "param": {"custom_action": "BossStoryProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired and not errors, errors
        assert observations == [False] and not inputs
