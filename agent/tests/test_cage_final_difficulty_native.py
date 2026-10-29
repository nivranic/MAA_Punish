"""Replay D06's saved Hell/zero-score page through production And/Or nodes.

Set MAA_CAGE_OCR_RESOURCE to an existing resource bundle with model/ocr.
The two public crops contain no account metadata and retain their original
720p coordinates. Missing-crop cases test necessary inputs, not real game pages.
Offline recognition does not accept stamina handling or the full Cage flow.
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
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker


ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads((ROOT / "resource/base/pipeline/Phantom_Pain_Cage.jsonc").read_text(encoding="utf-8"))
FIXTURES = Path(__file__).parent / "fixtures/cage_final_difficulty"
PROVENANCE = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
ORIGINAL_ROI = [794, 141, 413, 117]
RECOGNITIONS = (
    "检查是否是最终难度", "没有分数", "是最终难度但是没有分数",
    "需要进行作战", "作战准备", "没打满", "A区退出", "B区退出", "C区退出",
)


class ReadOnlyController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "offline-cage-final-difficulty-replay"
    def get_features(self): return 0
    def screencap(self): raise AssertionError("Saved replay image must be explicit")
    def deny(self, *args): raise AssertionError("No input or app operations during saved-frame replay")
    click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = deny


def fixture_page(*, omit=None):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for key, row in PROVENANCE["crops"].items():
        raw = (FIXTURES / row["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        crop = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        x, y, width, height = row["rect"]
        assert crop.shape == (height, width, 3)
        if key != omit:
            image[y:y + height, x:x + width] = crop
    return image


def replay(tmp_path, image, *, original_roi=False):
    configured = os.environ.get("MAA_CAGE_OCR_RESOURCE")
    bundle = Path(configured) if configured else ROOT / "resource/base"
    if not (bundle / "model/ocr/rec.onnx").is_file():
        if configured:
            pytest.fail("MAA_CAGE_OCR_RESOURCE has no model/ocr/rec.onnx")
        pytest.skip("Initialize OCR models or set MAA_CAGE_OCR_RESOURCE")
    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), ReadOnlyController(), Tasker()
    # A model-bearing external bundle is loaded once; current pipeline bytes
    # are overlaid separately only when it is a different resource path.
    assert resource.post_bundle(bundle).wait().succeeded
    if bundle.resolve() != (ROOT / "resource/base").resolve():
        assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    overrides = deepcopy(NODES)
    if original_roi:
        overrides["检查是否是最终难度"]["recognition"]["param"]["roi"] = ORIGINAL_ROI
    results, errors = {}, []

    class Inspect(CustomAction):
        def run(self, context, argv):
            try:
                for name in RECOGNITIONS:
                    result = context.run_recognition(name, image, overrides)
                    assert result is not None, name
                    results[name] = dict(hit=result.hit, raw_detail=result.raw_detail)
                return CustomAction.RunResult(success=True)
            except Exception as error:
                # Exceptions escaping ctypes callbacks can leave Tasker marked
                # succeeded, so transfer errors to the outer pytest assertion.
                errors.append(repr(error))
                return CustomAction.RunResult(success=False)

    assert resource.register_custom_action("OfflineCageInspect", Inspect())
    job = tasker.post_task("OfflineCageInspect", {"OfflineCageInspect": {
        "recognition": {"type": "DirectHit"},
        "action": {"type": "Custom", "param": {"custom_action": "OfflineCageInspect"}},
        "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0,
    }})
    job.wait()
    assert errors == []
    assert job.succeeded and not tasker.running and not tasker.stopping
    assert set(results) == set(RECOGNITIONS)
    return results


@pytest.mark.parametrize("original_roi,expected_hit", [(True, False), (False, True)])
def test_hell_zero_score_reaches_prepare_without_becoming_completed(tmp_path, original_roi, expected_hit):
    results = replay(tmp_path, fixture_page(), original_roi=original_roi)
    assert results["检查是否是最终难度"]["hit"] is expected_hit
    texts = [row["text"] for row in results["检查是否是最终难度"]["raw_detail"]["all"]]
    assert ("狱" if original_roi else "地狱") in texts
    assert results["没有分数"]["hit"] is True
    assert results["作战准备"]["hit"] is True
    for node in ("是最终难度但是没有分数", "需要进行作战", "没打满"):
        assert results[node]["hit"] is expected_hit, node
    for node in ("A区退出", "B区退出", "C区退出"):
        assert results[node]["hit"] is False, node


@pytest.mark.parametrize("omit", ["hell_zero", "prepare_button"])
def test_missing_required_pixels_does_not_match_ready_to_fight(tmp_path, omit):
    results = replay(tmp_path, fixture_page(omit=omit))
    assert results["没打满"]["hit"] is False
    assert results["检查是否是最终难度"]["hit"] is (omit != "hell_zero")
    assert results["作战准备"]["hit"] is (omit != "prepare_button")
    for node in ("A区退出", "B区退出", "C区退出"):
        assert results[node]["hit"] is False, node
