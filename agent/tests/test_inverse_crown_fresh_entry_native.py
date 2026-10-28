"""Real SDK And/selected-box/native Click replay; no phone or OS input.

Set MAA_FRESH_ENTRY_PREPARATION_FRAME to an ignored, real 1280x720 preparation
PNG and MAA_FRESH_ENTRY_OCR_RESOURCE to the existing OCR model resource. These
opt-in replays do not establish Android freshness or a game transition.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

import numpy as np
import pytest

from maa.custom_action import CustomAction
from maa.define import AndRecognitionResult, RecognitionDetail
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

import logger_component
from action.combat.config.inverse_crown_profile import InverseCrownProfile, InverseCrownUnavailable
from action.combat.core.fresh_entry import PREPARE_NODE, START_NODE, begin_from_preparation
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from test_inverse_crown_task_option import _MemoryController
from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def preparation_frame():
    import cv2
    configured = os.environ.get("MAA_FRESH_ENTRY_PREPARATION_FRAME")
    if not configured:
        pytest.skip("opt-in real preparation PNG not supplied")
    source = Path(configured)
    image = cv2.imdecode(np.fromfile(source, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape == (720, 1280, 3)
    return source, image


@contextmanager
def native_runtime(tmp_path, monkeypatch, image):
    bundle = Path(os.environ.get("MAA_FRESH_ENTRY_OCR_RESOURCE", ROOT / "build-android/pi-source/resource/base"))
    if not (bundle / "model/ocr/rec.onnx").is_file():
        pytest.fail("real replay requires the configured OCR model bundle")
    monkeypatch.setattr(logger_component, "LOG_DIR", str(tmp_path / "logs"))
    Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)

    class Controller(_MemoryController):
        def screencap(self):
            self.captures += 1
            return image.copy()

        def click(self, *args):
            self.inputs.append(("Click", *args))
            return True

    resource, controller, tasker = Resource(), Controller(), Tasker()
    assert resource.post_bundle(bundle).wait().succeeded
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        yield resource, controller, tasker
    finally:
        stop_and_wait_idle(tasker)


def run_callback(resource, tasker, callback):
    assert resource.register_custom_action("FreshEntrySdkReplay", callback)
    job = tasker.post_task("FreshEntrySdkReplay", {"FreshEntrySdkReplay": {
        "action": {"type": "Custom", "param": {"custom_action": "FreshEntrySdkReplay"}},
        "pre_delay": 0, "post_delay": 0, "next": []}})
    assert job.wait().succeeded


def save_report(tmp_path, name, report):
    output = Path(os.environ.get("MAA_FRESH_ENTRY_SDK_EVIDENCE", tmp_path))
    output.mkdir(parents=True, exist_ok=True)
    # An explicitly supplied evidence directory must be exclusive per run.
    with (output / name).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def test_native_real_p1_and_selected_box_to_one_memory_click(tmp_path, monkeypatch, preparation_frame):
    source, image = preparation_frame

    class Inspect(CustomAction):
        def run(self, context, argv):
            ready = context.run_recognition(PREPARE_NODE, image)
            self.ready = ready
            self.action = context.run_action(START_NODE, box=tuple(ready.box)) if ready.hit else None
            return CustomAction.RunResult(success=True)

    inspect = Inspect()
    with native_runtime(tmp_path, monkeypatch, image) as (resource, controller, tasker):
        run_callback(resource, tasker, inspect)
        ready, action = inspect.ready, inspect.action
        report = {"framework": Library.version(), "source_frame_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "bgr_sha256": hashlib.sha256(image.tobytes()).hexdigest(), "recognition_type": type(ready).__name__,
            "algorithm": str(ready.algorithm), "hit": ready.hit, "best_result_type": type(ready.best_result).__name__,
            "best_result_has_box": hasattr(ready.best_result, "box"), "selected_box": list(ready.box) if ready.box else None,
            "sub_results": [{"name": item.name, "algorithm": str(item.algorithm), "hit": item.hit,
                             "box": list(item.box) if item.box else None} for item in ready.best_result.sub_results],
            "action": {"name": action.name, "action": str(action.action), "success": action.success,
                       "action_id": action.action_id, "box": list(action.box)} if action else None,
            "memory_inputs": controller.inputs, "hardware_inputs": 0,
            "scope": "Actual And-to-Click ABI replay, not a freshness or game-start acceptance"}
        save_report(tmp_path, "native-and-to-click.json", report)
        assert isinstance(ready, RecognitionDetail) and ready.algorithm == "And" and ready.hit is True
        assert isinstance(ready.best_result, AndRecognitionResult) and not hasattr(ready.best_result, "box")
        assert tuple(ready.box) == tuple(ready.best_result.sub_results[0].box)
        assert action.success and action.name == START_NODE and action.action == "Click"
        assert tuple(action.box) == tuple(ready.box) and action.action_id > 0
        assert len(controller.inputs) == 1 and controller.inputs[0][0] == "Click"
        x, y, width, height = tuple(ready.box)
        click_x, click_y = controller.inputs[0][1:3]
        assert x <= click_x < x + width and y <= click_y < y + height


@pytest.mark.parametrize("preparation", [True, False])
def test_native_helper_real_and_or_negative_frame_keeps_start_and_age_guards(
    tmp_path, monkeypatch, preparation_frame, preparation,
):
    source, image = preparation_frame
    replay = image if preparation else np.zeros_like(image)

    class RecordingContext:
        def __init__(self, context):
            self.context, self.tasker, self.recognitions = context, context.tasker, []
        def get_node_data(self, name): return self.context.get_node_data(name)
        def run_action(self, name, *, box): return self.context.run_action(name, box=box)
        def run_recognition(self, name, frame):
            before = time.monotonic()
            result = self.context.run_recognition(name, frame)
            self.recognitions.append({"name": name, "started_at": before, "returned_at": time.monotonic(),
                "frame_object_id": id(frame), "hit": result.hit, "algorithm": str(result.algorithm),
                "box": list(result.box) if result.box else None, "best_result_type": type(result.best_result).__name__})
            return result

    class Inspect(CustomAction):
        def run(self, context, argv):
            # Warm real models before the qualifying, newly completed capture.
            # No capture timestamp or freshness threshold is replaced.
            for name in ("战斗中", "战斗重启1", PREPARE_NODE):
                context.run_recognition(name, replay)
            self.recording = RecordingContext(context)
            combat = CombatTask(self.recording, CombatCheck(), inverse_crown_profile=InverseCrownProfile(
                enabled=True, scene="single", source="self", signature_weapon_confirmed=True))
            self.combat, self.candidate, self.error = combat, None, None
            try:
                self.candidate = begin_from_preparation(combat)
            except BaseException as error:
                self.error = error
            finally:
                self.metadata = combat.input_scope.last_frame_metadata
                self.age = time.monotonic() - self.metadata.captured_at if self.metadata else None
                if self.candidate is not None: self.candidate.retire()
                self.cleanup = combat.input_scope.close()
            return CustomAction.RunResult(success=True)

    inspect = Inspect()
    with native_runtime(tmp_path, monkeypatch, replay) as (resource, controller, tasker):
        run_callback(resource, tasker, inspect)
        report = {"framework": Library.version(), "preparation": preparation,
            "source_frame_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replay_bgr_sha256": hashlib.sha256(replay.tobytes()).hexdigest(),
            "error_type": type(inspect.error).__name__ if inspect.error else None,
            "error": str(inspect.error) if inspect.error else None, "candidate_created": inspect.candidate is not None,
            "recognitions": inspect.recording.recognitions, "capture": asdict(inspect.metadata) if inspect.metadata else None,
            "observed_age_seconds": inspect.age, "capture_count": controller.captures,
            "start_attempted": inspect.combat._entry_start_attempted, "cleanup_confirmed": inspect.cleanup,
            "memory_inputs": controller.inputs, "hardware_inputs": 0,
            "clock": "Real monotonic/capture completion, no fake clock or threshold adjustment"}
        save_report(tmp_path, "native-helper-" + ("p1" if preparation else "negative") + ".json", report)
        assert inspect.cleanup is True and controller.captures == 1
        assert len({row["frame_object_id"] for row in inspect.recording.recognitions}) == 1
        if not preparation:
            assert isinstance(inspect.error, InverseCrownUnavailable)
            assert "preparation_unconfirmed" in str(inspect.error) and controller.inputs == []
        elif inspect.error is None:
            assert inspect.candidate is not None and inspect.candidate.retired
            assert len(controller.inputs) == 1 and controller.inputs[0][0] == "Click"
        else:
            # A real native run may independently exceed 750ms. Accept only
            # the existing stale-frame refusal; never hide an SDK/type error.
            assert isinstance(inspect.error, InverseCrownUnavailable)
            assert "preparation_changed" in str(inspect.error)
            assert inspect.age >= .75 and controller.inputs == []
