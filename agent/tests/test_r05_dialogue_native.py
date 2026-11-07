"""Native recognition of the R05 story continuation button; no device input."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer
import os

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = EVIDENCE_DIR / "r05_story_continue.png"


@pytest.mark.parametrize("scene", ["story", "ellipsis", "blank", "red_only", "text_only"])
def test_story_continue_requires_red_button_and_text(tmp_path, scene):
    # The fixture contains only the button area, without account or story data.
    crop = cv2.imread(str(FIXTURE))
    assert crop is not None
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    if scene == "ellipsis":
        crop = cv2.imread(str(FIXTURE.parent / "r05_ellipsis_continue.png"))
        assert crop is not None
    if scene in ("story", "ellipsis"):
        frame[632:687, 1020:1247] = crop
    elif scene == "red_only":
        frame[638:682, 1031:1200] = (30, 20, 190)
    elif scene == "text_only":
        cv2.putText(frame, "CONTINUE", (1035, 666), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (200, 200, 200), 2)

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r05-story-recognition"
        def get_features(self): return 0
        def screencap(self): return frame

    hits = []

    class Recognize(CustomAction):
        def run(self, context, argv):
            result = context.run_recognition("通用事件处理_对话1", frame)
            hits.append(result.hit)
            return True

    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    assert resource.register_custom_action("R05StoryRecognition", Recognize())
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(15, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task("R05StoryRecognition", {"R05StoryRecognition": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {
                "custom_action": "R05StoryRecognition",
            }},
            "next": [], "on_error": [],
        }}).wait()
        assert not expired and job.succeeded
        assert hits == [scene in ("story", "ellipsis")]
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


def test_skill_selection_click_lands_inside_visible_icon(tmp_path):
    """The observed first icon's inner hit area excludes the old paper-edge tap."""
    accepted = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r05-skill-hit-area"
        def get_features(self): return 0
        def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)

        def click(self, x, y, contact=0, pressure=1):
            accepted.append(148 <= x <= 212 and 393 <= y <= 447)
            return accepted[-1]

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    watchdog = Timer(15, tasker.post_stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task("选择技能_神寂启示录", {"选择技能_神寂启示录": {
            "recognition": {"type": "DirectHit"}, "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and accepted == [True]
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


def test_existing_adventure_resumes_into_battle_dispatch_without_terminate(tmp_path):
    """Replay real lobby and preparation crops through the production root."""
    # Agent CI does not initialize the optional OCR model submodule. A model-only
    # bundle lets local integration checks use existing models without changing it.
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE for the native replay")
    lobby = np.zeros((720, 1280, 3), dtype=np.uint8)
    prepare = lobby.copy()
    fixture_dir = FIXTURE.parent
    lobby_crop = cv2.imread(str(fixture_dir / "r05_resume_lobby.png"))
    battle_crop = cv2.imread(str(fixture_dir / "r05_prepare_battle.png"))
    assert lobby_crop is not None and battle_crop is not None
    lobby[397:546, 498:818] = lobby_crop
    prepare[569:719, 1039:1279] = battle_crop
    inputs = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r05-resume-adventure"
        def get_features(self): return 0
        def screencap(self): return prepare if inputs else lobby

        def click(self, x, y, contact=0, pressure=1):
            if not inputs:
                # The ongoing-adventure badge is above the destructive button.
                valid = 570 <= x <= 700 and 350 <= y <= 460
            else:
                valid = 1039 <= x < 1279 and 620 <= y < 710
            inputs.append((x, y, valid))
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
        job = tasker.post_task("神寂启示录", {
            # End only after the real preparation recognition/action runs.
            "点击开战_神寂启示录": {"next": ["R05ResumeTestEnd"]},
            "R05ResumeTestEnd": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "StopTask"}, "next": [], "on_error": [],
            },
        }).wait()
        assert job.succeeded and not expired
        assert len(inputs) == 2 and all(valid for _, _, valid in inputs)
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert "继续冒险_神寂启示录" in names
        assert "神寂启示录_事件分派" in names
        assert "终止_神寂启示录" not in names
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("return_scene", ["members", "new_character"])
def test_depleted_adventure_collects_settlement_before_stopping(tmp_path, return_scene):
    """Collect rewards, handle an optional unlock story, then stop at preparation."""
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE for the native replay")
    fixture = cv2.imread(str(FIXTURE.parent / "r05_settlement_stages.png"))
    assert fixture is not None and fixture.shape == (2880, 1280, 3)
    frames = [fixture[i * 720:(i + 1) * 720] for i in range(4)]
    areas = [(1014, 600, 1235, 720), (939, 589, 1051, 641),
             (888, 582, 1078, 646)]
    if return_scene == "new_character":
        unlock = cv2.imread(str(FIXTURE.parent / "r05_settlement_unlock.png"))
        assert unlock is not None and unlock.shape == (1440, 1280, 3)
        frames = frames[:3] + [unlock[:720], unlock[720:]]
        areas.append((1037, 9, 1279, 71))
    inputs = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r05-settlement-sequence"
        def get_features(self): return 0
        def screencap(self): return frames[min(len(inputs), len(frames) - 1)]

        def click(self, x, y, contact=0, pressure=1):
            if len(inputs) >= len(areas):
                inputs.append(False)
                return False
            x1, y1, x2, y2 = areas[len(inputs)]
            valid = x1 <= x < x2 and y1 <= y < y2
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
        job = tasker.post_task("失败_神寂启示录").wait()
        assert job.succeeded and not expired, {"inputs": inputs, "watchdog": expired}
        assert inputs == [True] * len(areas)
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert "对局结束_神寂启示录" in names
        assert "神寂启示录_退出结算" in names
        assert ("跳过剧情" in names) == (return_scene == "new_character")
        assert names[-1] == "神寂启示录_对局结束停止"
        assert "返回主菜单_err" not in names
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
