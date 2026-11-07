"""Real workshop title/collect pixels; simulated collection returns to real hub."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from action.basics.PPOverride import PPOverride
from test_r03_recruitment_native import native_hub


@pytest.mark.parametrize("entry", ["事件确定_矩阵循生", "矩阵循生"])
def test_workshop_collect_returns_to_board_without_ending_run(tmp_path, entry):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        directory = EVIDENCE_DIR
        image = cv2.imread(str(directory / "r03_hidden_workshop.png"))
        assert image is not None and image.shape == frame.shape
        frame[:] = image
        original_confirmation = resource.get_node_data("事件确定_矩阵循生")["recognition"]

        class Begin(CustomAction):
            def run(self, context, argv):
                context.override_pipeline({"事件确定_矩阵循生": {"recognition": original_confirmation}})
                return True

        class Collect(CustomAction):
            def run(self, context, argv):
                frame[:] = 0
                # This transition is simulated, not a device collection claim.
                frame[627:682, 700:1166] = cv2.imread(str(directory / "r03_hub_controls.png"))
                return True

        assert resource.register_custom_action("PPOverride", PPOverride())
        assert resource.register_custom_action("R03WorkshopBegin", Begin())
        assert resource.register_custom_action("R03WorkshopCollect", Collect())
        job = tasker.post_task(entry, {
            "事件确定_矩阵循生": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "R03WorkshopBegin"}},
                "post_delay": 0,
            },
            "隐秘工坊_对话": {
                "action": {"type": "Custom", "param": {"custom_action": "R03WorkshopCollect"}},
                "post_delay": 0,
            },
            "事件完成_矩阵循生": {"post_delay": 0},
            "事件完成_二次确认_矩阵循生": {"action": {"type": "StopTask"}},
            "事件完成_结束_二次确认_矩阵循生": {"action": {"type": "StopTask"}},
            "未知事件_矩阵循生": {"action": {"type": "StopTask"}},
        }).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names == [entry, "隐秘工坊_矩阵循生", "隐秘工坊_对话",
                         "事件完成_矩阵循生", "事件完成_二次确认_矩阵循生"]
