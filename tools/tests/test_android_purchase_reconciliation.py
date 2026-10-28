"""Host reconciliation tests use synthetic PNG/effects and no USB transport."""

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "agent"), str(ROOT / "agent/tests")]
import android_audit as audit
import audit_commit as commit
import purchase_reconciliation_contract as contract
from test_audit_purchase import Fixture, IDENTITY, INTENT
from test_purchase_reconciliation import manual_effect, request_for, source_binding, synthetic_terminal_log


def png(width, height, color):
    def chunk(kind, data):
        return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xffffffff).to_bytes(4, "big")
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    pixels = (b"\0" + bytes([color]) * (width * 3)) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


class Adapter:
    def __init__(self, pending):
        self.pending = pending
        self.markers = {("legacy", "pending.json"): commit.canonical(pending),
                        ("legacy", "pending.claim"): contract.CLAIM_BYTES}
        self.source, self.reload = source_binding(IDENTITY)
        self.requests = []
        self.log = None
        self.acceptances = []

    def marker(self, slot, filename):
        return self.markers[(slot, filename)]

    def current(self):
        return dict(IDENTITY)

    def read(self, filename):
        return deepcopy(self.source if filename == "source-state.json" else self.reload)

    def boottime_ms(self):
        return 1000

    def publish_reconciliation(self, request):
        self.requests.append(deepcopy(request))

    def finished_binding(self, identity, runtime):
        assert identity == IDENTITY
        assert runtime == contract.loaded_runtime(self.source, self.reload, IDENTITY)
        assert self.log is not None

    def maintenance_log(self, filename):
        assert filename == "run_20261026_223000_1.jsonl"
        return self.log

    def publish_acceptance(self, slot, acceptance):
        self.acceptances.append(deepcopy(acceptance))
        self.markers[slot, "accepted.json"] = contract.canonical(acceptance)

    def complete(self, request):
        receipt = dict(version=1, state="confirmed_consumed", request=request, reconciled_boottime_ms=1000)
        receipt["checksum_sha256"] = contract.digest(receipt)
        self.markers["legacy", "reconcile.claim"] = (request["request_id"] + "\n").encode()
        self.markers["legacy", "reconciled.json"] = contract.canonical(receipt)
        self.markers["legacy", "reconciled.done"] = contract.canonical({"receipt_sha256": contract.digest(receipt)})
        self.log = synthetic_terminal_log(request, receipt, self.pending).encode()


