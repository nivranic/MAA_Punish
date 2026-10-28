"""Persist-before-permit failures, using fake USB/phone state, never a device."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import android_audit as audit
import android_debug as debug

PACKAGE = "com.example.maa"
CHANNEL = {"package": PACKAGE, "app_instance": "1" * 32, "session_id": "2" * 32,
           "execution_id": "run-7", "run_nonce": "3" * 32, "collector_id": "4" * 32,
           "source_challenge": "5" * 32}
POLICY = {"cycle_id": "observed-cycle-1", "target": "inverse_shard", "currency": "cogs",
          "unit_price": 10, "budget_amount": 20, "budget_quantity": 2}
STREAM = "6" * 32
NONCE = "7" * 32


@pytest.fixture(autouse=True)
def isolated_authorization_root(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "authorization_root", lambda: tmp_path / "authorizations")


def run_state():
    return {**{key: CHANNEL[key] for key in audit.IDENTITY_KEYS}, "version": 1, "phase": "running",
            "started_boottime_ms": 9000, "observed_boottime_ms": 10000, "audit_persist_supported": True}


def source(kind="source_anchor", nonce=NONCE):
    value = {"audit_event": kind, **{key: CHANNEL[key] for key in ("execution_id", "run_nonce", "collector_id", "source_challenge")},
             "source_boottime_ms": 10000}
    if kind == "prepare_commit":
        intent = {**POLICY, "quantity": 1}
        value.update(case_id="D12", source_nonce=nonce, intent=intent, intent_sha256=audit.digest(intent))
    return value


def envelope(message=None, sequence=100):
    return {"event": "maa_punish_audit", "session_id": CHANNEL["session_id"], "stream_id": STREAM,
            "sequence": sequence, "monotonic_ns": 10_000_000_000, "level": "INFO", "logger": "agent.purchase",
            "message": json.dumps(message if message is not None else source(), separators=(",", ":"))}


def log_line(value):
    return ("10-23 14:26:48.212 22037  3851 I MaaFw   : agent| " + json.dumps(value, ensure_ascii=False) + "\n").encode()


class Adapter:
    def __init__(self):
        self.now = 10100
        self.run = run_state()
        self.writes = []
        self.adb = object()

    def current(self):
        return self.run.copy()

    def boottime_ms(self):
        return self.now

    def write(self, message, **kwargs):
        self.writes.append(copy.deepcopy(message))


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "case.json"
    audit.init_case(path, "D12", POLICY.copy())
    adapter = Adapter()
    collector = audit.Collector(adapter, path, CHANNEL.copy(), clock=lambda: 1.0)
    return path, adapter, collector


def anchored(collector):
    collector.accept(envelope(), 1.0)


def test_real_logcat_format_and_cross_run_sequence_anchor(setup):
    path, adapter, collector = setup
    anchored(collector)
    ordinary = envelope(sequence=101)
    ordinary["message"] = "ordinary shared logger line"
    collector.accept(audit.parse_line(log_line(ordinary)), 1.0)
    result = collector.accept(audit.parse_line(log_line(envelope(source("prepare_commit"), 102))), 1.0)
    assert result["sequence"] == 102
    assert result["operation"] == "persist_ack"
    assert audit.read_case(path)["reservations"][0]["status"] == "pending_unconfirmed"
    assert len(adapter.writes) == 1


def test_persistent_record_exists_and_readable_before_ack(setup):
    path, adapter, collector = setup
    anchored(collector)
    observed = []
    adapter.write = lambda _, **kwargs: observed.append(audit.read_case(path)["revision"])
    collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert observed == [1]


@pytest.mark.parametrize("value", [True, False, 1.0, 0.1, -1, 2**53, "10", None])
def test_non_integer_or_out_of_range_money_refused(value):
    intent = {**POLICY, "quantity": 1, "unit_price": value}
    with pytest.raises(audit.AuditError):
        audit.validate_intent(intent)


def test_intent_hash_known_canonical_vector():
    intent = {**POLICY, "quantity": 1}
    assert audit.canonical(intent) == b'{"budget_amount":20,"budget_quantity":2,"currency":"cogs","cycle_id":"observed-cycle-1","quantity":1,"target":"inverse_shard","unit_price":10}'
    assert audit.digest(intent) == audit.digest(dict(reversed(list(intent.items()))))
    with pytest.raises(audit.AuditError):
        audit.validate_intent({**intent, "target": "\u9006\u5143\u788e\u7247"})
    with pytest.raises(audit.AuditError):
        audit.validate_intent({**intent, "quantity": audit.MAX_INTEGER})


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'\xff'])
def test_ambiguous_or_invalid_json_refused(raw):
    with pytest.raises(audit.AuditError):
        audit.decode(raw)


@pytest.mark.parametrize("sequence", [100, 102, 99])
def test_gap_or_repeated_sequence_never_ack(setup, sequence):
    path, adapter, collector = setup
    anchored(collector)
    with pytest.raises(audit.AuditError):
        collector.accept(envelope(source("prepare_commit"), sequence), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0


@pytest.mark.parametrize("key", ["execution_id", "run_nonce", "source_challenge", "collector_id"])
def test_old_run_or_challenge_never_ack(setup, key):
    path, adapter, collector = setup
    anchored(collector)
    event = source("prepare_commit")
    event[key] = "old-run" if key == "execution_id" else "a" * 32
    with pytest.raises(audit.AuditError):
        collector.accept(envelope(event, 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0


def test_pre_anchor_old_history_ignored_but_prepare_requires_anchor(setup):
    path, adapter, collector = setup
    old = source()
    old["source_challenge"] = "f" * 32
    assert collector.accept(envelope(old), 1.0) is None
    with pytest.raises(audit.AuditError, match="anchor"):
        collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert adapter.writes == []


@pytest.mark.parametrize("key", ["session_id", "stream_id"])
def test_envelope_identity_change_refused(setup, key):
    _, adapter, collector = setup
    anchored(collector)
    value = envelope(source("prepare_commit"), 101)
    value[key] = "f" * 32
    with pytest.raises(audit.AuditError):
        collector.accept(value, 1.0)
    assert adapter.writes == []


def test_duplicate_nonce_remains_pending_and_not_acked_twice(setup):
    path, adapter, collector = setup
    anchored(collector)
    collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    with pytest.raises(audit.AuditError):
        collector.accept(envelope(source("prepare_commit"), 102), 1.0)
    assert len(adapter.writes) == 1
    assert audit.read_case(path)["revision"] == 1


def test_restart_does_not_reset_pending_or_allow_nonce_replay(setup):
    path, adapter, collector = setup
    anchored(collector)
    collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    restarted = audit.Collector(adapter, path, CHANNEL.copy(), clock=lambda: 1.0)
    restarted.accept(envelope(sequence=200), 1.0)
    with pytest.raises(audit.AuditError, match="already reserved"):
        restarted.accept(envelope(source("prepare_commit"), 201), 1.0)
    assert len(adapter.writes) == 1


def test_pending_across_sessions_consumes_budget(setup):
    path, adapter, collector = setup
    anchored(collector)
    collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    channel2 = {**CHANNEL, "session_id": "b" * 32, "execution_id": "run-8", "run_nonce": "c" * 32}
    adapter.run.update({key: channel2[key] for key in audit.IDENTITY_KEYS})
    second = audit.Collector(adapter, path, channel2, clock=lambda: 1.0)
    def next_event(kind, seq, nonce):
        event = source(kind, nonce)
        event.update(execution_id=channel2["execution_id"], run_nonce=channel2["run_nonce"])
        result = envelope(event, seq)
        result["session_id"] = channel2["session_id"]
        return result
    second.accept(next_event("source_anchor", 300, NONCE), 1.0)
    second.accept(next_event("prepare_commit", 301, "d" * 32), 1.0)
    with pytest.raises(audit.AuditError, match="budget"):
        second.accept(next_event("prepare_commit", 302, "e" * 32), 1.0)
    assert audit.read_case(path)["revision"] == 2
    assert len(adapter.writes) == 2


def test_controller_completed_does_not_release_reservation(setup):
    path, _, collector = setup
    anchored(collector)
    collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    collector.accept(envelope({"audit_event": "controller_completed"}, 102), 1.0)
    assert audit.read_case(path)["reservations"][0]["status"] == "pending_unconfirmed"


@pytest.mark.parametrize("stage", ["fsync", "replace", "readback_fsync"])
def test_disk_failure_never_ack_even_if_record_was_replaced(setup, stage):
    path, adapter, collector = setup
    anchored(collector)
    original = audit.os.fsync
    calls = 0
    def sync(fd):
        nonlocal calls
        calls += 1
        if stage == "fsync" or (stage == "readback_fsync" and calls == 2):
            raise OSError("disk unavailable")
        original(fd)
    with patch.object(audit.os, "fsync", side_effect=sync):
        with patch.object(audit.os, "replace", side_effect=OSError("replace refused")) if stage == "replace" else patch.object(audit.time, "sleep"):
            with pytest.raises(OSError):
                collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0
    authority = audit.authorization_path("D12", POLICY["target"])
    assert audit.read_case(authority)["revision"] == (1 if stage == "readback_fsync" else 0)


def test_ack_transport_failure_retains_budget(setup):
    path, adapter, collector = setup
    anchored(collector)
    adapter.write = lambda _, **kwargs: (_ for _ in ()).throw(debug.DebugError("USB lost"))
    with pytest.raises(debug.DebugError):
        collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert audit.read_case(path)["revision"] == 1


@pytest.mark.parametrize("phone_now", [9999, 14750, 17000])
def test_old_future_or_safety_margin_source_never_ack(setup, phone_now):
    path, adapter, collector = setup
    anchored(collector)
    adapter.now = phone_now
    with pytest.raises(audit.AuditError):
        collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0


def test_becomes_late_during_persistence_keeps_pending_without_ack(setup):
    path, adapter, collector = setup
    anchored(collector)
    original = audit.persist_case
    def delayed(*args):
        original(*args)
        adapter.now = 15000
    with patch.object(audit, "persist_case", side_effect=delayed):
        with pytest.raises(audit.AuditError):
            collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 1


def test_run_changes_during_persistence_keeps_pending_without_ack(setup):
    path, adapter, collector = setup
    anchored(collector)
    original = audit.persist_case
    def changed(*args):
        original(*args)
        adapter.run["execution_id"] = "run-next"
    with patch.object(audit, "persist_case", side_effect=changed):
        with pytest.raises(audit.AuditError):
            collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 1


def test_source_before_run_start_refused(setup):
    path, adapter, collector = setup
    adapter.run["started_boottime_ms"] = 10001
    with pytest.raises(audit.AuditError, match="predates"):
        anchored(collector)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0


@pytest.mark.parametrize("clock_expired", [False, True])
def test_challenge_expiration_does_not_authorize_even_fresh_source(setup, clock_expired):
    path, adapter, collector = setup
    collector.begin()
    anchored(collector)
    event = source("prepare_commit")
    event["source_boottime_ms"] = 40000
    adapter.now = 40100
    if clock_expired:
        adapter.now = 10100
        event["source_boottime_ms"] = 10000
        collector.clock = lambda: 31.0
    with pytest.raises(audit.AuditError):
        collector.accept(envelope(event, 101), collector.clock())
    assert [value["operation"] for value in adapter.writes] == ["source_challenge"]
    assert audit.read_case(path)["revision"] == 0


@pytest.mark.parametrize("phone_lag_ms", [0, 1000])
def test_ack_usb_verification_cannot_outlive_local_challenge(tmp_path, phone_lag_ms):
    clock = [0.0]
    publications = []
    phone_reads = [0]
    adapter = Adapter()

    def boottime():
        phone_reads[0] += 1
        if phone_reads[0] == 1:
            clock[0] += 1.0
        lag = phone_lag_ms if clock[0] > 2.0 else 0
        return 10_000 + round(clock[0] * 1000) - lag

    def process(args, **kwargs):
        if args[1:] == ["-d", "get-serialno"]:
            clock[0] += .25
            return subprocess.CompletedProcess(args, 0, b"usb-device\n", b"")
        if args[3:] == ["get-state"]:
            clock[0] += .25
            return subprocess.CompletedProcess(args, 0, b"device\n", b"")
        publications.append(audit.decode(kwargs["input"])["operation"])
        return subprocess.CompletedProcess(args, 0, b"", b"")

    adapter.boottime_ms = boottime
    writer = audit.AuditUsb(debug.UsbAdb("adb", "usb-device", PACKAGE, run=process))
    adapter.write = writer.write
    path = tmp_path / "case.json"
    audit.init_case(path, "D12", POLICY.copy())
    collector = audit.Collector(adapter, path, CHANNEL.copy(), clock=lambda: clock[0])
    with patch.object(audit.time, "monotonic", side_effect=lambda: clock[0]):
        collector.begin()
        anchor = source()
        anchor["source_boottime_ms"] = 11_500
        collector.accept(envelope(anchor), clock[0])
        clock[0] = 29.7
        event = source("prepare_commit")
        event["source_boottime_ms"] = 39_700 - phone_lag_ms
        with pytest.raises(debug.DebugError, match="operation budget"):
            collector.accept(envelope(event, 101), clock[0])
    assert publications == ["source_challenge"]
    reservation = audit.read_case(path)["reservations"]
    assert len(reservation) == 1
    assert reservation[0]["status"] == "pending_unconfirmed"
    assert reservation[0]["source_nonce"] == NONCE


@pytest.mark.parametrize("delay_before_sample", [True, False])
def test_challenge_expiry_respects_phone_maximum_after_slow_uptime(tmp_path, delay_before_sample):
    clock = [0.0]
    challenge_requests = []

    def process(args, **kwargs):
        if args[1:] == ["-d", "get-serialno"]:
            clock[0] += .001
            return subprocess.CompletedProcess(args, 0, b"usb-device\n", b"")
        if args[3:] == ["get-state"]:
            clock[0] += .001
            return subprocess.CompletedProcess(args, 0, b"device\n", b"")
        if args[-1] == "/proc/uptime":
            if delay_before_sample:
                clock[0] += 1.0
            phone_ms = 10_000 + round(clock[0] * 1000)
            if not delay_before_sample:
                clock[0] += 1.0
            raw = f"{phone_ms // 1000}.{phone_ms % 1000:03d} 9.00\n".encode()
            return subprocess.CompletedProcess(args, 0, raw, b"")
        clock[0] += .005
        request = audit.decode(kwargs["input"])
        if request["operation"] == "source_challenge":
            phone_now = 10_000 + round(clock[0] * 1000)
            challenge_requests.append((request["expires_boottime_ms"], phone_now))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    adapter = audit.AuditUsb(debug.UsbAdb("adb", "usb-device", PACKAGE, run=process))
    adapter.current = run_state
    collector = audit.Collector(adapter, tmp_path / "unused.json", CHANNEL.copy(), clock=lambda: clock[0])
    with patch.object(audit.time, "monotonic", side_effect=lambda: clock[0]):
        collector.begin()
    assert len(challenge_requests) == 1
    requested, phone_now = challenge_requests[0]
    assert phone_now < requested <= phone_now + audit.CHALLENGE_MS


def test_phone_uptime_over_104_days_does_not_overflow_envelope():
    value = envelope()
    value["monotonic_ns"] = 20_000_000_000_000_000
    assert audit.parse_line(log_line(value))["monotonic_ns"] == value["monotonic_ns"]


def test_local_processing_deadline_is_independent_of_phone_clock(setup):
    path, adapter, collector = setup
    anchored(collector)
    collector.clock = lambda: 6.0
    with pytest.raises(audit.AuditError, match="local evidence"):
        collector.accept(envelope(source("prepare_commit"), 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0


def test_missing_corrupt_or_checksum_damaged_case_cannot_collect(tmp_path):
    path = tmp_path / "case.json"
    adapter = Adapter()
    with pytest.raises(FileNotFoundError):
        audit.collect(adapter, path)
    assert not path.exists()
    path.write_bytes(b'{"version":1')
    with pytest.raises(audit.AuditError):
        audit.collect(adapter, path)
    path.unlink()
    audit.init_case(path, "D12", POLICY)
    damaged = json.loads(path.read_text())
    damaged["policy"]["budget_amount"] = 9000
    path.write_text(json.dumps(damaged))
    with pytest.raises(audit.AuditError, match="checksum"):
        audit.collect(adapter, path)
    assert adapter.writes == []


def test_init_refuses_overwrite_and_collect_lock_is_exclusive(setup):
    path, _, _ = setup
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        audit.init_case(path, "other", POLICY)
    assert path.read_bytes() == original
    with audit.case_lock(path):
        with pytest.raises(audit.AuditError, match="already in use"):
            with audit.case_lock(path):
                pytest.fail("second collector acquired same case")
    with audit.case_lock(path):
        assert audit.read_case(path)["revision"] == 0


def test_tampered_intent_or_budget_rejected(setup):
    path, adapter, collector = setup
    anchored(collector)
    event = source("prepare_commit")
    event["intent"]["unit_price"] = 9
    event["intent_sha256"] = audit.digest(event["intent"])
    with pytest.raises(audit.AuditError, match="policy"):
        collector.accept(envelope(event, 101), 1.0)
    assert adapter.writes == []
    assert audit.read_case(path)["revision"] == 0


class UsbProcess:
    def __init__(self):
        self.calls = []
        self.serial = "usb-device"

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[1:] == ["-d", "get-serialno"]:
            raw = (self.serial + "\n").encode()
        elif args[3:] == ["get-state"]:
            raw = b"device\n"
        elif args[-1] == "/proc/uptime":
            raw = b"10.25 99.00\n"
        elif "head" in args:
            raw = b'{"version":1}'
        else:
            raw = b""
        return subprocess.CompletedProcess(args, 0, raw, b"")


def test_audit_adapter_fixed_files_and_no_session_mailbox_write():
    process = UsbProcess()
    adapter = audit.AuditUsb(debug.UsbAdb("adb", "usb-device", PACKAGE, run=process))
    with pytest.raises(audit.AuditError):
        adapter.read("request.json")
    with pytest.raises(audit.AuditError):
        adapter.write({"operation": "heartbeat"})
    adapter.read("audit-state.json")
    adapter.write({"operation": "revoke"})
    with patch.object(audit.time, "monotonic", side_effect=[1.0, 1.1]):
        assert adapter.boottime_ms() >= 10350
    operations = [args for args, _ in process.calls if args[1:3] == ["-s", "usb-device"]]
    assert any("audit-request.json" in " ".join(args) for args in operations)
    assert not any("close.json" in " ".join(args) or "/request.json" in " ".join(args) for args in operations)
    process.serial = "another-device"
    with pytest.raises(debug.DebugError):
        adapter.write({"operation": "revoke"})


def test_collector_exit_revokes_only_own_channel_and_closes_owned_stream(setup):
    path, adapter, _ = setup
    class Stream:
        closed = False
        def next(self, timeout):
            raise KeyboardInterrupt
        def close(self):
            self.closed = True
    stream = Stream()
    with pytest.raises(KeyboardInterrupt):
        audit.collect(adapter, path, logcat_factory=lambda _: stream)
    assert stream.closed
    assert [item["operation"] for item in adapter.writes] == ["source_challenge", "revoke"]
    assert adapter.writes[0]["collector_id"] == adapter.writes[1]["collector_id"]
    assert audit.read_case(path)["revision"] == 0


def test_owned_logcat_process_is_bounded_and_terminated():
    process = UsbProcess()
    adb = debug.UsbAdb("adb", "usb-device", PACKAGE, run=process)
    class Child:
        def __init__(self):
            self.stdout = io.BytesIO(log_line(envelope()))
            self.terminated = False
        def poll(self):
            return None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            return 0
    child = Child()
    calls = []
    def spawn(args, **kwargs):
        calls.append(args)
        return child
    logcat = audit.Logcat(adb, popen=spawn)
    assert audit.parse_line(logcat.next(1)[0])["sequence"] == 100
    logcat.close()
    assert child.terminated
    assert "-c" not in calls[0]
    assert calls[0][1:3] == ["-s", "usb-device"]


def test_init_cli_requires_explicit_policy_without_adb(tmp_path):
    path = tmp_path / "case.json"
    assert audit.main(["init-case", "--case-record", str(path), "--case-id", "D12",
                       "--cycle-id", "verified-1", "--target", "inverse_shard", "--currency", "cogs",
                       "--unit-price", "10", "--budget-amount", "20", "--budget-quantity", "2"]) == 0
    assert audit.read_case(path)["policy"]["budget_amount"] == 20
