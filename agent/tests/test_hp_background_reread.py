"""Reject background OCR without accepting changed or invented HP digits."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import pytest
from maa.custom_action import CustomAction
from maa.define import OCRResult

from action.combat.core.provider import CombatCheck, parse_hp_fraction
from test_r03_recruitment_native import native_hub
from test_support.fakes import FakeContext


def readings(monkeypatch, raw, filtered):
    context, child = FakeContext(), FakeContext()
    image, calls = object(), []
    raw_ocr = OCRResult(box=[70, 15, 190, 29], score=.99, text=raw)
    detail = SimpleNamespace(hit=True, best_result=raw_ocr, all_results=[raw_ocr])
    monkeypatch.setattr(context, 'run_recognition', lambda *_: detail)
    context.clone = lambda: child
    child.override_pipeline = lambda patch: child.pipeline_overrides.append(patch) or True

    def recognize(name, supplied):
        calls.append((name, supplied))
        return SimpleNamespace(hit=True, best_result=SimpleNamespace(text=filtered))

    child.run_recognition = recognize
    return context, child, image, calls, detail


@pytest.mark.parametrize('raw,filtered,reason', [
    ('10823/10823VV', '10823/10823', 'alive'),
    ('886/10823V', '886/10823', 'alive'),
    ('0/10823V', '0/10823', 'dead'),
    ('10823/10823VV', '10828/10823', 'hp_text_invalid'),
    ('10823/10823VV', '10823/10828', 'hp_text_invalid'),
    ('886/10823V', '886/1082', 'hp_text_invalid'),
    ('0/10823V', '8/10823', 'hp_text_invalid'),
    ('10823/10823VV', '10823/10823V', 'hp_text_invalid'),
    ('10823/10823VV', '', 'hp_text_invalid'),
])
def test_background_reread_requires_both_original_numeric_fields(monkeypatch, raw, filtered, reason):
    context, child, image, calls, _ = readings(monkeypatch, raw, filtered)
    before = deepcopy(context.node_data)
    hp = CombatCheck().read_hp_from_frame(context, image)
    assert hp.reason == reason
    assert calls == [('检查血量', image)]
    assert context.node_data == before and context.pipeline_overrides == []
    assert not context.actions and not child.actions
    assert child.pipeline_overrides == [{'检查血量': {'recognition': {'param': {
        'color_filter': '检查血量_白字',
    }}}}]
    if reason == 'dead':
        assert hp.current == 0 and not hp.alive


@pytest.mark.parametrize('raw', [
    '10823/10V823', '10823/10823V2', '10823/10823/9', '10823/10823 5/6',
    '-1/10823V', '1.5/10823V', '/10823V', '10823/V', '10823/108V',
    'HP10823/10823', '10823/10823', '0/10823',
])
def test_unrelated_or_already_valid_hp_does_not_use_background_reread(monkeypatch, raw):
    context, _, image, calls, _ = readings(monkeypatch, raw, '10823/10823')
    context.clone = lambda: pytest.fail('This observation cannot authorize a reread')
    hp = CombatCheck().read_hp_from_frame(context, image)
    assert hp == parse_hp_fraction(raw)
    assert not calls


@pytest.mark.parametrize('failure', ['multi', 'different_best', 'stopped', 'stop_during', 'clone', 'override', 'ocr'])
def test_background_reread_refuses_ambiguous_or_failed_observation(monkeypatch, failure):
    context, child, image, _, detail = readings(monkeypatch, '10823/10823VV', '10823/10823')
    if failure == 'multi':
        detail.all_results.append(detail.all_results[0])
    elif failure == 'different_best':
        detail.best_result = SimpleNamespace(text='1/10823V')
    elif failure == 'stopped':
        context.tasker.stopping = True
    elif failure == 'stop_during':
        def recognize(*_):
            context.tasker.stopping = True
            return SimpleNamespace(hit=True, best_result=SimpleNamespace(text='10823/10823'))
        child.run_recognition = recognize
    elif failure == 'clone':
        def clone(): raise RuntimeError('Unavailable')
        context.clone = clone
    elif failure == 'override':
        child.override_pipeline = lambda _: False
    elif failure == 'ocr':
        def recognize(*_): raise RuntimeError('Unavailable')
        child.run_recognition = recognize
    hp = CombatCheck().read_hp_from_frame(context, image)
    assert hp.reason == 'hp_text_invalid' and not hp.alive
    assert not context.actions and not child.actions


def test_native_background_letters_need_same_frame_white_text_confirmation(tmp_path):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        font, scale = cv2.FONT_HERSHEY_SIMPLEX, .55
        text = '10823/10823'
        cv2.putText(frame, text, (75, 36), font, scale, (255, 255, 255), 1)
        width = cv2.getTextSize(text, font, scale, 1)[0][0]
        cv2.putText(frame, 'VV', (75 + width, 36), font, scale, (150, 150, 150), 1)
        errors = []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    context.override_pipeline({'检查血量': {'recognition': {'param': {'roi': [70, 15, 190, 29]}}}})
                    before = context.get_node_data('检查血量')
                    raw = context.run_recognition('检查血量', frame)
                    assert raw.hit and not parse_hp_fraction(raw.best_result.text).alive
                    hp = CombatCheck().read_hp_from_frame(context, frame)
                    assert (hp.current, hp.maximum, hp.reason) == (10823, 10823, 'alive'), hp
                    assert context.get_node_data('检查血量') == before
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action('HpBackgroundProbe', Probe())
        job = tasker.post_task('HpBackgroundProbe', {'HpBackgroundProbe': {
            'recognition': 'DirectHit', 'action': {'type': 'Custom', 'param': {'custom_action': 'HpBackgroundProbe'}},
            'next': [], 'on_error': [],
        }}).wait()
        assert not errors, errors
        assert job.succeeded and not expired and not inputs