@pytest.fixture(autouse=True)
def isolated_authorization_root(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "authorization_root", lambda: tmp_path / "authorizations")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    phone = tmp_path / "phone"
    phone.mkdir()
    f = Fixture(phone)
    f.emitter.callback = f.ack
    f.run()
    pending = commit.read_object(f.paths.marker_root / "pending.json")
    initial = request_for(f, pending)
    case = tmp_path / "case.json"
    audit.init_case(case, "D12", {key: value for key, value in INTENT.items() if key != "quantity"})
    event = next(item for item in f.emitter.events if item["audit_event"] == "prepare_commit")
    from datetime import datetime
    monkeypatch.setattr(audit, "datetime", SimpleNamespace(
        now=lambda _: datetime.fromisoformat("2026-10-26T22:30:30+08:00")))
    audit.reserve(case, initial["reservation"]["channel"], initial["reservation"]["source_envelope"], event)
    effect = manual_effect(pending["intent"])
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    for path, frame, color in ((before, effect["before"], 0), (after, effect["after"], 1)):
        path.write_bytes(png(frame["width"], frame["height"], color))
        frame["png_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    effect_path = tmp_path / "effect.json"
    effect_path.write_bytes(contract.canonical(effect))
    return SimpleNamespace(adapter=Adapter(pending), case=case, effect=effect, effect_path=effect_path,
        before=before, after=after, request=tmp_path / "request.json", receipt=tmp_path / "receipt.json")


def prepare(f):
    return audit.prepare_reconciliation(f.adapter, f.case, "legacy", f.effect_path, f.before, f.after, f.request)


def finish(f):
    return audit.finish_reconciliation(f.adapter, f.case, f.request, f.receipt,
                                      run_log_filename="run_20261026_223000_1.jsonl")


def test_explicit_effect_publishes_and_finalizes_without_refunding_or_mutating_original_case(prepared):
    original = prepared.case.read_bytes()
    record = prepare(prepared)
    assert len(prepared.adapter.requests) == 1
    assert record["request"]["case_record_sha256"] == audit.digest(audit.read_case(prepared.case))
    prepared.adapter.complete(record["request"])
    first = finish(prepared)
    second = finish(prepared)
    assert first == second
    assert prepared.case.read_bytes() == original
    case = audit.read_case(prepared.case)
    assert case["reservations"][0]["status"] == "pending_unconfirmed"  # original fact remains
    index_path = prepared.case.with_name(prepared.case.name + ".reconciliations.json")
    index = audit.decode(index_path.read_bytes(), audit.MAX_CASE_BYTES)
    assert len(index["receipts"]) == 1
    # The same original reservation still consumes its full amount and quantity.
    assert case["reservations"][0]["intent"]["quantity"] == INTENT["quantity"]
    original_reservation = case["reservations"][0]
    for nonce, quantity, allowed in (("0" * 32, 8, True), ("1" * 32, 1, False)):
        event = dict(case_id="D12", source_nonce=nonce,
                     intent=dict(INTENT, quantity=quantity))
        event["intent_sha256"] = audit.digest(event["intent"])
        event["source_boottime_ms"] = original_reservation["source_boottime_ms"]
        if allowed:
            audit.reserve(prepared.case, original_reservation["channel"], original_reservation["source_envelope"], event)
        else:
            with pytest.raises(audit.AuditError, match="cumulative budget"):
                audit.reserve(prepared.case, original_reservation["channel"], original_reservation["source_envelope"], event)


def reserve_next(f, path, quantity, nonce):
    original = audit.read_case(f.case)["reservations"][0]
    intent = dict(INTENT, quantity=quantity)
    event = dict(case_id="D12", source_nonce=nonce * 32, intent=intent, intent_sha256=audit.digest(intent),
                 source_boottime_ms=original["source_boottime_ms"])
    audit.reserve(path, original["channel"], original["source_envelope"], event)


def test_new_case_paths_share_one_frozen_authorization_and_its_used_budget(prepared):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    finish(prepared)
    rebuilt = prepared.case.with_name("new-scope-case.json")
    policy = audit.read_case(prepared.case)["policy"]
    audit.init_case(rebuilt, "D12", policy)
    reserve_next(prepared, rebuilt, 8, "0")
    third = prepared.case.with_name("another-scope-case.json")
    audit.init_case(third, "D12", policy)
    with pytest.raises(audit.AuditError, match="cumulative budget"):
        reserve_next(prepared, third, 1, "1")
    authority = audit.read_case(audit.authorization_path("D12", INTENT["target"]))
    assert sum(item["intent"]["quantity"] for item in authority["reservations"]) == 10
    assert audit.read_case(prepared.case)["revision"] == 1


@pytest.mark.parametrize("key,value", [("budget_amount", 700), ("budget_quantity", 100),
    ("cycle_id", "invented-next-cycle"), ("currency", "other-coins"), ("unit_price", 1)])
def test_new_case_policy_cannot_silently_raise_or_roll_over_original_authorization(prepared, key, value):
    policy = dict(audit.read_case(prepared.case)["policy"], **{key: value})
    rebuilt = prepared.case.with_name("raised-policy.json")
    with pytest.raises(audit.AuditError, match="frozen"):
        audit.init_case(rebuilt, "D12", policy)
    assert not rebuilt.exists()


def test_case_persist_failure_keeps_unknown_write_ahead_use_across_rebuild(prepared, monkeypatch):
    original = audit.persist_case

    def fail_local(path, value):
        if path == prepared.case:
            raise OSError("injected case failure after durable authorization")
        return original(path, value)

    monkeypatch.setattr(audit, "persist_case", fail_local)
    with pytest.raises(OSError):
        reserve_next(prepared, prepared.case, 8, "0")
    monkeypatch.setattr(audit, "persist_case", original)
    rebuilt = prepared.case.with_name("after-unknown-case.json")
    audit.init_case(rebuilt, "D12", audit.read_case(prepared.case)["policy"])
    with pytest.raises(audit.AuditError, match="cumulative budget"):
        reserve_next(prepared, rebuilt, 1, "1")


def test_product_authorization_lock_excludes_a_different_case_file(prepared):
    rebuilt = prepared.case.with_name("concurrent-case.json")
    audit.init_case(rebuilt, "D12", audit.read_case(prepared.case)["policy"])
    with audit.case_lock(audit.authorization_path("D12", INTENT["target"])):
        with pytest.raises(audit.AuditError, match="already in use"):
            reserve_next(prepared, rebuilt, 1, "1")
    assert audit.read_case(rebuilt)["reservations"] == []


@pytest.mark.parametrize("change", ["not_terminal", "late_ready", "different_reads", "other_run", "no_ready"])
def test_host_finish_never_publishes_final_acceptance_from_unproven_device_log(prepared, change):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    import json
    rows = [json.loads(line) for line in prepared.adapter.log.splitlines()]
    if change == "not_terminal":
        rows.pop()
    elif change == "late_ready":
        rows[1], rows[2] = rows[2], rows[1]
    elif change == "no_ready":
        rows.pop(1)
    elif change == "other_run":
        terminal = json.loads(rows[2]["text"].split(" ", 1)[1])
        terminal["maintenance_identity"]["run_nonce"] = "0" * 32
        rows[2]["text"] = "purchase_maintenance_terminal " + contract.canonical(terminal).decode()
    prepared.adapter.log = b"".join(contract.canonical(row) + b"\n" for row in rows)
    if change == "different_reads":
        calls = iter((prepared.adapter.log, prepared.adapter.log + b"\n"))
        prepared.adapter.maintenance_log = lambda _: next(calls)
    with pytest.raises((ValueError, audit.AuditError)):
        finish(prepared)
    assert prepared.adapter.acceptances == []
    assert not prepared.receipt.exists()


def test_host_final_acceptance_is_durable_before_phone_publication_failure(prepared):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    original_case = prepared.case.read_bytes()

    def fail(slot, acceptance):
        target = prepared.receipt.with_name(prepared.receipt.name + ".acceptance.json")
        assert audit.decode(target.read_bytes(), contract.MAX_ACCEPTANCE_BYTES) == acceptance
        raise OSError("injected final publication failure")

    prepared.adapter.publish_acceptance = fail
    with pytest.raises(OSError):
        finish(prepared)
    assert prepared.case.read_bytes() == original_case
    assert not prepared.receipt.exists() and not prepared.adapter.acceptances


@pytest.mark.parametrize("stage", ["file_fsync", "directory_fsync"])
def test_final_acceptance_retries_reprove_durability_then_resume_without_refund(prepared, monkeypatch, stage):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    authority = audit.authorization_path("D12", INTENT["target"])
    originals = {path: path.read_bytes() for path in (prepared.case, authority)}
    acceptance_path = prepared.receipt.with_name(prepared.receipt.name + ".acceptance.json")
    calls = []
    if stage == "file_fsync":
        original = audit.os.fsync
        target, name = audit.os, "fsync"
    else:
        original = audit.sync_record_directory
        target, name = audit, "sync_record_directory"

    def fail(value):
        calls.append(value)
        raise OSError("injected continuing final evidence durability failure")

    monkeypatch.setattr(target, name, fail)
    saved = None
    for _ in range(2):
        with pytest.raises(OSError, match="durability failure"):
            finish(prepared)
        raw = acceptance_path.read_bytes()
        assert raw == (saved or raw)
        saved = raw
        assert prepared.adapter.acceptances == [] and not prepared.receipt.exists()
        assert all(path.read_bytes() == before for path, before in originals.items())
    assert len(calls) == 2
    monkeypatch.setattr(target, name, original)
    first = finish(prepared)
    second = finish(prepared)
    assert first == second and len(prepared.adapter.acceptances) == 2
    assert acceptance_path.read_bytes() == saved
    assert all(path.read_bytes() == before for path, before in originals.items())


def test_existing_acceptance_has_checked_file_and_directory_sync_before_each_publication(prepared, monkeypatch):
    import os

    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    finish(prepared)
    acceptance_path = prepared.receipt.with_name(prepared.receipt.name + ".acceptance.json")
    inode = acceptance_path.stat().st_ino
    events = []
    original_sync = audit.os.fsync
    original_directory = audit.sync_record_directory
    original_publish = prepared.adapter.publish_acceptance

    def sync(descriptor):
        if os.fstat(descriptor).st_ino == inode:
            events.append("file_fsync")
        return original_sync(descriptor)

    def directory(path):
        events.append("directory_fsync")
        return original_directory(path)

    def publish(slot, acceptance):
        events.append("publish")
        return original_publish(slot, acceptance)

    monkeypatch.setattr(audit.os, "fsync", sync)
    monkeypatch.setattr(audit, "sync_record_directory", directory)
    prepared.adapter.publish_acceptance = publish
    finish(prepared)
    assert events[:3] == ["file_fsync", "directory_fsync", "publish"]


def test_concurrent_host_finish_cannot_publish_or_touch_existing_evidence(prepared):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    finish(prepared)
    acceptance = prepared.receipt.with_name(prepared.receipt.name + ".acceptance.json")
    originals = {path: path.read_bytes() for path in (prepared.case, acceptance)}
    prepared.adapter.acceptances.clear()
    with audit.case_lock(prepared.case):
        with pytest.raises(audit.AuditError, match="already in use"):
            finish(prepared)
    assert prepared.adapter.acceptances == []
    assert all(path.read_bytes() == before for path, before in originals.items())


@pytest.mark.parametrize("linked_parent", [False, True])
def test_checked_record_refuses_linked_existing_acceptance_or_ancestor(tmp_path, linked_parent):
    real = tmp_path / "real"
    real.mkdir()
    original = real / "acceptance.json"
    original.write_bytes(audit.canonical({"fixture": True}) + b"\n")
    linked = tmp_path / "linked"
    try:
        if linked_parent:
            linked.symlink_to(real, target_is_directory=True)
            path = linked / original.name
        else:
            linked.symlink_to(original)
            path = linked
    except OSError:
        pytest.skip("sandbox/host denies creating symlink; rejection check remains unverified")
    with pytest.raises(audit.AuditError, match="linked|redirected"):
        audit.write_record_exclusive(path, {"fixture": True}, verify_existing=True)
    assert original.read_bytes() == audit.canonical({"fixture": True}) + b"\n"


def test_checked_record_rejects_reported_windows_reparse_ancestor_before_open(tmp_path, monkeypatch):
    import stat

    parent = tmp_path / "reported-junction"
    parent.mkdir()
    path = parent / "acceptance.json"
    original = Path.lstat

    def lstat(candidate, *args, **kwargs):
        if candidate == parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(audit.os, "open", lambda *_, **__: pytest.fail("redirect must be rejected before opening"))
    with pytest.raises(audit.AuditError, match="linked|redirected"):
        audit.write_record_exclusive(path, {"fixture": True}, verify_existing=True)
    assert not path.exists()


@pytest.mark.parametrize("filename", ["../run_20261026_223000_1.jsonl", "local-success.json", "run_20261026_223000_2.jsonl"])
def test_actual_log_allowlist_rejects_arbitrary_input_before_usb(filename):
    adapter = audit.AuditUsb(SimpleNamespace(verify=lambda: pytest.fail("must reject before USB")))
    with pytest.raises(audit.AuditError):
        adapter.maintenance_log(filename)


@pytest.mark.parametrize("failure", ["unknown", "wrong_balance", "wrong_inventory", "hash", "dimensions", "not_png", "corrupt_png"])
def test_unverified_effect_files_or_arithmetic_never_publish(prepared, failure):
    effect = deepcopy(prepared.effect)
    if failure == "unknown":
        effect["state"] = "UNKNOWN"
    elif failure == "wrong_balance":
        effect["after"]["balance"]["value"] += 1
    elif failure == "wrong_inventory":
        effect["after"]["inventory"]["value"] += 1
    elif failure == "hash":
        effect["before"]["png_sha256"] = "0" * 64
    elif failure == "dimensions":
        effect["before"]["width"] += 1
    elif failure == "not_png":
        prepared.before.write_text("arbitrary file hash is not consumption evidence")
        effect["before"]["png_sha256"] = hashlib.sha256(prepared.before.read_bytes()).hexdigest()
    else:
        raw = bytearray(prepared.before.read_bytes())
        raw[-5] ^= 1
        prepared.before.write_bytes(raw)
        effect["before"]["png_sha256"] = hashlib.sha256(raw).hexdigest()
    prepared.effect_path.write_bytes(contract.canonical(effect))
    with pytest.raises((ValueError, audit.AuditError)):
        prepare(prepared)
    assert not prepared.adapter.requests and not prepared.request.exists()


def test_cross_case_record_never_reconciles_phone_pending(prepared):
    other = prepared.case.with_name("other.json")
    audit.init_case(other, "D13", {key: value for key, value in INTENT.items() if key != "quantity"})
    prepared.case = other
    with pytest.raises(audit.AuditError, match="another original purchase"):
        prepare(prepared)
    assert not prepared.adapter.requests


def test_request_record_is_durable_before_publish_and_failed_publish_keeps_budgets(prepared):
    original = prepared.case.read_bytes()

    def fail(request):
        assert prepared.request.exists()
        raise OSError("injected USB publication failure")

    prepared.adapter.publish_reconciliation = fail
    with pytest.raises(OSError):
        prepare(prepared)
    assert prepared.request.exists() and prepared.case.read_bytes() == original
    assert not prepared.adapter.requests


def test_request_record_fsync_failure_never_publishes_or_returns_budget(prepared, monkeypatch):
    original = prepared.case.read_bytes()
    monkeypatch.setattr(audit.os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("injected fsync failure")))
    with pytest.raises(OSError):
        prepare(prepared)
    assert not prepared.adapter.requests
    assert prepared.case.read_bytes() == original


