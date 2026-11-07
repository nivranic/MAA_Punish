"""R03 must leave its dialogue loop active until battle controls are visible."""
import os
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer

import cv2
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from test_r03_recruitment_native import ROOT, native_hub
from test_support.native_lifecycle import stop_and_wait_idle


READY = "矩阵循生_战斗就绪"
FIXTURES = EVIDENCE_DIR


@pytest.mark.parametrize("sample", ["dialogue", "before_skip", "blank", "hud"])
def test_entry_requires_controls_in_addition_to_the_shared_pause_marker(tmp_path, sample):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        if sample == "hud":
            frame[:] = cv2.imread(str(FIXTURES / "r03_guili_bright_bench_hud.png"))
        elif sample != "blank":
            frame[:] = cv2.imread(str(FIXTURES / "r03_boss_combat_dialogue.png"))
            if sample == "before_skip":
                frame[499:630, 760:935] = 0
        errors = []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    assert context.get_node_data(READY) is not None
                    pause = context.run_recognition("战斗中", frame)
                    assert bool(pause and pause.hit) == (sample != "blank")
                    ready = context.run_recognition(READY, frame)
                    assert bool(ready and ready.hit) == (sample == "hud")
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("BattleEntryProbe", Probe())
        job = tasker.post_task("BattleEntryProbe", {"BattleEntryProbe": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "BattleEntryProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not errors and not expired and not inputs, errors


@pytest.mark.parametrize("entry", [
    "厄运连锁_矩阵循生", "战斗事件_矩阵循生", "BOSS战_矩阵循生",
])
def test_dialogue_appearing_after_the_pause_marker_is_skipped_before_combat(tmp_path, entry):
    model_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not model_bundle:
        pytest.skip("Set MAA_TEST_OCR_BUNDLE for the native dialogue replay")
    dialogue = cv2.imread(str(FIXTURES / "r03_boss_combat_dialogue.png"))
    hud = cv2.imread(str(FIXTURES / "r03_guili_bright_bench_hud.png"))
    assert dialogue is not None and hud is not None
    before_skip = dialogue.copy()
    before_skip[499:630, 760:935] = 0
    inputs, captures, expired = [], [], []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r03-battle-entry"
        def get_features(self): return 0
        def screencap(self):
            captures.append(True)
            return hud if inputs == [(832, 564)] else (before_skip if len(captures) < 5 else dialogue)
        def click(self, x, y, contact=0, pressure=1):
            inputs.append((x, y))
            return (x, y) == (832, 564)

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu() and resource.post_bundle(ROOT / "resource/base").wait().succeeded
    skip = resource.get_node_data("跳过战斗对话")
    assert skip["action"]["type"] == "Click"
    assert skip["action"]["param"]["target"] is True
    assert not (Path(model_bundle) / "pipeline").exists()
    assert resource.post_bundle(Path(model_bundle)).wait().succeeded
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(25, stop)
    watchdog.start()
    try:
        job = tasker.post_task(entry, {
            entry: {"recognition": {"type": "DirectHit"},
                    "action": {"type": "DoNothing"}, "timeout": 6000},
            # Observe the real outer routing; never invoke a combat action.
            "战斗中": {"action": {"type": "StopTask"}},
            # Fix only replay input; production Click still uses the OCR box.
            "跳过战斗对话": {"action": {"type": "Click", "param": {"target": [832, 564]}}},
            "返回主菜单_err": {"action": {"type": "StopTask"}, "next": [], "on_error": []},
        }).wait()
        assert job.succeeded and not expired
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert "返回主菜单_err" not in names
        assert names.index("跳过战斗对话") < names.index(READY) < names.index("战斗中"), names
        assert len(inputs) == 1 and len(captures) >= 5, (inputs, len(captures), names)
        assert inputs == [(832, 564)]
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
