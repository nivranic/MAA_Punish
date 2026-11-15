"""Unobserved time inside a role action is not continuous missing-HUD evidence."""
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from test_support.fakes import FakeContext


@dataclass
class Presence(CombatCheck):
    visible: bool = True
    outer: bool = False
    overlay: str | None = None

    def in_combat(self, *_): return self.visible
    def in_outer_interface(self, *_): return self.outer
    def match_exit_overlay(self, *_): return self.overlay
    def read_current_hp(self, *_): return 9491


def setup(monkeypatch, r03):
    clock = [100.0]
    monkeypatch.setattr("action.combat.core.session.time.monotonic", lambda: clock[0])
    check = Presence()
    combat = CombatTask(FakeContext(), check, roguelike3_identity_required=r03)
    combat._in_combat = True
    combat.team = TeamSnapshot(R="CrimsonWeave", B="GeneralFight", Y="", current="B")
    combat._r3_defeated_colors = {"R"}
    assert combat._check_combat_presence() == ""
    return clock, check, combat


@pytest.mark.parametrize("r03", [False, True])
def test_one_miss_after_long_action_preserves_session_and_pauses_input(monkeypatch, r03):
    clock, check, combat = setup(monkeypatch, r03)
    team = combat.team
    clock[0] += 28
    check.visible = False
    assert combat._check_combat_presence() == ""
    assert combat.combat_ui_lost_elapsed == 0
    monkeypatch.setattr(combat, "get_current_role", lambda: SimpleNamespace(
        perform=lambda: pytest.fail("No input while HUD is unobserved"),
    ))
    combat.perform()
    clock[0] += 1.5
    check.visible = True
    assert combat._check_combat_presence() == ""
    assert combat.combat_ui_lost_elapsed == 0
    assert combat.team is team and combat.team.current == "B"
    assert combat._r3_defeated_colors == {"R"}
    assert not combat.context.actions and not combat.context.tasker.controller.clicks


@pytest.mark.parametrize("r03", [False, True])
@pytest.mark.parametrize("action_gap", [0, 28])
def test_repeated_missing_observations_still_exit_at_eight_seconds(monkeypatch, r03, action_gap):
    clock, check, combat = setup(monkeypatch, r03)
    clock[0] += action_gap
    check.visible = False
    assert combat._check_combat_presence() == ""
    clock[0] += 7.99
    assert combat._check_combat_presence() == ""
    assert combat.combat_ui_lost_elapsed == pytest.approx(7.99)
    clock[0] += .01
    assert combat._check_combat_presence() == "combat_ui_lost"


@pytest.mark.parametrize("r03", [False, True])
def test_reappearance_and_cleanup_reset_the_missing_observation_window(monkeypatch, r03):
    clock, check, combat = setup(monkeypatch, r03)
    clock[0] += 1
    check.visible = False
    assert combat._check_combat_presence() == ""
    clock[0] += 6
    check.visible = True
    assert combat._check_combat_presence() == ""
    clock[0] += 18
    check.visible = False
    assert combat._check_combat_presence() == ""
    assert combat.combat_ui_lost_elapsed == 0
    combat._clear_combat_state(log_end=False)
    clock[0] += 20
    combat._in_combat = True
    assert combat._check_combat_presence() == ""
    assert combat.combat_ui_lost_elapsed == 0


@pytest.mark.parametrize("case", ["outer", "overlay"])
def test_explicit_exit_does_not_wait_for_the_missing_hud_budget(monkeypatch, case):
    clock, check, combat = setup(monkeypatch, True)
    check.visible = False
    if case == "outer":
        check.outer = True
    else:
        check.overlay = "confirmed exit"
    assert combat._check_combat_presence() == "outer_interface"
