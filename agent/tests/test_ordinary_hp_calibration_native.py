"""Ordinary combat uses the saved HP rectangle through the real loader."""
import json

import cv2
import pytest
from maa.custom_action import CustomAction

from action.basics.InterfaceZone import roi_zone_controller as policy
from action.basics.InterfaceZone.LoadRoiZone import LoadRoiZone
from action.combat.core.provider import CombatCheck
from test_hud_roi_calibration import cache
from test_r03_recruitment_native import native_hub


@pytest.mark.parametrize("text,reason,current", [
    ("4800/9000", "alive", 4800),
    ("0/9000", "dead", 0),
    ("9800/9000", "hp_out_of_range", None),
])
def test_ordinary_hp_load_and_strict_read(tmp_path, monkeypatch, text, reason, current):
    path = tmp_path / "adb_zone_offset.json"
    path.write_text(json.dumps(cache()), encoding="utf-8")
    monkeypatch.setitem(policy.OFFSET_FILES, "adb", path)
    before = path.read_bytes()
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        frame[:] = 0
        # A separate background glyph is outside the saved HP text rectangle.
        cv2.putText(frame, "C", (25, 35), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
        cv2.putText(frame, text, (75, 35), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
        assert tasker.controller.post_screencap().wait().succeeded
        errors = []
        loader = LoadRoiZone()

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    loader.apply_offsets(context, "adb")
                    node = context.get_node_data("检查血量")
                    assert node["recognition"]["param"]["roi"] == [70, 15, 140, 29]
                    hp = CombatCheck().read_hp_from_frame(context, frame)
                    assert (hp.reason, hp.current) == (reason, current), hp
                    loader.apply_offsets(context, "adb")
                    assert context.get_node_data("检查血量") == node
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("HpCalibrationProbe", Probe())
        job = tasker.post_task("HpCalibrationProbe", {"HpCalibrationProbe": {
            "recognition": "DirectHit",
            "action": {"type": "Custom", "param": {"custom_action": "HpCalibrationProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert not errors, errors
        assert job.succeeded and not expired and not inputs
    assert path.read_bytes() == before
