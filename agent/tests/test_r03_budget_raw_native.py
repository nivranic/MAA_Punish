"""Exact recruitment fractions remain readable when color filtering loses text."""
import cv2
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.tasker import Tasker

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize('language', ['base', 'en_US', 'zh_TW', 'ja_JP'])
@pytest.mark.parametrize('text,expected', [
    ('3/3', (3, 3)), ('1/3', (1, 3)), ('0/5', (0, 5)),
    ('5/5', (5, 5)), ('3', None), ('4/3', None), ('0/0', None),
])
def test_unfiltered_fraction_keeps_budget_validation(tmp_path, language, text, expected):
    frame = np.full((720, 1280, 3), 220, dtype=np.uint8)
    # Gray glyphs are readable but excluded by the blue/dark text filter.
    cv2.putText(frame, text, (411, 648), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (110, 110, 110), 2, cv2.LINE_AA)
    observed = []

    class Capture(Controller):
        def screencap(self): return frame
        def click(self, *_): raise AssertionError('Budget replay must not send input')

    class Probe(CustomAction):
        def run(self, ctx, argv):
            observed.append(R03Recruitment.read_budget(ctx, frame))
            return True

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_action('ReadBudget', Probe())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task('ReadBudget', {'ReadBudget': {
            'recognition': 'DirectHit', 'action': {'type': 'Custom', 'param': {'custom_action': 'ReadBudget'}},
            'pre_delay': 0, 'post_delay': 0, 'next': [],
        }}).wait()
        assert job.succeeded and observed == [expected]
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize('language', ['base', 'en_US', 'zh_TW', 'ja_JP'])
@pytest.mark.parametrize('text,expected', [('1/5', (1, 5)), ('0/5', (0, 5)), ('15', None), ('4/3', None)])
def test_budget_filter_excludes_gray_character_model_background(tmp_path, language, text, expected):
    frame = np.full((720, 1280, 3), 220, dtype=np.uint8)
    # A synthetic gray character-model surface must not become counter ink.
    cv2.rectangle(frame, (410, 611), (472, 659), (65, 65, 65), -1)
    cv2.putText(frame, text, (411, 648), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (0, 0, 0), 2, cv2.LINE_AA)
    observed = []

    class Capture(Controller):
        def screencap(self): return frame
        def click(self, *_): raise AssertionError('Budget replay must not send input')

    class Probe(CustomAction):
        def run(self, ctx, argv):
            result = ctx.run_recognition('矩阵循生_招募能量_列表_整区', frame)
            observed.append(R03Recruitment._budget_value(result))
            return True

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_action('ReadBudget', Probe())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task('ReadBudget', {'ReadBudget': {
            'recognition': 'DirectHit', 'action': {'type': 'Custom', 'param': {'custom_action': 'ReadBudget'}},
            'pre_delay': 0, 'post_delay': 0, 'next': [], 'on_error': [],
        }}).wait()
        assert job.succeeded and observed == [expected]
    finally:
        stop_and_wait_idle(tasker)
