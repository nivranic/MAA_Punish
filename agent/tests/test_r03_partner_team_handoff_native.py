"""Replay the event-to-team handoff without tasks, actions, or device input."""
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from maa.pipeline import JOr
from maa.resource import Resource
from maa.tasker import Tasker

from test_r03_partner_event_title_native import (
    EVENTS, ROOT, RecognitionOnlyController, saved_title,
)


FIXTURES = Path(__file__).parent / "fixtures/r03_partner_team_handoff"
PROVENANCE = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
HANDOFF = "贴心搭档_战斗承接_矩阵循生"


def saved_party(omit=None):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for key, row in PROVENANCE["crops"].items():
        raw = (FIXTURES / row["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        crop = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        x, y, width, height = row["rect"]
        assert crop.shape == (height, width, 3)
        if key != omit:
            image[y:y + height, x:x + width] = crop
    return image


@pytest.mark.parametrize("frame,original_next,expected", [
    ("party", True, None),
    ("party", False, HANDOFF),
    ("without_team_adjustment", False, None),
    ("without_deploy", False, None),
    ("map_negative", False, None),
    ("partner_title", False, None),
])
def test_event_parent_requires_both_team_labels_before_handoff(tmp_path, frame, original_next, expected):
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
    controller, tasker = RecognitionOnlyController(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    references = EVENTS["贴心搭档_矩阵循生"]["next"]
    names = [name.removeprefix("[JumpBack]") for name in references]
    if original_next:
        names.remove(HANDOFF)
    if frame.startswith("without_"):
        image = saved_party(omit=frame.removeprefix("without_"))
    elif frame == "party":
        image = saved_party()
    else:
        image = saved_title(frame)
    result = tasker.post_recognition("Or", JOr(any_of=names), image).wait().get()
    assert result is not None
    recognition = result.nodes[0].recognition
    assert recognition is not None
    hits = [row["name"] for row in recognition.raw_detail if row["box"] is not None]
    assert hits == ([] if expected is None else [expected])
    assert recognition.hit is (expected is not None)
    assert not tasker.running and not tasker.stopping
