"""Synthetic orb colors and same-frame HUD evidence; no device screenshots."""
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from action.combat.actions.oblivion_form import classify_oblivion_form, _hsv_channels
from action.combat.actions.CombatActions import CombatActions
from action.combat.actions.signal_ball_layout import SIGNAL_BALL_SLOT_CENTERS
from action.combat.roles import oblivion as module
from action.combat.roles.oblivion import Oblivion


def test_numpy_hsv_preserves_existing_uint8_color_thresholds():
    colors = np.random.default_rng(42).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    colors[0, :, :] = np.arange(256, dtype=np.uint8)[:, None]
    actual = np.stack(_hsv_channels(colors), axis=-1)
    np.testing.assert_array_equal(actual, cv2.cvtColor(colors, cv2.COLOR_BGR2HSV))


@pytest.mark.parametrize('offset', [(0, 0), (-120, 35)])
@pytest.mark.parametrize('colors,expected', [
    ('YYYYYYYY', 'normal'), ('RRRRRRRR', 'main'), ('BBBBBBBB', 'main'),
    ('RBRBRBRB', 'main'), ('YYY?????', 'normal'), ('BBR?????', 'main'),
    ('YY??????', 'unknown'), ('YRRRRRRR', 'unknown'), ('????????', 'unknown'),
])
def test_agreeing_visible_slots_define_form_without_background_fallback(offset, colors, expected):
    centers = [(x+offset[0], y+offset[1]) for x,y in SIGNAL_BALL_SLOT_CENTERS]
    image = np.zeros((720, 1280, 3), np.uint8)
    palette = {'Y': (0, 220, 220), 'R': (40, 40, 220), 'B': (220, 140, 40)}
    for (x,y), color in zip(centers, colors):
        if color != '?':
            cv2.circle(image, (x,y), 14, palette[color], -1)
            cv2.circle(image, (x,y), 5, (255,255,255), -1)
    assert classify_oblivion_form(image, centers, 8) == expected


@pytest.mark.parametrize('count', [0, 1, 2, 17, True, None])
def test_missing_or_invalid_ball_count_cannot_establish_form(count):
    image = np.full((720, 1280, 3), (220,140,40), np.uint8)
    assert classify_oblivion_form(image, SIGNAL_BALL_SLOT_CENTERS, count) == 'unknown'


@pytest.mark.parametrize('count,colors,expected', [
    (1, 'YBBBBBBB', 'normal'), (2, 'YYRRRRRR', 'normal'),
    (1, 'RYYYYYYY', 'unknown'), (2, 'YBYYYYYY', 'unknown'),
    (2, 'Y?YYYYYY', 'unknown'), (0, 'YYYYYYYY', 'unknown'),
    (True, 'YYYYYYYY', 'unknown'),
])
def test_sparse_normal_confirmation_requires_every_occupied_orb_to_be_yellow(count, colors, expected):
    image = np.zeros((720, 1280, 3), np.uint8)
    palette = {'Y': (0, 220, 220), 'R': (40, 40, 220), 'B': (220, 140, 40)}
    for (x, y), color in zip(SIGNAL_BALL_SLOT_CENTERS, colors):
        if color in palette:
            cv2.circle(image, (x, y), 14, palette[color], -1)
    assert classify_oblivion_form(image, SIGNAL_BALL_SLOT_CENTERS, count) == 'unknown'
    assert classify_oblivion_form(
        image, SIGNAL_BALL_SLOT_CENTERS, count, allow_sparse_normal=True,
    ) == expected


@pytest.mark.parametrize('count,occupied,background,expected', [
    (3, 'YYY', 'BBBBB', 'normal'),
    (3, 'BBR', 'YYYYY', 'main'),
    (4, 'YYYY', 'RRRR', 'normal'),
    (4, 'Y?Y?', 'YYYY', 'unknown'),
    (3, 'YRR', 'RRRRR', 'unknown'),
    (8, 'RBRBRBRB', '', 'main'),
    (16, 'YYYYYYYY', '', 'normal'),
])
def test_empty_slots_cannot_veto_or_supply_form_evidence(count, occupied, background, expected):
    image = np.zeros((720, 1280, 3), np.uint8)
    palette = {'Y': (0, 220, 220), 'R': (40, 40, 220), 'B': (220, 140, 40)}
    for (x, y), color in zip(SIGNAL_BALL_SLOT_CENTERS, occupied + background):
        if color in palette:
            cv2.circle(image, (x, y), 14, palette[color], -1)
    assert classify_oblivion_form(image, SIGNAL_BALL_SLOT_CENTERS, count) == expected


