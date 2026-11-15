"""One bounded opening charge yields to the existing live identity checks."""
import pytest

from test_oblivion_yield import make_role


def opening_role():
    role, state, inputs = make_role('farm')
    role._opening_charge_attempted = False
    role._observe_form = lambda: ('normal', True)
    role.action.long_press_attack = lambda ms: inputs.append(('charge', ms))
    return role, state, inputs


def test_live_normal_opening_charges_once_before_the_first_elimination():
    role, state, inputs = opening_role()
    role.perform()
    assert inputs == [('charge', 2500)]
    assert role.phase == 'farm' and role._opening_charge_attempted
    assert role._clear_ticks == 0 and state.checks == 1
    role.perform()
    assert inputs == [('charge', 2500), 'attack', ('ball', 1), 'attack']
    assert state.checks == 2


@pytest.mark.parametrize('case', ['identity', 'stopped', 'observe_stop', 'energy_stop', 'energy_ready'])
def test_opening_charge_never_bypasses_identity_stop_or_new_energy(monkeypatch, case):
    role, state, inputs = opening_role()
    state.allowed = case != 'identity'
    role.combat.context.tasker.stopping = case == 'stopped'
    if case == 'observe_stop':
        def observe():
            role.combat.context.tasker.stopping = True
            return 'normal', True
        role._observe_form = observe
    reads = 0
    def energy():
        nonlocal reads
        reads += 1
        if reads == 2 and case == 'energy_stop':
            role.combat.context.tasker.stopping = True
        return reads == 2 and case == 'energy_ready'
    role.action.check_Skill_energy_bar = energy
    role.perform()
    assert inputs == []


@pytest.mark.parametrize('form,hud', [('unknown', False), ('unknown', True), ('main', True)])
def test_unconfirmed_normal_form_does_not_start_a_charge(form, hud):
    role, _, inputs = opening_role()
    role._observe_form = lambda: (form, hud)
    role.perform()
    assert not any(isinstance(item, tuple) and item[0] == 'charge' for item in inputs)


def test_role_change_after_opening_charge_blocks_the_next_input():
    role, state, inputs = opening_role()
    role.perform()
    state.allowed = False
    role.perform()
    assert inputs == [('charge', 2500)]


def test_reset_allows_one_charge_in_the_next_rotation():
    role, _, _ = opening_role()
    role._opening_charge_attempted = True
    role.reset_state()
    assert role._opening_charge_attempted is False
