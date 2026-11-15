"""Closing casts need positive form evidence even when only two orbs remain."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from action.combat.actions.oblivion_form import classify_oblivion_form
from action.combat.actions.signal_ball_layout import SIGNAL_BALL_SLOT_CENTERS
from action.combat.roles import oblivion as module
from action.combat.roles.oblivion import Oblivion
from test_oblivion_yield import make_role


def orb_frame(colors):
    frame = np.zeros((720, 1280, 3), np.uint8)
    palette = {'R': (40, 40, 220), 'B': (220, 140, 40), 'Y': (0, 220, 220)}
    for (x, y), color in zip(SIGNAL_BALL_SLOT_CENTERS, colors):
        if color in palette:
            cv2.circle(frame, (x, y), 14, palette[color], -1)
    return frame


@pytest.mark.parametrize('count,colors,expected', [
    (1, 'RYYYYYYY', 'main'), (1, 'BYYYYYYY', 'main'),
    (2, 'RRYYYYYY', 'main'), (2, 'RBYYYYYY', 'main'),
    (2, 'BBYYYYYY', 'main'), (2, 'RYRRRRRR', 'unknown'),
    (2, 'R?RRRRRR', 'unknown'), (1, 'YYYYYYYY', 'unknown'),
    (0, 'RRRRRRRR', 'unknown'), (True, 'RRRRRRRR', 'unknown'),
])
def test_sparse_main_requires_every_occupied_orb_and_explicit_permission(count, colors, expected):
    frame = orb_frame(colors)
    assert classify_oblivion_form(frame, SIGNAL_BALL_SLOT_CENTERS, count) == 'unknown'
    assert classify_oblivion_form(
        frame, SIGNAL_BALL_SLOT_CENTERS, count, allow_sparse_main=True,
    ) == expected


@pytest.mark.parametrize('case', [
    'one_red', 'two_red', 'two_blue', 'red_blue', 'yellow_mixed', 'missing',
    'zero', 'unready', 'no_request', 'reset', 'opening', 'hidden', 'dead',
    'identity_changed', 'stopped', 'stop_during_energy',
])
def test_sparse_main_can_retry_only_current_close_with_live_ready_same_frame(monkeypatch, case):
    role, state, inputs = make_role('ult_close')
    role._close_cast_requested = case != 'no_request'
    if case == 'reset':
        role.reset_state()
        role.phase = 'ult_close'
    if case == 'opening':
        role.phase = 'ult_open'
    initial_phase = role.phase
    context = role.combat.context
    context.tasker.stopping = case == 'stopped'
    state.allowed = case != 'identity_changed'
    colors = {'one_red': 'R', 'two_blue': 'BB', 'red_blue': 'RB',
              'yellow_mixed': 'RY', 'missing': 'R?', 'zero': ''}.get(case, 'RR')
    frame = orb_frame(colors)
    seen = []

    def hud(ctx, image):
        assert ctx is context and image is frame
        return case != 'hidden'

    def hp(ctx, image):
        assert ctx is context and image is frame
        return SimpleNamespace(alive=case != 'dead')

    def balls(*, image):
        assert image is frame
        return len(colors)

    def energy(*, image=None):
        if image is not None:
            assert image is frame
        seen.append(image)
        if case == 'stop_during_energy':
            context.tasker.stopping = True
        return case != 'unready'

    monkeypatch.setattr(module, 'is_dodge_button_visible', hud)
    monkeypatch.setattr(module, 'signal_ball_slot_centers', lambda _: SIGNAL_BALL_SLOT_CENTERS)
    role.combat.combat_check = SimpleNamespace(read_hp_from_frame=hp)
    role.action._get_image = lambda: frame
    role.action.count_signal_balls = balls
    role.action.check_Skill_energy_bar = energy
    role.action.use_skill = lambda _: inputs.append('skill')
    role.action.auxiliary_machine = lambda: inputs.append('auxiliary')

    role.perform()

    assert role.phase == initial_phase  # A request alone cannot confirm exit.
    if case in {'one_red', 'two_red', 'two_blue', 'red_blue'}:
        assert seen[0] is frame
        assert inputs == ['skill']
        # The next observation must confirm the normal form before switching.
        frame[:] = orb_frame('Y' * len(colors))
        role.action.check_Skill_energy_bar = lambda **_: False
        role.perform()
        assert role.phase == 'switch'
        assert inputs == ['skill', 'auxiliary']
    elif case in {'hidden', 'dead', 'identity_changed', 'stopped', 'stop_during_energy'}:
        assert inputs == []
    else:
        assert inputs == ['attack']
