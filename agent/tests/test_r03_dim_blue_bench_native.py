"""A dim blue bench marker is identity evidence, independent of switch permission."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from action.combat.core.provider import CombatCheck, Roguelike3IdentityError
from action.combat.core.role_factory import create_role
from action.combat.core.session import CombatTask
from action.combat.core.switch import detect_visible_team_colors
from action.combat.core.team import TeamSnapshot, publish_team_roster
from test_r03_recruitment_native import native_hub


ROSTER = {"R": "Oblivion", "B": "GeneralFight", "Y": ""}
MARKER = "矩阵循生_场外蓝位暗色标记"


@pytest.mark.parametrize("sample,expected_hp", [
    ("endgame", (7768, 9832)), ("hp", (5836, 9832)),
    ("background", (6215, 9832)),
])
def test_observed_dim_marker_restores_existing_attacker_without_input(tmp_path, sample, expected_hp):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        results, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    publish_team_roster(context, ROSTER)
                    item = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
                    item.team = TeamSnapshot(**ROSTER, current="B")
                    item.roles = {c: create_role(item, c, item.team.cls_at(c)) for c in item.team.filled_colors()}
                    original_roles = item.roles.copy()
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / f"r03_dim_blue_{sample}_hud.png"))
                    item.frame = frame
                    hp = item.combat_check.read_hp_from_frame(context, frame)
                    assert hp.alive and (hp.current, hp.maximum) == expected_hp
                    assert detect_visible_team_colors(context, frame) == []
                    assert item.refresh_field_role(item.roles["B"]) is True
                    assert item.team.current == "R" and item.field_identity == "confirmed"
                    assert item.roles == original_roles
                    assert {c: item.team.cls_at(c) for c in ROSTER} == ROSTER
                    assert item.refresh_field_role(item.roles["R"]) is False
                    # Observation never grants a switch: original QTE detection stays unchanged.
                    assert detect_visible_team_colors(context, frame) == []
                    results.append(True)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("DimBenchProbe", Probe())
        job = tasker.post_task("DimBenchProbe", {
            "DimBenchProbe": {"recognition": {"type": "DirectHit"},
                              "action": {"type": "Custom", "param": {"custom_action": "DimBenchProbe"}},
                              "next": [], "on_error": []},
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and results == [True] and not inputs


@pytest.mark.parametrize("invalid", ["blank", "red", "removed", "wrong_hue", "blue_blob"])
def test_marker_requires_real_blue_pattern_and_refuses_unobserved_field(tmp_path, invalid):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        errors, observed = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    fixture = "r03_guili_return_hud.png" if invalid == "red" else "r03_dim_blue_endgame_hud.png"
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / fixture))
                    if invalid == "blank":
                        frame[:] = 0
                    elif invalid == "blue_blob":
                        frame[:] = 0
                        frame[177:208, 1178:1208] = (80, 30, 0)
                    elif invalid == "removed":
                        frame[170:215,1170:1218] = 0
                    elif invalid == "wrong_hue":
                        patch = frame[177:208,1178:1208]
                        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                        mask = cv2.inRange(hsv, (100,80,15), (130,255,255)) > 0
                        hsv[mask, 0] = 25
                        patch[:] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                    hit = context.run_recognition(MARKER, frame)
                    assert not hit or not hit.hit
                    observed.append(invalid)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("DimBenchNegative", Probe())
        job = tasker.post_task("DimBenchNegative", {
            "DimBenchNegative": {"recognition": {"type": "DirectHit"},
                                 "action": {"type": "Custom", "param": {"custom_action": "DimBenchNegative"}},
                                 "next": [], "on_error": []},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and observed == [invalid] and not inputs
