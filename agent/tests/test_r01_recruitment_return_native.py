"""R01 nested recruitment must unwind after actor selection reaches the map."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from test_r03_recruitment_native import native_hub


STARTS = ("开始招募_宣叙妄响", "开始招募_倒序_宣叙妄响")
RETURN = "宣叙妄响内部_reco"


def load_map_hud(frame):
    crop = cv2.imread(str(EVIDENCE_DIR / "r01_recruitment_return_hud.png"))
    assert crop is not None and crop.shape == (95, 350, 3)
    frame[610:705, 930:1280] = crop


@pytest.mark.parametrize("entry", STARTS)
def test_recruitment_unwinds_on_real_map_before_any_more_recruitment(tmp_path, entry):
    with native_hub(tmp_path, visible=False) as (_, tasker, frame, inputs, expired):
        load_map_hud(frame)
        # Actor selection already moved the game to the map. Keep production
        # candidates and their recognition; shorten only the failure budget.
        job = tasker.post_task(entry, {
            entry: {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "DoNothing"}, "timeout": 200,
            },
            "返回主菜单_err": {"action": {"type": "StopTask"}},
        }).wait()
        names = [tasker.get_node_detail(n).name
                 for n in tasker.get_task_detail(job.job_id).node_id_list]
        assert job.succeeded and not inputs and not expired
        assert names == [entry, RETURN]


@pytest.mark.parametrize("visible", [True, False])
def test_map_return_still_requires_the_map_hud(tmp_path, visible):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        if visible:
            load_map_hud(frame)
        hits, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    result = context.run_recognition(RETURN, frame)
                    hits.append(bool(result and result.hit))
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("R01MapProbe", Probe())
        job = tasker.post_task("R01MapProbe", {"R01MapProbe": {
            "action": {"type": "Custom", "param": {"custom_action": "R01MapProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not errors and not expired and not inputs
        assert hits == [visible]
