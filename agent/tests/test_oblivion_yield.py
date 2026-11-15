"""Long elimination batches must yield before another role can be controlled."""
from types import SimpleNamespace

import pytest

from action.combat.roles.oblivion import Oblivion


def make_role(phase):
    inputs=[]
    state=SimpleNamespace(ready=False, balls=16, allowed=True, checks=0)
    def verify(role):
        state.checks+=1
        return not state.allowed
    combat=SimpleNamespace(
        context=SimpleNamespace(tasker=SimpleNamespace(stopping=False)),
        ensure_strategy_ready=lambda *_: None, refresh_field_role=verify,
    )
    role=Oblivion.__new__(Oblivion)
    role.combat=combat
    role.phase=phase
    role._clear_ticks=role._farm_ticks=role._ult_ticks=role._finish_ticks=0
    # These fixtures exercise the elimination/ultimate phases after opening.
    role._opening_charge_attempted = True
    role._close_cast_available = False
    role._close_cast_requested = False
    role._open_cast_requested = False
    role.action=SimpleNamespace(
        check_Skill_energy_bar=lambda: state.ready,
        count_signal_balls=lambda: state.balls,
        attack=lambda: inputs.append('attack'),
        ball_elimination_target=lambda target: inputs.append(('ball',target)),
        check_status=lambda _: False,
        long_press_attack=lambda ms: inputs.append(('charge', ms)),
    )
    if phase == 'ult_loop':
        role._observe_form = lambda: ('main', True)
    return role,state,inputs


@pytest.mark.parametrize('phase',['farm','ult_loop'])
def test_changed_field_is_rechecked_before_the_second_elimination(phase):
    role,state,inputs=make_role(phase)
    role.perform()
    expected = ['attack', ('ball', 1), 'attack'] if phase == 'farm' else [('charge', 2500)]
    assert inputs == expected
    state.allowed=False
    role.perform()
    assert state.checks==2
    assert inputs == expected
    assert role._clear_ticks == (1 if phase == 'farm' else 0)


@pytest.mark.parametrize('phase,counter',[('farm','_farm_ticks'),('ult_loop','_ult_ticks')])
def test_five_eliminations_keep_the_existing_rotation_across_fresh_ticks(phase,counter):
    role,state,inputs=make_role(phase)
    for _ in range(6): role.perform()
    if phase == 'farm':
        assert inputs.count(('ball', 1)) == 5
        assert inputs.count('attack') == 7
    else:
        assert inputs == [('charge', 2500)] * 6
    assert state.checks==6
    assert getattr(role, counter) == (1 if phase == 'farm' else 6)
    assert role._clear_ticks == 0


@pytest.mark.parametrize('phase,next_phase',[('farm','ult_open'),('ult_loop','ult_close')])
def test_energy_ready_between_eliminations_advances_without_extra_input(phase,next_phase):
    role,state,inputs=make_role(phase)
    role.perform()
    state.ready=True
    before=inputs.copy()
    role.perform()
    assert role.phase==next_phase and inputs==before


def test_confirmed_second_ultimate_returns_to_switch_without_fixed_attack_tail():
    role, state, inputs = make_role('ult_close')
    state.ready = True
    state.form = 'main'
    role._observe_form = lambda: (state.form, True)
    role.action.use_skill = lambda _: inputs.append('skill')
    role.action.auxiliary_machine = lambda: inputs.append('auxiliary')
    role.combat.request_role_switch = lambda _: inputs.append('switch')

    role.perform()
    assert role.phase == 'ult_close'
    state.form = 'normal'
    role.perform()
    role.perform()

    assert inputs == ['skill', 'auxiliary', 'switch']
    assert state.checks == 2


def test_unconfirmed_second_ultimate_does_not_switch_or_emit_tail_inputs():
    role, state, inputs = make_role('ult_close')
    state.ready = True
    role._observe_form = lambda: ('main', True)
    role.action.use_skill = lambda _: inputs.append('skill')
    role.action.auxiliary_machine = lambda: inputs.append('auxiliary')

    role.perform()

    assert role.phase == 'ult_close'
    assert inputs == ['skill']


