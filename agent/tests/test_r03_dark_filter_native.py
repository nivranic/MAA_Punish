"""R03 dark-filter clicks stay inside the observed reliable icon interior."""
import json
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
from maa.custom_action import CustomAction

from test_r03_recruitment_native import ROOT, native_hub


def test_production_r03_dark_filter_excludes_observed_dead_edge(tmp_path):
    patch = json.loads((ROOT / "tasks/矩阵循生.json").read_text(encoding="utf-8"))["task"][0]["pipeline_override"]
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        errors = []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / "r03_dark_filter.png"))
                    hit = context.run_recognition("打开暗属性", frame)
                    assert hit and hit.hit and list(hit.box) == [67, 400, 53, 46]
                    for _ in range(16):
                        # The fake controller records and refuses every click.
                        action = context.run_action("打开暗属性", hit.box)
                        assert action and not action.success
                        x, y = inputs[-1]
                        assert 85 <= x <= 102 and 413 <= y <= 433, (x, y)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("DarkFilterProbe", Probe())
        job = tasker.post_task("DarkFilterProbe", {
            **patch,
            "DarkFilterProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "DarkFilterProbe"}},
                "next": [], "on_error": [],
            },
            "打开暗属性": {
                **patch.get("打开暗属性", {}),
                "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
            },
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and len(inputs) == 16
