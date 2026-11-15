"""Synthetic native color checks for a moved energy bar, without device pixels."""
import numpy as np
import pytest
from maa.custom_action import CustomAction

from action.basics.InterfaceZone.hud_roi_calibration import skill_energy_override_from_cache
from test_hud_roi_calibration import cache
from test_r03_recruitment_native import native_hub


@pytest.mark.parametrize("case,old_hit,new_hit", [
    ("arena", True, False),
    ("ready_cap", False, True),
    ("ready_pulse", True, True),
    ("empty_marker", False, False),
])
def test_calibrated_energy_ignores_arena_and_includes_ready_cap(tmp_path, case, old_hit, new_hit):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        frame[:] = 0
        x, y, width, height = {
            "arena": (18, 52, 15, 10),
            "ready_cap": (282, 62, 9, 5),
            "ready_pulse": (130, 62, 40, 5),
            "empty_marker": (148, 62, 1, 3),
        }[case]
        frame[y:y+height, x:x+width] = np.array([250, 200, 120], dtype=np.uint8)
        errors = []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    before = context.run_recognition("技能_能量条", frame)
                    assert bool(before and before.hit) is old_hit
                    assert context.override_pipeline(skill_energy_override_from_cache(cache(), "adb", (1280, 720)))
                    after = context.run_recognition("技能_能量条", frame)
                    assert bool(after and after.hit) is new_hit
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("EnergyProbe", Probe())
        job = tasker.post_task("EnergyProbe", {"EnergyProbe": {
            "recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "EnergyProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert not errors, errors
        assert job.succeeded and not expired and not inputs