def test_missing_completion_witness_cannot_finalize(prepared):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    prepared.adapter.markers["legacy", "reconciled.done"] = b"{}"
    with pytest.raises(audit.AuditError, match="completion witness"):
        finish(prepared)
    assert not prepared.receipt.exists()


def test_original_host_reservation_mismatch_cannot_publish(prepared):
    changed = deepcopy(prepared.adapter.pending)
    changed["source_nonce"] = "0" * 32
    prepared.adapter.markers["legacy", "pending.json"] = contract.canonical(changed)
    with pytest.raises(audit.AuditError, match="reservation not found"):
        prepare(prepared)
    assert not prepared.adapter.requests


@pytest.mark.parametrize("slot,filename", [("../escape", "pending.json"), ("legacy", "../escape"), ("a" * 32, "anything")])
def test_marker_allowlist_rejects_arbitrary_path_before_any_adb(slot, filename):
    adapter = audit.AuditUsb(SimpleNamespace(verify=lambda: pytest.fail("must reject before USB")))
    with pytest.raises(audit.AuditError):
        adapter.marker(slot, filename)


class RawReadAdb:
    """Virtual raw transport: observed shell-T newline expansion, no real ADB."""
    serial = "fixture-usb"
    root = "/sdcard/Android/data/com.example.maa/files/.dev-session"

    def __init__(self, payloads, linked_component=None):
        self.payloads = iter(payloads) if isinstance(payloads, list) else None
        self.payload = payloads
        self.linked_component = linked_component
        self.calls = []
        self.verifications = 0
        self.head_reads = 0

    def verify(self):
        self.verifications += 1

    def _run(self, *args, **kwargs):
        import re
        self.calls.append((args, kwargs))
        raw = next(self.payloads) if self.payloads is not None else self.payload
        if args[2:4] == ("shell", "-T"):
            self.head_reads += 1
            return raw.replace(b"\n", b"\r\n")
        assert args[:5] == ("-s", self.serial, "exec-out", "sh", "-c")
        script = args[5]
        # exec-out's script is an argv string, not shell-quoted a second time.
        assert script.startswith('test ! -L "$1"')
        for index in range(1, 5):
            assert f'test ! -L "${index}"' in script
        assert kwargs == {"timeout": 3}
        if self.linked_component is not None:
            assert 1 <= self.linked_component <= 4
            return b""  # A guarded failing exec-out may still report rc0; no head read.
        maximum = int(re.search(r'head -c (\d+) "\$4"\Z', script).group(1))
        self.head_reads += 1
        return raw[:maximum]


