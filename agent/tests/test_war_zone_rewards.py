"""D07's real 720p Go button must end rewards without claiming again.

For native OCR replay, point MAA_WAR_ZONE_OCR_RESOURCE at a resource bundle
containing model/ocr (normally resource/base after model initialization).
The fixture is only the exact reward button crop, with no account metadata.
This replay is offline; it does not prove the game task returns successfully.
"""

from pathlib import Path
import os
import re

import jsonc
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads((ROOT / "resource/base/pipeline/War_Zone.jsonc").read_text(encoding="utf-8"))
FIXTURES = Path(__file__).parent / "fixtures/war_zone_rewards"


@pytest.mark.parametrize("text,claimable", [
    ("领取", True), ("前往", False), ("已领取", False),
    ("一键领取", False), ("领取详情", False),
])
def test_claim_does_not_match_navigation_or_already_claimed_labels(text, claimable):
    expected = NODES["领取战区奖励"]["recognition"]["param"]["expected"]
    assert bool(re.search(expected, text)) is claimable


@pytest.mark.parametrize("use_original_roi,expected_hit", [(True, False), (False, True)])
@pytest.mark.parametrize(
    "fixture_name,node_name,crop_rect,original_roi,full_text,cropped_text",
    [
        ("go_button.png", "战区奖励已领取", (1000, 159, 230, 65),
         (1054, 159, 176, 65), "前往", "往"),
        ("challenge_button.png", "打开挑战_纷争战区", (1090, 656, 114, 53),
         (1130, 656, 74, 53), "挑战", "战"),
    ],
)
def test_native_ocr_replays_complete_button(
    tmp_path, use_original_roi, expected_hit,
    fixture_name, node_name, crop_rect, original_roi, full_text, cropped_text,
):
    import cv2
    from maa.controller import CustomController
    from maa.pipeline import JOCR
    from maa.resource import Resource
    from maa.tasker import Tasker

    configured = os.environ.get("MAA_WAR_ZONE_OCR_RESOURCE")
    bundle = Path(configured) if configured else ROOT / "resource/base"
    if not (bundle / "model/ocr/rec.onnx").is_file():
        if configured:
            pytest.fail("MAA_WAR_ZONE_OCR_RESOURCE has no model/ocr/rec.onnx")
        pytest.skip("OCR model submodule is not initialized; set MAA_WAR_ZONE_OCR_RESOURCE")

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-war-zone-reward-replay"
        def get_features(self): return 0
        def screencap(self): raise AssertionError("Replay must use the saved image")
        def click(self, *args): raise AssertionError("No input during OCR replay")
        def swipe(self, *args): raise AssertionError("No input during OCR replay")
        def touch_down(self, *args): raise AssertionError("No input during OCR replay")
        def touch_move(self, *args): raise AssertionError("No input during OCR replay")
        def touch_up(self, *args): raise AssertionError("No input during OCR replay")
        def click_key(self, *args): raise AssertionError("No input during OCR replay")
        def input_text(self, *args): raise AssertionError("No input during OCR replay")
        def key_down(self, *args): raise AssertionError("No input during OCR replay")
        def key_up(self, *args): raise AssertionError("No input during OCR replay")
        def start_app(self, *args): raise AssertionError("No App input during OCR replay")
        def stop_app(self, *args): raise AssertionError("No App input during OCR replay")

    crop = cv2.imdecode(np.fromfile(FIXTURES / fixture_name, dtype=np.uint8), cv2.IMREAD_COLOR)
    x, y, width, height = crop_rect
    assert crop.shape == (height, width, 3)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[y:y + height, x:x + width] = crop
    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    controller = ReadOnlyController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    params = dict(NODES[node_name]["recognition"]["param"])
    if use_original_roi:
        params["roi"] = list(original_roi)
    task = tasker.post_recognition("OCR", JOCR(**params), image).wait().get()
    recognition = task.nodes[0].recognition
    assert recognition.hit is expected_hit
    texts = [row["text"] for row in recognition.raw_detail["all"]]
    assert (cropped_text if use_original_roi else full_text) in texts
