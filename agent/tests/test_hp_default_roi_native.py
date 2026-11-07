"""Replay actual HP pixels through native OCR and the strict supplied-frame reader."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from action.basics.InterfaceZone import roi_zone_controller as zones
from action.basics.InterfaceZone.hud_roi_calibration import (
    HUD_CALIBRATION_KEY,
    hud_overrides_from_cache,
    make_hud_calibration,
)
from action.combat.core.provider import CombatCheck
from test_r03_recruitment_native import native_hub


FIXTURES = EVIDENCE_DIR


@pytest.mark.parametrize("sample,expected", [
    ("five_digit", (9433, 10282, "alive")),
    ("four_digit", (8035, 9832, "alive")),
    ("blank", (None, None, "hp_not_recognized")),
    ("calibrated", (9433, 10282, "alive")),
])
def test_default_hp_roi_and_explicit_calibration(tmp_path, sample, expected):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        if sample in ("five_digit", "calibrated"):
            crop = cv2.imread(str(FIXTURES / "hp_five_digit.png"))
            assert crop is not None and crop.shape == (29, 190, 3)
            frame[15:44, 19:209] = crop
        elif sample == "four_digit":
            image = cv2.imread(str(FIXTURES / "r03_multi_oblivion_hud.png"))
            assert image is not None and image.shape == frame.shape
            frame[:] = image
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    if sample == "calibrated":
                        layout = {
                            key: dict(default=list(rect), recognized=list(rect),
                                      offset=[0, 0, 0, 0], hit=True)
                            for key, _, rect in zones.zones_for("adb")
                        }
                        payload = dict(
                            schema=1, screen_size=[1280, 720],
                            source_frame_sha256="a" * 64,
                            nodes={"检查血量": [70, 15, 140, 29],
                                   "检查逆冕能量空": [75, 62, 215, 3]},
                        )
                        layout[HUD_CALIBRATION_KEY] = make_hud_calibration(
                            payload, "adb", (1280, 720), layout,
                        )
                        assert context.override_pipeline(
                            hud_overrides_from_cache(layout, "adb", (1280, 720)),
                        )
                        assert context.get_node_data("检查血量")["recognition"]["param"]["roi"] == [70, 15, 140, 29]
                    hp = CombatCheck().read_hp_from_frame(context, frame)
                    observations.append((hp.current, hp.maximum, hp.reason))
                    if sample == "five_digit":
                        # The observed old crop loses the last maximum digit;
                        # strict parsing must continue to reject that evidence.
                        assert context.override_pipeline({"检查血量": {
                            "recognition": {"param": {"roi": [19, 15, 141, 29]}},
                        }})
                        old = CombatCheck().read_hp_from_frame(context, frame)
                        assert old.text == "9433/1028"
                        assert old.reason == "hp_out_of_range" and not old.alive
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("HpRoiProbe", Probe())
        job = tasker.post_task("HpRoiProbe", {"HpRoiProbe": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "HpRoiProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert not errors, errors
        assert job.succeeded and not expired and not inputs
        assert observations == [expected]