@pytest.mark.parametrize("raw", [contract.CLAIM_BYTES,
    contract.CLAIM_BYTES.replace(b"\n", b"\r\n"), b"first\r\nsecond\nthird\r", b"\x00\xff\r\n\n"])
def test_raw_marker_preserves_original_lf_crlf_and_opaque_bytes(raw):
    wire = RawReadAdb(raw)
    assert audit.AuditUsb(wire).marker("legacy", "pending.claim") == raw
    assert wire.verifications == wire.head_reads == 1


@pytest.mark.parametrize("slot", ["legacy", "a" * 32])
@pytest.mark.parametrize("filename", ["pending.claim", "pending.json", "reconcile.claim",
    "reconciled.json", "reconciled.done", "accepted.json"])
def test_raw_marker_keeps_slot_paths_and_maximum_plus_one(slot, filename):
    maximum = contract.MAX_ACCEPTANCE_BYTES if filename == "accepted.json" else contract.MAX_RECONCILE_BYTES
    wire = RawReadAdb(b"x" * (maximum + 2))
    assert len(audit.AuditUsb(wire).marker(slot, filename)) == maximum + 1
    args, _ = wire.calls[0]
    root = wire.root.rsplit("/", 1)[0] + "/.audit-commit"
    directory = root if slot == "legacy" else root + "/commits/" + slot
    assert args[6:] == ("audit-marker", root, root + "/commits", directory, directory + "/" + filename)


