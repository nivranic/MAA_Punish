"""Main-form charging is bounded by fresh identity, HUD and ultimate checks."""
import pytest

from test_oblivion_yield import make_role


def charging_role():
    role, state, inputs = make_role("ult_loop")
    role._observe_form = lambda: ("main", True)
    role.action.long_press_attack = lambda ms: inputs.append(("charge", ms))
    role.action.check_status = lambda _: pytest.fail("Main charging must not depend on red effects")
    return role, state, inputs


def test_main_charges_from_empty_gauge_then_yields_to_identity():
    role, state, inputs = charging_role()
    role.perform()
    assert inputs == [("charge", 2500)]
    assert role._ult_ticks == 1 and state.checks == 1
    state.allowed = False
    role.perform()
    assert inputs == [("charge", 2500)] and state.checks == 2


@pytest.mark.parametrize("form,hud", [("unknown", True), ("main", False), ("unknown", False)])
def test_unconfirmed_main_or_hud_never_charges(form, hud):
    role, _, inputs = charging_role()
    role._observe_form = lambda: (form, hud)
    role.perform()
    assert inputs == [] and role._ult_ticks == 0


def test_normal_form_returns_to_idle_without_stale_closing_permission():
    role, _, inputs = charging_role()
    role._close_cast_available = role._close_cast_requested = True
    role._observe_form = lambda: ("normal", True)
    role.perform()
    assert inputs == [] and role.phase == "idle"
    assert not role._close_cast_available and not role._close_cast_requested


@pytest.mark.parametrize("when", ["before", "observe", "energy"])
def test_stop_at_any_observation_boundary_blocks_charge(when):
    role, _, inputs = charging_role()
    role.combat.context.tasker.stopping = when == "before"
    def observe():
        if when == "observe":
            role.combat.context.tasker.stopping = True
        return "main", True
    role._observe_form = observe
    reads = 0
    def energy():
        nonlocal reads
        reads += 1
        if when == "energy" and reads == 2:
            role.combat.context.tasker.stopping = True
        return False
    role.action.check_Skill_energy_bar = energy
    role.perform()
    assert inputs == [] and role._ult_ticks == 0


@pytest.mark.parametrize("ready_read", [1, 2])
def test_new_ready_energy_preempts_charge(ready_read):
    role, _, inputs = charging_role()
    reads = 0
    def energy():
        nonlocal reads
        reads += 1
        return reads == ready_read
    role.action.check_Skill_energy_bar = energy
    role.perform()
    assert inputs == [] and role.phase == "ult_close" and role._ult_ticks == 0


def test_charge_keeps_existing_loop_budget():
    role, _, inputs = charging_role()
    role._ult_ticks = 29
    role.perform()
    role.perform()
    assert inputs == [("charge", 2500)]
    assert role._ult_ticks == 30 and role.phase == "ult_close"


def test_each_charge_rechecks_field_and_never_appends_an_input_batch():
    role, state, inputs = charging_role()
    role._clear_ticks = 4
    role.perform()
    role.perform()
    assert inputs == [("charge", 2500), ("charge", 2500)]
    assert state.checks == 2 and role._clear_ticks == 0 and role._ult_ticks == 2