def test_stop_after_second_ultimate_suppresses_auxiliary_and_switch():
    role, state, inputs = make_role('ult_close')
    state.ready = True
    role._observe_form = lambda: ('main', True)
    def cast(_):
        inputs.append('skill')
        role.combat.context.tasker.stopping = True
        return True
    role.action.use_skill = cast
    role.action.auxiliary_machine = lambda: inputs.append('auxiliary')

    role.perform()

    assert inputs == ['skill']
    assert role.phase == 'ult_close'


def test_first_ultimate_does_not_advance_until_red_or_blue_orbs_are_observed():
    role, state, inputs = make_role('ult_open')
    state.ready = True
    state.form = 'normal'
    role._observe_form = lambda: (state.form, True)
    role.action.use_skill = lambda _: inputs.append('skill')
    role.action.auxiliary_machine = lambda: inputs.append('auxiliary')

    role.perform()
    assert role.phase == 'ult_open' and inputs == ['skill']
    state.ready = False  # A dark energy pulse is insufficient evidence.
    role.perform()
    assert role.phase == 'ult_open' and inputs == ['skill', 'attack']
    state.form = 'main'
    role.perform()
    assert role.phase == 'ult_loop' and inputs[-1] == 'auxiliary'


def test_lost_energy_before_first_cast_returns_to_elimination():
    role, state, inputs = make_role('farm')
    state.ready = True
    role.perform()
    assert role.phase == 'ult_open' and inputs == []
    state.ready = False
    role._observe_form = lambda: ('normal', True)
    role.perform()
    assert role.phase == 'farm' and inputs == []
    role.perform()
    assert ('ball', 1) in inputs


def test_unknown_form_cannot_return_to_farming_after_energy_miss():
    role, _, inputs = make_role('ult_open')
    role._observe_form = lambda: ('unknown', True)
    role.perform()
    assert role.phase == 'ult_open' and inputs == ['attack']


def test_stop_during_negative_energy_read_cannot_attack_or_change_phase():
    role, _, inputs = make_role('ult_open')
    role._observe_form = lambda: ('normal', True)
    def stop():
        role.combat.context.tasker.stopping = True
        return False
    role.action.check_Skill_energy_bar = stop
    role.perform()
    assert role.phase == 'ult_open' and inputs == []


@pytest.mark.parametrize('phase', ['ult_open', 'ult_close'])
def test_hidden_hud_waits_without_cast_or_attack(phase):
    role, _, inputs = make_role(phase)
    role._observe_form = lambda: ('unknown', False)
    role.perform()
    assert role.phase == phase and inputs == []


@pytest.mark.parametrize('phase', ['ult_open', 'ult_close'])
def test_visible_hud_with_too_few_orbs_can_regenerate_without_assuming_form(phase):
    role, _, inputs = make_role(phase)
    role._observe_form = lambda: ('unknown', True)
    role.perform()
    assert role.phase == phase and inputs == ['attack']


def test_field_change_after_cast_blocks_confirmation_and_switch():
    role, state, inputs = make_role('ult_close')
    state.ready = True
    role._observe_form = lambda: ('main', True)
    role.action.use_skill = lambda _: inputs.append('skill')
    role.perform()
    state.allowed = False
    role._observe_form = lambda: ('normal', True)
    role.perform()
    assert inputs == ['skill']


@pytest.mark.parametrize('phase,form', [('ult_open', 'normal'), ('ult_close', 'main')])
def test_stop_during_energy_read_prevents_cast(phase, form):
    role, _, inputs = make_role(phase)
    role._observe_form = lambda: (form, True)
    def ready():
        role.combat.context.tasker.stopping = True
        return True
    role.action.check_Skill_energy_bar = ready
    role.action.use_skill = lambda _: inputs.append('skill')
    role.perform()
    assert inputs == []


def enter_confirmed_main(role, state, inputs):
    role.phase = 'ult_open'
    state.form, state.hud = 'main', True
    role._observe_form = lambda: (state.form, state.hud)
    role.action.auxiliary_machine = lambda: inputs.append('auxiliary')
    role.action.use_skill = lambda _: inputs.append('skill')
    role.perform()
    assert role.phase == 'ult_loop'
    state.ready = True
    role.perform()
    assert role.phase == 'ult_close'
    state.form = 'unknown'  # Main sequence exhausted its visible orbs.


