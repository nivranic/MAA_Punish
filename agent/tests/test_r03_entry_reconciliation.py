"""A resumed R03 callback must validate the living member already on field."""
import pytest

from action.combat.core import session
from action.combat.core.provider import CombatCheck, HpObservation, Roguelike3IdentityError
from action.combat.core.role_factory import create_role
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot, publish_team_roster
from test_support.fakes import FakeContext


ROSTER = {"R": "Oblivion", "B": "GeneralFight", "Y": ""}
NAMES = {"R": "露娜·终焉", "B": "薇拉·瑰丽", "Y": ""}


def setup_entry(monkeypatch):
    context = FakeContext()
    publish_team_roster(context, ROSTER, role_names=NAMES)
    combat = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
    combat.team = TeamSnapshot(**ROSTER, current="R")
    combat.roles = {c: create_role(combat, c, combat.team.cls_at(c)) for c in ("R", "B")}
    frame = object()
    combat.frame = frame
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("瑰丽", "GeneralFight"))
    monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: ["R"])
    def hp(ctx, image):
        assert ctx is context and image is frame
        return HpObservation(628, 628, "alive", text="628/628")
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", hp)
    return combat


def test_reentry_selects_the_named_living_member_without_input_or_roster_rewrite(monkeypatch):
    combat = setup_entry(monkeypatch)
    monkeypatch.setattr(combat.combat_check, "detect_team",
                        lambda *_: TeamSnapshot(**ROSTER, current="R"))
    assert combat.load_team()
    assert combat.team.current == "B"
    assert combat.current_role_name == "瑰丽"
    assert combat.field_identity == "confirmed"
    assert {c: combat.team.cls_at(c) for c in ROSTER} == ROSTER
    assert not combat.context.actions
    assert not combat.context.tasker.controller.clicks


@pytest.mark.parametrize("case", ["zero_hp", "bad_hp", "missing_names", "wrong_name",
                                  "ambiguous", "absent", "bench_unconfirmed", "fallback", "stopped"])
def test_entry_reconciliation_cannot_relax_existing_identity_guards(monkeypatch, case):
    combat = setup_entry(monkeypatch)
    if case in {"zero_hp", "bad_hp"}:
        hp = HpObservation(0, 628, "zero") if case == "zero_hp" else HpObservation(None, None, "invalid")
        monkeypatch.setattr(combat.combat_check, "read_hp_from_frame", lambda *_: hp)
    elif case == "missing_names":
        publish_team_roster(combat.context, ROSTER)
    elif case == "wrong_name":
        monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("流光", "GeneralFight"))
    elif case == "ambiguous":
        combat.team = TeamSnapshot(**{**ROSTER, "Y": "GeneralFight"}, current="R")
    elif case == "absent":
        monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("深红囚影", "CrimsonWeave"))
    elif case == "bench_unconfirmed":
        monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: [])
    elif case == "fallback":
        combat.roles["B"].fallback_reason = "unavailable"
    else:
        combat.context.tasker.stopping = True
    with pytest.raises(Roguelike3IdentityError):
        combat._confirm_entry_field_identity("R")
    assert combat.team.current == "R"
    assert not combat.context.actions
    assert not combat.context.tasker.controller.clicks


def test_matching_red_entry_and_unknown_entry_keep_existing_behavior(monkeypatch):
    combat = setup_entry(monkeypatch)
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("终焉", "Oblivion"))
    assert combat._confirm_entry_field_identity("R") == "终焉"
    assert combat.team.current == "R"
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("未知", "GeneralFight"))
    assert combat._confirm_entry_field_identity("R") == "未知"
    assert combat.team.current == "R"
    assert not combat.context.actions