@pytest.mark.parametrize("component", [1, 2, 3, 4])
@pytest.mark.parametrize("kind", ["marker", "log"])
def test_raw_reads_keep_each_symlink_guard_before_head(kind, component):
    wire = RawReadAdb(contract.CLAIM_BYTES, linked_component=component)
    adapter = audit.AuditUsb(wire)
    raw = (adapter.marker("legacy", "pending.claim") if kind == "marker" else
           adapter.maintenance_log("run_20261026_223000_1.jsonl"))
    assert raw == b"" and wire.head_reads == 0 and wire.verifications == 1


def test_raw_lf_claim_reaches_prepare_with_original_hash(prepared):
    raw = audit.AuditUsb(RawReadAdb(contract.CLAIM_BYTES)).marker("legacy", "pending.claim")
    prepared.adapter.markers["legacy", "pending.claim"] = raw
    record = prepare(prepared)
    assert record["request"]["claim_sha256"] == hashlib.sha256(contract.CLAIM_BYTES).hexdigest()
    assert len(prepared.adapter.requests) == 1


@pytest.mark.parametrize("raw", [contract.CLAIM_BYTES.replace(b"\n", b"\r\n"),
    contract.CLAIM_BYTES + b"\r\n\x00"])
def test_raw_real_crlf_or_mixed_claim_still_rejected(prepared, raw):
    read = audit.AuditUsb(RawReadAdb(raw)).marker("legacy", "pending.claim")
    assert read == raw and read != contract.CLAIM_BYTES
    prepared.adapter.markers["legacy", "pending.claim"] = read
    with pytest.raises(ValueError, match="original pending/claim hash mismatch"):
        prepare(prepared)
    assert not prepared.adapter.requests and not prepared.request.exists()