def test_confirmed_main_allows_one_close_request_after_orbs_are_exhausted():
    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    role.perform()
    assert inputs == ['auxiliary', 'skill']
    assert role.phase == 'ult_close'
    role.perform()
    assert inputs == ['auxiliary', 'skill', 'attack']
    state.form = 'normal'
    role.perform()
    assert role.phase == 'switch'


def test_unknown_close_without_confirmed_main_cannot_request_a_skill():
    role, state, inputs = make_role('ult_close')
    state.ready = True
    role._observe_form = lambda: ('unknown', True)
    role.action.use_skill = lambda _: inputs.append('skill')
    role.perform()
    assert inputs == ['attack']


def test_hidden_or_dead_hud_cannot_consume_pending_close_request():
    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    state.hud = False
    role.perform()
    assert inputs == ['auxiliary']
    state.hud = True
    role.perform()
    assert inputs == ['auxiliary', 'skill']


def test_failed_close_action_does_not_rearm_unknown_form_requests():
    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    def failed_cast(_):
        inputs.append('failed_skill')
        raise RuntimeError('action result unavailable')
    role.action.use_skill = failed_cast
    with pytest.raises(RuntimeError):
        role.perform()
    role.perform()
    assert inputs == ['auxiliary', 'failed_skill', 'attack']


def test_positive_normal_confirmation_wins_over_unused_close_request():
    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    state.form = 'normal'
    role.perform()
    assert inputs == ['auxiliary', 'auxiliary']
    assert role.phase == 'switch'
    assert not role._close_cast_available


def test_reset_does_not_carry_a_close_request_into_another_battle():
    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    role.reset_state()
    role.phase = 'ult_close'
    role.perform()
    assert inputs == ['auxiliary', 'attack']


def test_stop_during_pending_close_energy_read_sends_no_skill():
    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    def ready():
        role.combat.context.tasker.stopping = True
        return True
    role.action.check_Skill_energy_bar = ready
    role.perform()
    assert inputs == ['auxiliary']


@pytest.mark.parametrize('case,expected', [
    ('one_yellow', 'switch'), ('two_yellow', 'switch'),
    ('energy_ready', 'ult_close'), ('mixed', 'ult_close'),
    ('empty', 'ult_close'), ('hidden', 'ult_close'), ('dead', 'ult_close'),
    ('no_request', 'ult_close'), ('reset', 'ult_close'), ('stop', 'ult_close'),
])
def test_sparse_yellow_after_zero_orb_close_uses_same_frame_and_current_cast(monkeypatch, case, expected):
    import cv2
    import numpy as np
    from action.combat.actions.signal_ball_layout import SIGNAL_BALL_SLOT_CENTERS
    from action.combat.roles import oblivion as module

    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    if case != 'no_request':
        role.perform()  # Zero orbs: the previously confirmed main permits one request.
        assert inputs == ['auxiliary', 'skill']
    if case == 'reset':
        role.reset_state()
        role.phase = 'ult_close'
    inputs.clear()

    frame = np.zeros((720, 1280, 3), np.uint8)
    state.balls = 2 if case in {'two_yellow', 'mixed'} else 0 if case == 'empty' else 1
    for i, (x, y) in enumerate(SIGNAL_BALL_SLOT_CENTERS[:max(1, state.balls)]):
        color = (220, 140, 40) if case == 'mixed' and i == 1 else (0, 220, 220)
        cv2.circle(frame, (x, y), 14, color, -1)
    context = role.combat.context
    def hud(ctx, image):
        assert ctx is context and image is frame
        return case != 'hidden'
    def hp(ctx, image):
        assert ctx is context and image is frame
        return SimpleNamespace(alive=case != 'dead')
    def count(*, image):
        assert image is frame
        return state.balls
    energy_frames = []
    def energy(*, image=None):
        if image is not None:
            assert image is frame, 'Sparse confirmation must use the HUD/HP/orb frame'
        energy_frames.append(image)
        if case == 'stop':
            context.tasker.stopping = True
        return case == 'energy_ready'
    monkeypatch.setattr(module, 'is_dodge_button_visible', hud)
    monkeypatch.setattr(module, 'signal_ball_slot_centers', lambda _: SIGNAL_BALL_SLOT_CENTERS)
    role.combat.combat_check = SimpleNamespace(read_hp_from_frame=hp)
    role.action._get_image = lambda: frame
    role.action.count_signal_balls = count
    role.action.check_Skill_energy_bar = energy
    role._observe_form = Oblivion._observe_form.__get__(role)
    role.combat.request_role_switch = lambda _: inputs.append('switch')

    role.perform()
    assert role.phase == expected
    if case in {'one_yellow', 'two_yellow', 'energy_ready', 'mixed', 'stop'}:
        assert energy_frames and energy_frames[0] is frame
    if expected == 'switch':
        role.perform()
        assert inputs == ['auxiliary', 'switch']
    elif case in {'hidden', 'dead', 'stop'}:
        assert inputs == []
    else:
        assert inputs == ['attack']


