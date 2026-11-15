"""An unspent ready pulse must not trap main form in basic attacks."""
import pytest

from action.combat.roles.oblivion import _ULT_LOOP_MAX
from test_oblivion_yield import make_role


def test_lost_readiness_before_closing_cast_resumes_main_form_farming():
    role, state, inputs = make_role('ult_loop')
    role._close_cast_available = True
    role._clear_ticks = 2
    role._ult_ticks = 3
    role._observe_form = lambda: ('main', True)
    state.ready = True
    role.perform()
    assert role.phase == 'ult_close' and inputs == []

    state.ready = False
    role.perform()
    assert role.phase == 'ult_loop' and inputs == []
    assert role._ult_ticks == 3 and role._clear_ticks == 2
    assert role._close_cast_available and not role._close_cast_requested

    role.perform()
    assert inputs == [('charge', 2500)]
    assert state.checks == 3


@pytest.mark.parametrize('boundary', [
    'requested', 'unknown_form', 'hidden_hud', 'loop_budget',
    'identity_changed', 'stopped', 'stop_during_energy',
])
def test_close_recovery_preserves_cast_and_observation_boundaries(boundary):
    role, state, inputs = make_role('ult_close')
    role._close_cast_available = True
    role._close_cast_requested = boundary == 'requested'
    role._ult_ticks = _ULT_LOOP_MAX if boundary == 'loop_budget' else 2
    role._observe_form = lambda: (
        'unknown' if boundary == 'unknown_form' else 'main',
        boundary != 'hidden_hud',
    )
    state.allowed = boundary != 'identity_changed'
    role.combat.context.tasker.stopping = boundary == 'stopped'
    if boundary == 'stop_during_energy':
        def energy():
            role.combat.context.tasker.stopping = True
            return False
        role.action.check_Skill_energy_bar = energy

    role.perform()

    assert role.phase == 'ult_close'
    assert inputs == (['attack'] if boundary in {
        'requested', 'unknown_form', 'loop_budget',
    } else [])


def test_readiness_recovery_casts_without_an_extra_elimination():
    role, state, inputs = make_role('ult_close')
    role._close_cast_available = True
    role._observe_form = lambda: ('main', True)
    role.action.use_skill = lambda _: inputs.append('skill')
    state.ready = True

    role.perform()

    assert inputs == ['skill']
    assert role._close_cast_requested and not role._close_cast_available
    assert role.phase == 'ult_close'
