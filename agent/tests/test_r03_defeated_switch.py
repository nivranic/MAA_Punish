"""A defeated R03 teammate must not consume another QTE switch window."""
import pytest

from action.combat.core import session
from action.combat.core.provider import CombatCheck, HpObservation
from action.combat.core.role_factory import create_role
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot, publish_team_roster
from test_support.fakes import FakeContext


ROSTER = {"R": "Oblivion", "B": "GeneralFight", "Y": ""}
NAMES = {"R": "露娜·终焉", "B": "薇拉·瑰丽", "Y": ""}


def pair(monkeypatch):
    context = FakeContext()
    publish_team_roster(context, ROSTER, role_names=NAMES)
    combat = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
    combat.team = TeamSnapshot(**ROSTER, current="B")
    combat.roles = {color: create_role(combat, color, combat.team.cls_at(color))
                    for color in combat.team.filled_colors()}
    combat.frame = object()
    monkeypatch.setattr(combat.combat_check, "read_current_hp", lambda *_: 0)
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                        lambda *_: HpObservation(0, 628, "zero", text="0/628"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("瑰丽", "GeneralFight"))
    monkeypatch.setattr(combat.combat_check, "detect_qte_colors", lambda *_: ["B"])
    return combat


def test_death_then_automatic_handoff_does_not_retry_the_dead_qte(monkeypatch):
    combat = pair(monkeypatch)
    assert combat._check_hp_zero_exit() == ""
    assert combat._r3_defeated_colors == {"B"}
    assert combat._hp_zero_since is not None
    monkeypatch.setattr(combat.combat_check, "read_current_hp", lambda *_: 5000)
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                        lambda *_: HpObservation(5000, 10000, "alive"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("终焉", "Oblivion"))
    monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: ["B"])
    assert combat._check_hp_zero_exit() == ""
    assert combat.refresh_field_role(combat.roles["B"]) is True
    assert combat.team.current == "R"
    assert combat._hp_zero_since is None
    assert combat.choose_switch_color(combat.roles["R"]) is None
    assert not combat.switch_to_color("B", attacker=combat.roles["R"])
    assert not combat.context.tasker.controller.clicks
    assert not combat.context.actions
    assert {color: combat.team.cls_at(color) for color in ROSTER} == ROSTER


@pytest.mark.parametrize("invalid", ["ordinary", "missing_frame", "missing_names",
                                    "unknown", "wrong_name", "bad_hp", "alive",
                                    "fallback", "stopped"])
def test_zero_alone_never_marks_an_unconfirmed_role_dead(monkeypatch, invalid):
    combat = pair(monkeypatch)
    if invalid == "ordinary":
        combat.roguelike3_identity_required = False
    elif invalid == "missing_frame":
        combat.frame = None
    elif invalid == "missing_names":
        publish_team_roster(combat.context, ROSTER)
    elif invalid == "unknown":
        monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("未知", "GeneralFight"))
    elif invalid == "wrong_name":
        monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("流光", "GeneralFight"))
    elif invalid == "bad_hp":
        monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                            lambda *_: HpObservation(None, None, "invalid"))
    elif invalid == "alive":
        monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                            lambda *_: HpObservation(628, 628, "alive"))
    elif invalid == "fallback":
        combat.roles["B"].fallback_reason = "unavailable"
    else:
        combat.context.tasker.stopping = True
    assert combat._check_hp_zero_exit() == ""
    assert combat._r3_defeated_colors == set()
    assert not combat.context.actions
    assert not combat.context.tasker.controller.clicks


def test_observed_revived_role_and_new_battle_clear_defeat(monkeypatch):
    combat = pair(monkeypatch)
    combat._check_hp_zero_exit()
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                        lambda *_: HpObservation(628, 628, "alive"))
    assert combat.refresh_field_role(combat.roles["B"]) is False
    assert combat._r3_defeated_colors == set()
    combat._r3_defeated_colors.add("B")
    combat._clear_combat_state(log_end=False)
    assert combat._r3_defeated_colors == set()


def test_living_teammate_still_eligible(monkeypatch):
    combat = pair(monkeypatch)
    combat.team.current = "R"
    assert combat.choose_switch_color(combat.roles["R"]) == "B"


