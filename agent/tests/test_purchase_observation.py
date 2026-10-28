"""Synthetic field/identity tests; no real purchase-page calibration is claimed."""

from copy import deepcopy
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from audit_config import AuditConfig
from audit_commit import PURCHASE_CASES
from recognition.exclusives import PurchaseObservation as observation


IDENTITY = dict(package="com.example.test", app_instance="a" * 32,
                session_id="b" * 32, execution_id="test:1", run_nonce="c" * 32)


@pytest.fixture
def sample(monkeypatch):
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    clock = SimpleNamespace(value=1000)
    state_reads = []
    node = {"attach": {"audit_run": {"synthetic": "identity"},
                       "audit_intent": {"target": "forged", "quantity": 999}}}
    calls = []
    fields = {"title": "Actual product", "currency": "Actual coin", "price": "7", "quantity": "2"}
    calibration = observation.PurchaseCalibration(30, 20, "title", "Actual product", "actual_product",
        "currency", "Actual coin", "actual_coin", "price", "quantity", "available")

    def recognize(name, *, image):
        calls.append((name, image))
        if name == "available":
            return SimpleNamespace(hit=True)
        text = SimpleNamespace(text=fields[name])
        return SimpleNamespace(hit=True, algorithm="OCR", filtered_results=[text], best_result=text)

    context = SimpleNamespace(tasker=SimpleNamespace(stopping=False),
                              run_recognition=recognize,
                              get_node_data=lambda _: deepcopy(node))
    monkeypatch.setattr(observation, "AUDIT_CONFIG", AuditConfig(True, IDENTITY["session_id"], "d" * 32))
    monkeypatch.setattr(observation, "runtime_paths", lambda: "installed-paths")
    monkeypatch.setattr(observation, "validate_binding", lambda value, *_: (dict(IDENTITY), "fixed-mailbox"))
    monkeypatch.setattr(observation, "PurchaseCommit", lambda *_, **__: SimpleNamespace(
        state=lambda root, identity: state_reads.append((root, identity))))
    monkeypatch.setattr(observation, "boottime_ms", lambda: clock.value)
    monkeypatch.setattr(observation, "CALIBRATIONS", {key: calibration for key in PURCHASE_CASES})
    return SimpleNamespace(image=image, context=context, clock=clock, calls=calls,
                           fields=fields, node=node, state_reads=state_reads)


def run(sample, case_id="D12"):
    cls = getattr(observation, case_id + "PurchaseObservation")
    argv = SimpleNamespace(node_name=PURCHASE_CASES[case_id].observation_node, image=sample.image)
    return cls().analyze(sample.context, argv)


@pytest.mark.parametrize("case_id", ["D12", "D13", "D14"])
def test_same_supplied_frame_and_observed_values_ignore_forged_plan(sample, case_id):
    result = run(sample, case_id)
    assert result.detail == dict(IDENTITY, observed_boottime_ms=1000,
        frame_sha256=hashlib.sha256(sample.image.tobytes()).hexdigest(),
        target="actual_product", currency="actual_coin", unit_price=7, quantity=2)
    assert len(sample.calls) == 5
    assert all(image is sample.image for _, image in sample.calls)
    assert len(sample.state_reads) == 2


@pytest.mark.parametrize("field,value", [
    ("title", "Other product"), ("currency", "Other coin"), ("price", "7.00"),
    ("price", "7/10"), ("quantity", ""), ("quantity", "0"), ("quantity", "02"),
    ("quantity", "2 items"), ("price", "9007199254740992"),
])
def test_unknown_or_ambiguous_fields_never_get_defaults_from_plan(sample, field, value):
    sample.fields[field] = value
    assert run(sample) is None


@pytest.mark.parametrize("case_id", ["D12", "D13", "D14"])
def test_uncalibrated_source_has_no_observation(sample, monkeypatch, case_id):
    monkeypatch.setattr(observation, "CALIBRATIONS", {})
    assert run(sample, case_id) is None
    assert not sample.calls


def test_late_recognition_does_not_refresh_frame_time(sample):
    recognize = sample.context.run_recognition

    def slow(name, *, image):
        result = recognize(name, image=image)
        if name == "available":
            sample.clock.value += 5_000
        return result

    sample.context.run_recognition = slow
    assert run(sample) is None


@pytest.mark.parametrize("failure", ["stopping", "changed_node", "mutated_frame", "unavailable", "multiple_ocr"])
def test_stop_changed_identity_or_frame_and_ambiguous_results_reject(sample, failure):
    recognize = sample.context.run_recognition

    def changed(name, *, image):
        result = recognize(name, image=image)
        if name == "available":
            if failure == "stopping":
                sample.context.tasker.stopping = True
            elif failure == "changed_node":
                sample.node["attach"]["audit_run"] = {"different": "run"}
            elif failure == "mutated_frame":
                image[0, 0, 0] = 1
            elif failure == "unavailable":
                result.hit = False
        elif failure == "multiple_ocr":
            result.filtered_results.append(result.best_result)
        return result

    sample.context.run_recognition = changed
    assert run(sample) is None


def test_normal_mode_neutral_child_does_not_read_clock_plan_or_fields(sample, monkeypatch):
    monkeypatch.setattr(observation, "AUDIT_CONFIG", AuditConfig())
    monkeypatch.setattr(observation, "boottime_ms", lambda: pytest.fail("normal mode needs no clock"))
    sample.context.get_node_data = lambda _: pytest.fail("normal mode needs no run/plan")
    for case_id in PURCHASE_CASES:
        result = run(sample, case_id)
        assert result.box == (0, 0, 1, 1) and result.detail == {"audit_enabled": False}
    assert not sample.calls


def test_installed_calibrations_only_include_captured_cn_detail_pages():
    assert set(observation.CALIBRATIONS) == {"D12", "D13", "D14"}
    assert observation.CALIBRATIONS["D13"].pack_text == "x5"
    assert all(value.quantity_one_only for value in observation.CALIBRATIONS.values())