@pytest.mark.parametrize('case', [
    'one_yellow', 'two_yellow', 'empty', 'red', 'mixed', 'hidden',
    'dead', 'stop', 'unready', 'identity_changed',
])
@pytest.mark.parametrize('return_path', ['no_switch_target', 'switched_back'])
def test_next_ultimate_accepts_sparse_yellow_only_with_fresh_live_ready_evidence(
    monkeypatch, case, return_path,
):
    import cv2
    import numpy as np
    from action.combat.actions.signal_ball_layout import SIGNAL_BALL_SLOT_CENTERS
    from action.combat.roles import oblivion as module

    role, state, inputs = make_role('ult_open')
    enter_confirmed_main(role, state, inputs)
    role.perform()
    state.form = 'normal'
    role.perform()
    assert role.phase == 'switch'
    if return_path == 'no_switch_target':
        role.on_switch_failed()
    else:
        role.reset_state()
    assert role.phase == 'idle'
    role.action.lens_lock = lambda: None
    role.perform()
    assert role.phase == 'ult_open'
    inputs.clear()

    frame = np.zeros((720, 1280, 3), np.uint8)
    count = 2 if case in {'two_yellow', 'mixed'} else 0 if case == 'empty' else 1
    for index, (x, y) in enumerate(SIGNAL_BALL_SLOT_CENTERS[:count]):
        color = (40, 40, 220) if case == 'red' or (case == 'mixed' and index == 1) else (0, 220, 220)
        cv2.circle(frame, (x, y), 14, color, -1)
    context = role.combat.context
    seen = []

    def hud(ctx, image):
        assert ctx is context and image is frame
        return case != 'hidden'

    def hp(ctx, image):
        assert ctx is context and image is frame
        return SimpleNamespace(alive=case != 'dead')

    def balls(*, image):
        assert image is frame
        return count

    def energy(*, image=None):
        if image is not None:
            assert image is frame
        seen.append(image)
        if case == 'stop':
            context.tasker.stopping = True
        return case != 'unready'

    monkeypatch.setattr(module, 'is_dodge_button_visible', hud)
    monkeypatch.setattr(module, 'signal_ball_slot_centers', lambda _: SIGNAL_BALL_SLOT_CENTERS)
    role.combat.combat_check = SimpleNamespace(read_hp_from_frame=hp)
    role.action._get_image = lambda: frame
    role.action.count_signal_balls = balls
    role.action.check_Skill_energy_bar = energy
    role._observe_form = Oblivion._observe_form.__get__(role)
    state.allowed = case != 'identity_changed'

    role.perform()
    assert role.phase == 'ult_open'  # Requesting a skill does not confirm main form.
    if case in {'one_yellow', 'two_yellow'}:
        assert seen and seen[0] is frame
        assert inputs == ['skill']
    elif case in {'hidden', 'dead', 'stop', 'identity_changed'}:
        assert inputs == []
    else:
        assert inputs == ['attack']
