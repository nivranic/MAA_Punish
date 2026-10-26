"""Strict supplied-frame HP evidence, independent of ordinary HP grace policy."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from maa.define import OCRResult, RecognitionDetail

from action.combat.core.provider import CombatCheck, parse_hp_current, parse_hp_fraction
from test_support.fakes import FakeBestResult, FakeContext, FakeRecognitionResult


@pytest.mark.parametrize("text,current,maximum", [
    ("10509/10509", 10509, 10509), (" 5973 / 10509 ", 5973, 10509), ("1/2", 1, 2),
])
def test_complete_positive_fraction_is_alive(text, current, maximum):
    value = parse_hp_fraction(text)
    assert (value.current, value.maximum, value.reason, value.alive) == (current, maximum, "alive", True)


def test_zero_is_known_dead_and_does_not_authorize_input():
    value = parse_hp_fraction("0/10509")
    assert (value.current, value.maximum, value.reason) == (0, 10509, "dead")
    assert not value.alive


@pytest.mark.parametrize("text", [
    "10509/105", "6477/5973", "1/0", "0/0",
])
def test_truncated_or_invalid_range_is_unknown(text):
    value = parse_hp_fraction(text)
    assert value.current is None and value.maximum is None
    assert value.reason == "hp_out_of_range" and not value.alive


@pytest.mark.parametrize("text", [
    None, True, 10509, b"1/2", "", "10509", "10509/", "/10509", "-1/10", "1/-10",
    "HP 1/2", "1/2 extra", "1/2 3/4", "1.5/2", "1|2", "1／2", "１/２",
])
def test_malformed_or_partial_text_is_unknown_without_coercion(text):
    value = parse_hp_fraction(text)
    assert value.current is None and value.maximum is None
    assert value.reason == "hp_text_invalid" and not value.alive


def test_evidence_text_is_bounded_and_observation_immutable():
    value = parse_hp_fraction("unrecognized " * 100)
    assert len(value.text) == 128
    with pytest.raises(FrozenInstanceError):
        value.current = 1


def test_native_maa_detail_is_read_from_exact_supplied_frame_and_current_node(monkeypatch):
    image = object()
    ocr = OCRResult(box=[81, 16, 140, 29], score=0.99, text="5973/10509")
    result = RecognitionDetail(reco_id=1, name="检查血量", algorithm="OCR", hit=True,
        box=ocr.box, all_results=[ocr], filtered_results=[ocr], best_result=ocr,
        raw_detail={}, raw_image=None, draw_images=[])
    context = FakeContext(node_data={"检查血量": {"recognition": {"type": "OCR", "param": {
        "roi": [81, 16, 140, 29], "roi_offset": [2, 0, 0, 0], "only_rec": True,
        "expected": r"\d+/\d+", "threshold": 0.3}}}})
    before = deepcopy(context.node_data)
    calls = []

    class NoController:
        @property
        def controller(self):
            pytest.fail("strict read must not obtain Controller")

    context.tasker = NoController()

    def recognize(name, supplied_image):
        calls.append((name, supplied_image))
        assert context.node_data == before
        return result

    monkeypatch.setattr(context, "run_recognition", recognize)
    check = CombatCheck()
    monkeypatch.setattr(check, "_get_frame", lambda *_: pytest.fail("strict read must not capture"))
    value = check.read_hp_from_frame(context, image)
    assert value.alive and (value.current, value.maximum) == (5973, 10509)
    assert calls == [("检查血量", image)] and context.node_data == before
    assert context.pipeline_overrides == [] and context.actions == []


def test_missing_image_is_unknown_without_recognition(monkeypatch):
    context = FakeContext()
    monkeypatch.setattr(context, "run_recognition", lambda *_: pytest.fail("None is not a frame"))
    value = CombatCheck().read_hp_from_frame(context, None)
    assert value.reason == "hp_frame_missing" and not value.alive


@pytest.mark.parametrize("detail,reason", [
    (None, "hp_not_recognized"), (FakeRecognitionResult(hit=False), "hp_not_recognized"),
    (object(), "hp_detail_invalid"), (False, "hp_detail_invalid"),
    (SimpleNamespace(hit="true", best_result=SimpleNamespace(text="1/2")), "hp_detail_invalid"),
    (SimpleNamespace(hit=1, best_result=SimpleNamespace(text="1/2")), "hp_detail_invalid"),
    (SimpleNamespace(hit=True, best_result=None), "hp_detail_invalid"),
    (SimpleNamespace(hit=True, best_result=object()), "hp_detail_invalid"),
    (SimpleNamespace(hit=True, best_result=SimpleNamespace(text=12)), "hp_detail_invalid"),
    (SimpleNamespace(hit=True, best_result=SimpleNamespace(text="")), "hp_text_invalid"),
])
def test_missing_or_malformed_detail_has_explicit_unknown_reason(monkeypatch, detail, reason):
    context = FakeContext()
    monkeypatch.setattr(context, "run_recognition", lambda *_: detail)
    value = CombatCheck().read_hp_from_frame(context, object())
    assert value.reason == reason and value.current is None and not value.alive


def test_recognition_exception_is_unknown_with_error_type(monkeypatch):
    context = FakeContext()

    def fail(*_):
        raise RuntimeError("recognition unavailable")

    monkeypatch.setattr(context, "run_recognition", fail)
    value = CombatCheck().read_hp_from_frame(context, object())
    assert value.reason == "hp_recognition_error" and value.error_type == "RuntimeError"
    assert not value.alive


def test_broken_detail_property_is_distinct_from_recognition_failure(monkeypatch):
    class BrokenBest:
        @property
        def text(self):
            raise ValueError("malformed detail")

    context = FakeContext()
    monkeypatch.setattr(context, "run_recognition", lambda *_: SimpleNamespace(hit=True, best_result=BrokenBest()))
    value = CombatCheck().read_hp_from_frame(context, object())
    assert value.reason == "hp_detail_invalid" and value.error_type == "ValueError"
    assert not value.alive


@pytest.mark.parametrize("text,expected", [("prefix 321/999 suffix", 321), ("10509/105", 10509)])
def test_ordinary_parse_and_read_keep_existing_compatibility(text, expected):
    context = FakeContext(recognition_map={"检查血量": FakeRecognitionResult(
        hit=True, best_result=FakeBestResult(box=(19, 15, 141, 29), text=text))})
    check = CombatCheck()
    assert parse_hp_current(text) == expected
    assert check.read_current_hp(context, SimpleNamespace(frame=object())) == expected
    assert not check.read_hp_from_frame(context, object()).alive