@pytest.mark.parametrize("background", [(0, 0, 140), (140, 90, 20)])
@pytest.mark.parametrize("count,expected", [(3, "unknown"), (4, "normal"), (8, "normal")])
def test_dim_scene_color_cannot_veto_three_clear_yellow_orbs(background, count, expected):
    image = np.zeros((720, 1280, 3), np.uint8)
    for x, y in SIGNAL_BALL_SLOT_CENTERS[:count]:
        cv2.circle(image, (x, y), 13, (0, 220, 220), -1)
    x, y = SIGNAL_BALL_SLOT_CENTERS[0]
    image[y-22:y+22, x-22:x+22] = background
    cv2.circle(image, (x, y), 13, (0, 220, 220), -1)
    # The first slot is ambiguous: its large dim background must not become
    # a red/blue orb. It also cannot supply the third positive yellow slot.
    assert classify_oblivion_form(image, SIGNAL_BALL_SLOT_CENTERS, count) == expected


@pytest.mark.parametrize("opposing", [(0, 0, 110), (110, 70, 15), (0, 0, 220), (220, 140, 40)])
def test_a_real_opposing_orb_still_vetoes_normal_even_when_dim(opposing):
    image = np.zeros((720, 1280, 3), np.uint8)
    for x, y in SIGNAL_BALL_SLOT_CENTERS[:4]:
        cv2.circle(image, (x, y), 13, (0, 220, 220), -1)
    x, y = SIGNAL_BALL_SLOT_CENTERS[0]
    image[y-22:y+22, x-22:x+22] = 0
    cv2.circle(image, (x, y), 13, opposing, -1)
    assert classify_oblivion_form(image, SIGNAL_BALL_SLOT_CENTERS, 4) == "unknown"


@pytest.mark.parametrize("background", [(0, 0, 245), (245, 150, 20)])
def test_bright_occluding_effect_remains_unknown(background):
    image = np.zeros((720, 1280, 3), np.uint8)
    for x, y in SIGNAL_BALL_SLOT_CENTERS[:4]:
        cv2.circle(image, (x, y), 13, (0, 220, 220), -1)
    x, y = SIGNAL_BALL_SLOT_CENTERS[0]
    image[y-22:y+22, x-22:x+22] = background
    cv2.circle(image, (x, y), 13, (0, 220, 220), -1)
    assert classify_oblivion_form(image, SIGNAL_BALL_SLOT_CENTERS, 4) == "unknown"


@pytest.mark.parametrize('image,centers', [
    (None, SIGNAL_BALL_SLOT_CENTERS),
    (np.zeros((720,1280), np.uint8), SIGNAL_BALL_SLOT_CENTERS),
    (np.zeros((720,1280,3), np.float32), SIGNAL_BALL_SLOT_CENTERS),
    (np.zeros((720,1280,3), np.uint8), None),
    (np.zeros((720,1280,3), np.uint8), [(0,0)]*8),
])
def test_invalid_image_or_layout_cannot_establish_form(image, centers):
    assert classify_oblivion_form(image, centers, 8) == 'unknown'


def test_form_observation_uses_one_frame_and_requires_living_hud(monkeypatch):
    frame = np.zeros((720,1280,3), np.uint8)
    context = object()
    seen = []
    state = SimpleNamespace(hud=False, alive=True)
    role = Oblivion.__new__(Oblivion)
    role.phase = 'ult_open'
    role._close_cast_requested = False
    def hud(ctx, image):
        assert ctx is context and image is frame
        seen.append('hud')
        return state.hud
    def hp(ctx, image):
        assert ctx is context and image is frame
        seen.append('hp')
        return SimpleNamespace(alive=state.alive)
    def count(*, image):
        assert image is frame
        seen.append('count')
        return 8
    role.combat = SimpleNamespace(context=context, combat_check=SimpleNamespace(read_hp_from_frame=hp))
    role.action = SimpleNamespace(_get_image=lambda: frame, count_signal_balls=count)
    monkeypatch.setattr(module, 'is_dodge_button_visible', hud)
    monkeypatch.setattr(module, 'signal_ball_slot_centers', lambda _: SIGNAL_BALL_SLOT_CENTERS)
    monkeypatch.setattr(module, 'classify_oblivion_form', lambda *_, **__: 'main')

    assert role._observe_form() == ('unknown', False)
    assert seen == ['hud']
    state.hud, state.alive = True, False
    assert role._observe_form() == ('unknown', False)
    assert seen == ['hud', 'hud', 'hp']
    state.alive = True
    assert role._observe_form() == ('main', True)
    assert seen[-3:] == ['hud', 'hp', 'count']


def test_supplied_ball_count_frame_is_never_recaptured():
    frame = object()
    actions = CombatActions.__new__(CombatActions)
    actions.context = SimpleNamespace(run_recognition=Mock(return_value=None))
    actions.logger = Mock()
    actions._get_image = Mock(side_effect=AssertionError('must use supplied frame'))
    assert actions.count_signal_balls(image=frame) == 0
    actions.context.run_recognition.assert_called_once_with('统计信号球数量', frame)
