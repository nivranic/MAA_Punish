"""Replay the R03 lightning filter that ignored an observed right-edge click."""
import json
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from test_r03_recruitment_native import ROOT, native_hub


@pytest.mark.parametrize("source", ["owned", "trial"])
def test_r03_lightning_filter_clicks_stay_in_observed_working_interior(tmp_path, source):
    patch = json.loads((ROOT / "tasks/矩阵循生.json").read_text(encoding="utf-8"))["task"][0]["pipeline_override"]
    fixtures = EVIDENCE_DIR
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        errors = []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    frame[:] = cv2.imread(str(fixtures / f"r03_lightning_filter_{source}.png"))
                    hit = context.run_recognition("打开雷属性", frame)
                    assert hit and hit.hit and list(hit.box) == [77, 336, 41, 38]
                    # run_action below refuses input and has no recognition UID.
                    # Resolve the production reference ROI from the real hit;
                    # retain its offset and every production color parameter.
                    confirmation_roi = {"确认进入雷属性": {
                        "recognition": {"param": {"roi": list(hit.box)}},
                    }}
                    before = context.run_recognition("确认进入雷属性", frame, confirmation_roi)
                    assert before is None or not before.hit
                    for _ in range(16):
                        # Fake input is recorded and refused; no phone is used.
                        action = context.run_action("打开雷属性", hit.box)
                        assert action and not action.success
                        x, y = inputs[-1]
                        assert 91 <= x <= 104 and 348 <= y <= 362, (x, y)
                    frame[:] = cv2.imread(str(fixtures / "r03_lightning_filter_selected.png"))
                    after = context.run_recognition("确认进入雷属性", frame, confirmation_roi)
                    assert after and after.hit
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("LightningFilterProbe", Probe())
        job = tasker.post_task("LightningFilterProbe", {
            **patch,
            "LightningFilterProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "LightningFilterProbe"}},
                "next": [], "on_error": [],
            },
            "打开雷属性": {
                **patch.get("打开雷属性", {}),
                "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
            },
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and len(inputs) == 16