@pytest.mark.parametrize("newlines", ["lf", "crlf", "mixed"])
def test_raw_maintenance_jsonl_keeps_original_sha_and_embedded_bytes(prepared, newlines):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    raw = prepared.adapter.log
    if newlines == "crlf":
        raw = raw.replace(b"\n", b"\r\n")
    elif newlines == "mixed":
        raw = raw.replace(b"\n", b"\r\n", 1)
    wire = RawReadAdb(raw)
    prepared.adapter.maintenance_log = audit.AuditUsb(wire).maintenance_log
    original_case = prepared.case.read_bytes()
    finish(prepared)
    acceptance = prepared.adapter.acceptances[0]
    assert acceptance["run_log_sha256"] == hashlib.sha256(raw).hexdigest()
    assert acceptance["run_log"].encode("utf-8") == raw
    assert prepared.case.read_bytes() == original_case and wire.head_reads == 2
    args, _ = wire.calls[0]
    files = wire.root.rsplit("/", 1)[0]
    assert args[6:] == ("audit-maintenance-log", files, files + "/log", files + "/log/run",
                        files + "/log/run/run_20261026_223000_1.jsonl")
    assert 'head -c 262145 "$4"' in args[5]


def test_raw_maintenance_lf_crlf_change_between_reads_is_not_normalized(prepared):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    raw = prepared.adapter.log
    wire = RawReadAdb([raw, raw.replace(b"\n", b"\r\n")])
    prepared.adapter.maintenance_log = audit.AuditUsb(wire).maintenance_log
    with pytest.raises(audit.AuditError, match="changed during the terminal read"):
        finish(prepared)
    assert not prepared.adapter.acceptances and not prepared.receipt.exists()


@pytest.mark.parametrize("failure", ["oversize", "partial", "invalid_utf8"])
def test_raw_maintenance_log_size_utf8_and_completeness_remain_strict(prepared, failure):
    record = prepare(prepared)
    prepared.adapter.complete(record["request"])
    if failure == "oversize":
        raw = prepared.adapter.log[:-1] + b" " * contract.MAX_RUN_LOG_BYTES + b"\n"
    elif failure == "partial":
        raw = prepared.adapter.log[:-1]
    else:
        raw = b"\xff\n"
    wire = RawReadAdb(raw)
    prepared.adapter.maintenance_log = audit.AuditUsb(wire).maintenance_log
    with pytest.raises(ValueError):
        finish(prepared)
    if failure == "oversize":
        assert len(audit.AuditUsb(RawReadAdb(raw)).maintenance_log("run_20261026_223000_1.jsonl")) == contract.MAX_RUN_LOG_BYTES + 1
    assert not prepared.adapter.acceptances and not prepared.receipt.exists()
