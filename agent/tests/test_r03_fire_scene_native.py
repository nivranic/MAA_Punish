"""Actual event titles must select known handlers before the unknown fallback."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import jsonc
import pytest
from maa.custom_action import CustomAction

from test_r03_recruitment_native import native_hub


ROOT = Path(__file__).resolve().parents[2]
SAMPLES = {
    "fire": ("火灾现场_矩阵循生", "火灾现场_对话", "r03_fire_scene_event.png", "灾现场"),
    "fatal": ("致命失误_矩阵循生", "致命失误_对话2", "r03_fatal_error_event.png", "命失误"),
}


def load_frame(frame, sample):
    image = cv2.imread(str(EVIDENCE_DIR / SAMPLES[sample][2]))
    assert image is not None and image.shape == frame.shape
    frame[:] = image


@pytest.mark.parametrize("visible", [True, False])
@pytest.mark.parametrize("sample", SAMPLES)
def test_known_title_dispatch_and_existing_choice(tmp_path, visible, sample):
    event, choice_name, _, old_prefix = SAMPLES[sample]
    pipeline = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_3/Roguelike_3.jsonc").read_text(encoding="utf-8"))
    names = pipeline["事件确定_矩阵循生"]["next"]
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        if visible:
            load_frame(frame, sample)
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    selected = context.run_recognition("Or", frame, {"Or": {
                        "recognition": {"type": "Or", "param": {"any_of": names}},
                    }})
                    hits = [row["name"] for row in selected.raw_detail if row["box"] is not None]
                    choice = context.run_recognition(choice_name, frame)
                    observations.append((selected.hit, hits, choice.hit))
                    if visible:
                        # The old edge cuts the first character from both real
                        # titles; strict event matching must continue to reject it.
                        old = context.run_recognition(event, frame, {event: {
                            "recognition": {"param": {"roi": [700, 96, 254, 62]}},
                        }})
                        assert not old.hit
                        assert old.raw_detail["all"][0]["text"].startswith(old_prefix)
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("FireSceneProbe", Probe())
        job = tasker.post_task("FireSceneProbe", {"FireSceneProbe": {
            "action": {"type": "Custom", "param": {"custom_action": "FireSceneProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert not errors, errors
        assert job.succeeded and not inputs and not expired
        assert observations == ([(True, [event], True)] if visible else [(False, [], False)])


@pytest.mark.parametrize("sample", SAMPLES)
def test_actual_page_enters_known_chain_before_unknown_fallback(tmp_path, sample):
    event = SAMPLES[sample][0]
    with native_hub(tmp_path, visible=False) as (_, tasker, frame, inputs, expired):
        load_frame(frame, sample)
        job = tasker.post_task("事件确定_矩阵循生", {
            "事件确定_矩阵循生": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "DoNothing"}, "post_delay": 0,
            },
            # Stop before the first event action; keep real title OCR and
            # production dispatch order. This is routing, not game acceptance.
            event: {"action": {"type": "StopTask"}},
        }).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names == ["事件确定_矩阵循生", event]