@pytest.mark.parametrize("invalid", [None, "old", "different_max", "different_color", "switch_attempt"])
def test_recent_positive_identity_covers_the_darkened_death_icon(monkeypatch, invalid):
    combat = pair(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(session.time, "monotonic", lambda: clock[0])
    combat._remember_r3_living_identity(
        "瑰丽", "GeneralFight", combat.frame,
        hp=HpObservation(292, 628, "alive"),
    )
    clock[0] += 0.5
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("未知", "GeneralFight"))
    if invalid == "old":
        clock[0] += 3
    elif invalid == "different_max":
        monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                            lambda *_: HpObservation(0, 9999, "zero"))
    elif invalid == "different_color":
        combat.team.current = "R"
    elif invalid == "switch_attempt":
        monkeypatch.setattr(session, "attempt_switch_to_color", lambda *_, **kw: False)
        combat.switch_to_color("R", attacker=combat.roles["B"])
        assert combat._r3_living_identity is None
    combat._check_hp_zero_exit()
    assert combat._r3_defeated_colors == ({"B"} if invalid is None else set())
    assert not combat.context.actions
    assert not combat.context.tasker.controller.clicks


def test_zero_hp_on_the_confirmed_arrival_frame_records_death(monkeypatch):
    combat = pair(monkeypatch)
    combat.team.current = "R"
    combat._correct_role_from_field("B", "GeneralFight", combat.frame)
    assert combat._r3_defeated_colors == {"B"}


def crimson_death(monkeypatch):
    roster = {"R": "CrimsonWeave", "B": "GeneralFight", "Y": ""}
    names = {"R": "露西亚·深红囚影", "B": "薇拉·瑰丽[试用]", "Y": ""}
    context = FakeContext()
    publish_team_roster(context, roster, role_names=names)
    combat = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
    combat.team = TeamSnapshot(**roster, current="R")
    combat.roles = {color: create_role(combat, color, combat.team.cls_at(color))
                    for color in combat.team.filled_colors()}
    combat.frame = object()
    monkeypatch.setattr(combat.combat_check, "read_current_hp", lambda *_: 0)
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                        lambda *_: HpObservation(0, 10586, "zero", text="0/10586"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("深红囚影", "CrimsonWeave"))
    assert combat._check_hp_zero_exit() == ""
    assert combat._r3_defeated_colors == {"R"}
    monkeypatch.setattr(combat.combat_check, "read_current_hp", lambda *_: 7000)
    monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                        lambda *_: HpObservation(7000, 8141, "alive", text="7000/8141"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("瑰丽", "GeneralFight"))
    monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: [])
    return combat, roster, names


def test_last_living_named_teammate_takes_over_when_dead_bench_badge_disappears(monkeypatch):
    combat, roster, _ = crimson_death(monkeypatch)
    bindings = combat.roles.copy()

    assert combat.refresh_field_role(combat.roles["R"]) is True

    assert combat.team.current == "B" and combat.field_identity == "confirmed"
    assert combat._r3_defeated_colors == {"R"}
    assert combat.roles == bindings
    assert {color: combat.team.cls_at(color) for color in roster} == roster
    assert combat.refresh_field_role(combat.roles["B"]) is False
    assert not combat.context.actions and not combat.context.tasker.controller.clicks


@pytest.mark.parametrize("invalid", ["no_death", "two_survivors", "dead_target",
                                    "missing_names", "wrong_name", "visible_target",
                                    "foreign_marker", "zero_hp", "unknown_hp", "stopped"])
def test_missing_bench_badge_needs_all_remaining_identity_evidence(monkeypatch, invalid):
    combat, roster, names = crimson_death(monkeypatch)
    display_name = "瑰丽"
    if invalid == "no_death":
        combat._r3_defeated_colors.clear()
    elif invalid == "two_survivors":
        combat.team.Y = roster["Y"] = "Oblivion"
        names["Y"] = "露娜·终焉"
        combat.roles["Y"] = create_role(combat, "Y", "Oblivion")
        publish_team_roster(combat.context, roster, role_names=names)
    elif invalid == "dead_target":
        combat._r3_defeated_colors.add("B")
    elif invalid == "missing_names":
        publish_team_roster(combat.context, roster)
    elif invalid == "wrong_name":
        display_name = "流光"
    elif invalid in {"visible_target", "foreign_marker"}:
        monkeypatch.setattr(session, "detect_visible_team_colors",
                            lambda *_: ["B"] if invalid == "visible_target" else ["Y"])
    elif invalid in {"zero_hp", "unknown_hp"}:
        monkeypatch.setattr(combat.combat_check, "read_hp_from_frame",
                            lambda *_: HpObservation(0, 8141, "zero") if invalid == "zero_hp"
                            else HpObservation(None, None, "invalid"))
    else:
        combat.context.tasker.stopping = True

    assert combat._reconcile_r3_field_color(combat.roles["R"], display_name, "GeneralFight", combat.frame) is False
    assert combat.team.current == "R"
    assert not combat.context.actions and not combat.context.tasker.controller.clicks
