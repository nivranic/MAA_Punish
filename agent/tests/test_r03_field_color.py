"""Restore only an unambiguous, positively observed R03 field color."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from action.combat.core import session
from action.combat.core.provider import CombatCheck, HpObservation, Roguelike3IdentityError
from action.combat.core.role_factory import create_role
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot, publish_team_roster
from test_r03_recruitment_native import native_hub
from test_support.fakes import FakeContext


def pair(context):
    item = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
    item.team = TeamSnapshot(R="Oblivion", B="GeneralFight", Y="", current="B")
    item.roles = {color: create_role(item, color, item.team.cls_at(color))
                  for color in item.team.filled_colors()}
    return item


def test_real_field_return_selects_the_existing_role_without_rebinding_or_input(tmp_path):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    publish_team_roster(context, {"R": "Oblivion", "B": "GeneralFight", "Y": ""})
                    item = pair(context)
                    roles = item.roles.copy()
                    roster = (item.team.R, item.team.B, item.team.Y)
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / "r03_field_return_hud.png"))
                    item.frame = frame
                    hp = item.combat_check.read_hp_from_frame(context, frame)
                    assert hp.alive and (hp.current, hp.maximum) == (3298, 9832)
                    assert item.refresh_field_role(item.roles["B"]) is True
                    assert item.team.current == "R" and item.field_identity == "confirmed"
                    assert (item.team.R, item.team.B, item.team.Y) == roster
                    assert item.roles == roles
                    assert item.refresh_field_role(item.roles["R"]) is False
                    observations.append(True)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("R03FieldColorProbe", Probe())
        job = tasker.post_task("R03FieldColorProbe", {
            "R03FieldColorProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "R03FieldColorProbe"}},
                "next": [], "on_error": [],
            },
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and observations == [True] and not inputs


@pytest.mark.parametrize("invalid", ["unpublished", "duplicate", "field_qte", "extra_qte",
                                   "missing_old_qte", "dead", "fallback", "generic"])
def test_field_color_requires_positive_unambiguous_same_frame_evidence(monkeypatch, invalid):
    context = FakeContext()
    item = pair(context)
    item.frame = object()
    display, cls_name, colors = "终焉", "Oblivion", ["B"]
    if invalid == "unpublished":
        cls_name = "Spectre"
    elif invalid == "duplicate":
        item.team.Y = "Oblivion"
    elif invalid == "field_qte":
        colors = ["R", "B"]
    elif invalid == "extra_qte":
        colors = ["B", "Y"]
    elif invalid == "missing_old_qte":
        colors = []
    elif invalid == "fallback":
        item.roles["R"].fallback_reason = "requested_strategy_unavailable"
    elif invalid == "generic":
        item.team.current = "R"
        display, cls_name, colors = "瑰丽", "GeneralFight", ["R"]
    monkeypatch.setattr(item.combat_check, "read_hp_from_frame", lambda *_:
                        HpObservation(0 if invalid == "dead" else 3298, 9832, "alive"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: (display, cls_name))
    monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: colors, raising=False)
    before = (item.team.R, item.team.B, item.team.Y, item.team.current)
    with pytest.raises(Roguelike3IdentityError, match="r3_strategy_identity_mismatch"):
        item.refresh_field_role(item.roles[item.team.current])
    assert (item.team.R, item.team.B, item.team.Y, item.team.current) == before
    assert not context.actions and not context.tasker.controller.clicks
