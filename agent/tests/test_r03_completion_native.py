"""Replay the observed simulation completion and reward pages through production routes."""
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
PAGES = ("splash", "collection", "members", "upgrade", "stats", "menu")
ENTRY = "矩阵循生_模拟演算完成"
STOP = "矩阵循生_完成结算停止"


def load_sample(name):
    frame = cv2.imread(str(EVIDENCE_DIR / f"r03_completion_{name}.png"))
    assert frame is not None and frame.shape == (720, 1280, 3)
    return frame


@pytest.mark.parametrize(("entry", "first_page"), [
    ("矩阵循生", 0), ("BOSS战_矩阵循生", 0),
    ("战斗事件_矩阵循生", 0), ("厄运连锁_矩阵循生", 0),
    ("矩阵循生", 1),
])
def test_completion_collects_rewards_and_stops_only_after_return_to_menu(tmp_path, entry, first_page):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle:
        pytest.skip("Set MAA_TEST_OCR_BUNDLE for the model-only native replay")
    frames = [load_sample(name) for name in PAGES[first_page:]]
    inputs, expired = [], []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r03-completion"
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
            # Begin after the entry/card/battle; retain actual completion OCR,
            # production next order, all settlement actions and final StopTask.
            entry: {"recognition": {"type": "DirectHit"},
                    "action": {"type": "DoNothing"}, "timeout": 800},
            "覆盖布局菜单": {"enabled": False},
            "返回主菜单_err": {"action": {"type": "StopTask"}, "next": [], "on_error": []},
        }).wait()
        assert job.succeeded and not expired
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names[-1] == STOP, names
        assert names.count(ENTRY) == 1
        assert names.count("矩阵循生_完成结算下一页") == 3
        assert names.count("矩阵循生_完成结算统计") == 1
        assert names.count("矩阵循生_完成提示继续") == 1 - first_page
        assert "返回主菜单_err" not in names and "启程_矩阵循生" not in names
        assert len(inputs) == 5 - first_page
        if first_page == 0:
            x, y = inputs[0]
            assert 550 <= x < 750 and 650 <= y < 715
        for x, y in inputs[1 - first_page:]:
            assert 975 <= x < 1185 and 610 <= y < 700
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("missing", ["splash_title", "stats_title", "stats_button"])
def test_completion_rejects_unconfirmed_page_without_input(tmp_path, missing):
    from test_r03_recruitment_native import native_hub

    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        frame[:] = load_sample("splash" if missing == "splash_title" else "stats")
        if missing == "splash_title":
            frame[360:440, 480:800] = 0
            target = ENTRY
        else:
            target = "矩阵循生_完成结算统计"
            if missing == "stats_title":
                frame[85:150, 85:285] = 0
            else:
                frame[610:700, 975:1185] = 0
        hits, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    hits.append(context.run_recognition(target, frame).hit)
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("CompletionNegativeProbe", Probe())
        job = tasker.post_task("CompletionNegativeProbe", {"CompletionNegativeProbe": {
            "action": {"type": "Custom", "param": {"custom_action": "CompletionNegativeProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired and not errors, errors
        assert hits == [False] and not inputs


def test_idle_menu_still_starts_a_new_adventure(tmp_path):
    from test_r03_recruitment_native import native_hub

    with native_hub(tmp_path, visible=False) as (_, tasker, frame, inputs, expired):
        frame[:] = load_sample("menu")
        job = tasker.post_task("矩阵循生", {
            "矩阵循生": {"action": {"type": "DoNothing"}, "timeout": 800},
            "覆盖布局菜单": {"enabled": False},
            "启程_矩阵循生": {"action": {"type": "StopTask"}},
            "返回主菜单_err": {"action": {"type": "StopTask"}, "next": [], "on_error": []},
        }).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names[-1] == "启程_矩阵循生", names
        assert ENTRY not in names and STOP not in names and "返回主菜单_err" not in names
