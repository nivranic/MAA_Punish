"""Replay title pixels through the production R03 event selection order.

Set MAA_R03_OCR_MODEL to an existing directory containing det.onnx, rec.onnx,
and keys.txt. These saved crops verify recognition only; no task or input runs.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import cv2
import jsonc
import numpy as np
import pytest
from maa.controller import CustomController
from maa.pipeline import JOr
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures/r03_partner_event_title"
PROVENANCE = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
PIPELINE = ROOT / "resource/base/pipeline/Roguelike_3"
EVENTS = jsonc.loads((PIPELINE / "RogueLike_3_Event.jsonc").read_text(encoding="utf-8"))
FLOW = jsonc.loads((PIPELINE / "Roguelike_3.jsonc").read_text(encoding="utf-8"))


class RecognitionOnlyController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "offline-r03-event-title-recognition"
    def get_features(self): return 0
    def deny(self, *args): raise AssertionError("No controller operations in saved-frame recognition")
    screencap = click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = deny


def saved_title(name):
    row = PROVENANCE["crops"][name]
    raw = (FIXTURES / row["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row["sha256"]
    crop = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    x, y, width, height = row["rect"]
    assert crop.shape == (height, width, 3)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[y:y + height, x:x + width] = crop
    return image


@pytest.mark.parametrize("frame,original_roi,expected", [
    ("partner_title", True, "未知事件_矩阵循生"),
    ("partner_title", False, "贴心搭档_矩阵循生"),
    ("map_negative", False, None),
])
def test_event_selection_keeps_full_title_before_unknown_fallback(tmp_path, frame, original_roi, expected):
    configured = os.environ.get("MAA_R03_OCR_MODEL")
    model = Path(configured) if configured else ROOT / "resource/base/model/ocr"
    if not all((model / name).is_file() for name in ("det.onnx", "rec.onnx", "keys.txt")):
        if configured:
            pytest.fail("MAA_R03_OCR_MODEL does not contain the three OCR model files")
        pytest.skip("Initialize OCR models or set MAA_R03_OCR_MODEL")
    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert resource.post_ocr_model(model).wait().succeeded
    if original_roi:
        original = deepcopy(EVENTS["贴心搭档_矩阵循生"])
        original["recognition"]["param"]["roi"] = [700, 96, 254, 62]
        assert resource.override_pipeline({"贴心搭档_矩阵循生": original})
    controller, tasker = RecognitionOnlyController(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    # Consume the actual ordered next list, including the original unknown event
    # fallback, rather than testing an isolated OCR definition.
    result = tasker.post_recognition("Or", JOr(
        any_of=FLOW["事件确定_矩阵循生"]["next"]), saved_title(frame)).wait().get()
    assert result is not None
    recognition = result.nodes[0].recognition
    assert recognition is not None
    hits = [row["name"] for row in recognition.raw_detail if row["box"] is not None]
    assert hits == ([] if expected is None else [expected])
    assert recognition.hit is (expected is not None)
    assert not tasker.running and not tasker.stopping
