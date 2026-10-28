#!/usr/bin/env python3
"""Persist-before-permit collector. Python 3.12; no session ownership or game input.

Wire contract: docs/plans/2026-10-23-iqoo16-persist-ack.md. The matching App and
Python boundary consumer are required; missing capabilities fail closed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shlex
import stat
import subprocess
import sys
import threading
import time
import uuid
import zlib

from android_debug import DebugError, TOKEN, UsbAdb, validate_hello, validate_state

# Shared, standard-library-only manual-effect schema. Do not import the Maa
# runtime into a host collector or duplicate the host/phone arithmetic checks.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import purchase_reconciliation_contract as reconciliation

MAX_BYTES = 16_384
MAX_CASE_BYTES = 4_194_304
MAX_INTEGER = 2**53 - 1
SOURCE_AGE_MS = 5_000
CHALLENGE_MS = 30_000
SAFETY_MS = 250
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
LOG_PREFIX = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+[VDIWEF]\s+MaaFw\s*:\s*agent\|\s*(.*)$")
INTENT_KEYS = {"cycle_id", "target", "currency", "unit_price", "quantity", "budget_amount", "budget_quantity"}
IDENTITY_KEYS = {"package", "app_instance", "session_id", "execution_id", "run_nonce"}
CHANNEL_KEYS = IDENTITY_KEYS | {"collector_id", "source_challenge"}
SOURCE_KEYS = {"audit_event", "execution_id", "run_nonce", "collector_id", "source_challenge", "source_boottime_ms"}
STATE_KEYS = IDENTITY_KEYS | {"version", "phase", "started_boottime_ms", "observed_boottime_ms", "audit_persist_supported"}


class AuditError(DebugError):
    """Evidence, budget or current-run binding is insufficient to authorize."""


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def decode(raw: bytes, maximum: int = MAX_BYTES) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AuditError("duplicate JSON key")
            result[key] = value
        return result

    if len(raw) > maximum:
        raise AuditError("JSON exceeds size bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(AuditError("nonfinite JSON number")))
    except (ValueError, UnicodeError) as error:
        raise AuditError("invalid UTF-8 JSON") from error
    if type(value) is not dict:
        raise AuditError("expected JSON object")
    return value


def fields(value: dict, expected: set[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise AuditError("unexpected object fields")


def integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise AuditError("expected bounded strict integer")
    return value


def name(value: object) -> str:
    if type(value) is not str or not NAME.fullmatch(value):
        raise AuditError("expected ASCII identifier")
    return value


def token(value: object) -> str:
    if type(value) is not str or not TOKEN.fullmatch(value):
        raise AuditError("expected random token")
    return value


def validate_intent(value: dict) -> dict:
    fields(value, INTENT_KEYS)
    for key in ("cycle_id", "target", "currency"):
        name(value[key])
    for key in ("unit_price", "budget_amount", "budget_quantity"):
        integer(value[key])
    integer(value["quantity"], 1)
    integer(value["unit_price"] * value["quantity"])
    return value


def validate_channel(value: dict) -> None:
    fields(value, CHANNEL_KEYS)
    name(value["execution_id"])
    if type(value["package"]) is not str or not value["package"]:
        raise AuditError("missing package")
    for key in CHANNEL_KEYS - {"execution_id", "package"}:
        token(value[key])


def validate_run(value: dict, session: dict, package: str) -> dict:
    fields(value, STATE_KEYS)
    if type(value["version"]) is not int or value["version"] != 1 or value["audit_persist_supported"] is not True:
        raise AuditError("App does not support persist ACK")
    if value["phase"] != "running" or value["package"] != package:
        raise AuditError("no active matching audit run")
    for key in ("app_instance", "session_id"):
        if value[key] != session[key]:
            raise AuditError("audit run/session mismatch")
    token(value["run_nonce"])
    name(value["execution_id"])
    if integer(value["started_boottime_ms"]) > integer(value["observed_boottime_ms"]):
        raise AuditError("invalid run clock")
    return value


class AuditUsb:
    """Independent allowlist using existing USB target verification, never Session."""

    def __init__(self, adb: UsbAdb):
        self.adb = adb

    def read(self, filename: str) -> dict:
        if filename in {"hello.json", "state.json", "source-state.json", "reload-state.json"}:
            return self.adb.read(filename)
        if filename not in {"audit-state.json", "audit-permit.json"}:
            raise AuditError("audit mailbox is not readable")
        self.adb.verify()
        path = f"{self.adb.root}/{filename}"
        raw = self.adb._run("-s", self.adb.serial, "shell", "-T", "head", "-c",
                            str(MAX_BYTES + 1), shlex.quote(path), timeout=2)
        return decode(raw)

    def marker(self, slot: str, filename: str) -> bytes:
        if slot != "legacy":
            token(slot)
        if filename not in {"pending.claim", "pending.json", "reconcile.claim", "reconciled.json", "reconciled.done", "accepted.json"}:
            raise AuditError("marker file is not readable")
        self.adb.verify()
        marker_root = self.adb.root.rsplit("/", 1)[0] + "/.audit-commit"
        directory = marker_root if slot == "legacy" else marker_root + "/commits/" + slot
        path = directory + "/" + filename
        # All path components are fixed or a strict token. Refuse symlinks before
        # a bounded read; callers cannot select arbitrary app files.
        maximum = reconciliation.MAX_ACCEPTANCE_BYTES if filename == "accepted.json" else reconciliation.MAX_RECONCILE_BYTES
        script = f'test ! -L "$1" && test ! -L "$2" && test ! -L "$3" && test ! -L "$4" && head -c {maximum + 1} "$4"'
        # Marker claims and frozen marker SHA values require the original bytes.
        # exec-out receives the script as argv; quoting it again changes the command.
        return self.adb._run("-s", self.adb.serial, "exec-out", "sh", "-c", script,
                             "audit-marker", marker_root, marker_root + "/commits",
                             directory, path, timeout=3)

    def publish_reconciliation(self, request: dict) -> None:
        if request.get("operation") not in {"reconcile_purchase", "revalidate_purchase"}:
            raise AuditError("unknown reconciliation operation")
        token(request.get("request_id"))
        current = self.current()
        if {key: current[key] for key in IDENTITY_KEYS} != request["maintenance_identity"]:
            raise AuditError("maintenance run changed before request publication")
        raw = canonical(request)
        if len(raw) > reconciliation.MAX_RECONCILE_BYTES:
            raise AuditError("reconciliation request exceeds bound")
        self.adb.verify()
        temporary = f"{self.adb.root}/.purchase-reconcile-{request['request_id']}.tmp"
        destination = f"{self.adb.root}/purchase-reconcile.json"
        script = 'trap \'rm -f "$1"\' 0; test ! -L "$2" && cat > "$1" && mv "$1" "$2"'
        self.adb._run("-s", self.adb.serial, "shell", "-T", "sh", "-c", shlex.quote(script),
                      "audit-reconciliation", shlex.quote(temporary), shlex.quote(destination),
                      input=raw, timeout=3)

    def maintenance_log(self, filename: str) -> bytes:
        if type(filename) is not str or not reconciliation.RUN_LOG_NAME.fullmatch(filename):
            raise AuditError("maintenance log filename is not allowlisted")
        self.adb.verify()
        files = self.adb.root.rsplit("/", 1)[0]
        root = files + "/log/run"
        script = 'test ! -L "$1" && test ! -L "$2" && test ! -L "$3" && test ! -L "$4" && head -c 262145 "$4"'
        # Keep the exact JSONL bytes used by the acceptance hash and embedded log.
        return self.adb._run("-s", self.adb.serial, "exec-out", "sh", "-c", script,
            "audit-maintenance-log", files, files + "/log", root,
            root + "/" + filename, timeout=3)

    def finished_binding(self, identity: dict, runtime: dict) -> None:
        session = validate_state(self.read("state.json"), self.adb.package)
        hello = validate_hello(self.read("hello.json"), self.adb.package)
        run = self.read("audit-state.json")
        fields(run, STATE_KEYS)
        if (session["phase"] != "active" or session["audit_logging"] is not True or session["lease_remaining_ms"] <= 0 or
                session["app_instance"] != hello["app_instance"] or session["challenge"] != hello["challenge"] or
                session["session_id"] != identity["session_id"] or hello["app_instance"] != identity["app_instance"] or
                {key: run[key] for key in IDENTITY_KEYS} != identity or run["phase"] != "idle" or
                type(run["version"]) is not int or run["version"] != 1 or run["audit_persist_supported"] is not True or
                integer(run["started_boottime_ms"]) > integer(run["observed_boottime_ms"]) or
                reconciliation.loaded_runtime(self.read("source-state.json"), self.read("reload-state.json"), identity) != runtime):
            raise AuditError("maintenance run/session/runtime did not finish on its original binding")

    def publish_acceptance(self, slot: str, acceptance: dict) -> None:
        if slot != "legacy":
            token(slot)
        request = acceptance["maintenance_request"]
        self.finished_binding(request["maintenance_identity"], request["runtime"])
        self.adb.verify()
        marker_root = self.adb.root.rsplit("/", 1)[0] + "/.audit-commit"
        directory = marker_root if slot == "legacy" else marker_root + "/commits/" + slot
        destination = directory + "/accepted.json"
        temporary = self.adb.root + "/.acceptance-" + request["request_id"] + ".tmp"
        # The destination is append-only. A partial exclusive write remains a
        # blocker; a retry can only compare identical bytes, never overwrite it.
        # Phone boundary independently fsyncs/readbacks it before any game input.
        script = ('trap \'rm -f "$1"\' 0; test ! -L "$1" && test ! -L "$2" && test ! -L "$3" && test ! -L "$4" && '
                  'test ! -L "$5" && cat > "$1" && '
                  'if test -e "$5"; then cmp -s "$1" "$5"; else (set -C; cat "$1" > "$5"); fi && cmp -s "$1" "$5"')
        self.adb._run("-s", self.adb.serial, "shell", "-T", "sh", "-c", shlex.quote(script), "audit-final-acceptance",
            shlex.quote(temporary), shlex.quote(marker_root), shlex.quote(marker_root + "/commits"),
            shlex.quote(directory), shlex.quote(destination), input=canonical(acceptance), timeout=3)

    def boottime_ms(self) -> int:
        self.adb.verify()
        started = time.monotonic()
        raw = self.adb._run("-s", self.adb.serial, "shell", "-T", "head", "-c", "128", "/proc/uptime", timeout=2)
        match = re.fullmatch(rb"(\d+)\.(\d{1,9})\s+\d+\.\d+\s*", raw)
        if match is None:
            raise AuditError("invalid phone boottime")
        milliseconds = int(match[1]) * 1000 + int((match[2] + b"000")[:3])
        # Phone value was sampled during this call. Adding the entire local
        # read duration is conservative; never subtract clocks from two hosts.
        return integer(milliseconds + int((time.monotonic() - started) * 1000 + 1))

    def write(self, message: dict, *, deadline: float | None = None) -> None:
        if message.get("operation") not in {"source_challenge", "persist_ack", "revoke"}:
            raise AuditError("unknown audit operation")
        raw = canonical(message)
        if len(raw) > MAX_BYTES:
            raise AuditError("audit request exceeds size bound")
        deadline = min(time.monotonic() + 3, deadline) if deadline is not None else time.monotonic() + 3
        # Reuse the existing bounded USB verification. It only checks target
        # identity/state; it neither publishes nor owns a heartbeat.
        timings = []
        self.adb._verify_heartbeat(deadline, timings)
        temporary = f"{self.adb.root}/.audit-request-{uuid.uuid4().hex}.tmp"
        destination = f"{self.adb.root}/audit-request.json"
        script = 'trap \'rm -f "$1"\' 0; test ! -L "$2" && cat > "$1" && mv "$1" "$2"'
        self.adb._heartbeat_run("audit_publish", deadline, timings, "-s", self.adb.serial,
                                "shell", "-T", "sh", "-c", shlex.quote(script),
                                "maa-audit", shlex.quote(temporary), shlex.quote(destination), input=raw)

    def current(self) -> dict:
        session = validate_state(self.read("state.json"), self.adb.package)
        hello = validate_hello(self.read("hello.json"), self.adb.package)
        if session["phase"] != "active" or session["audit_logging"] is not True or session["lease_remaining_ms"] <= 0:
            raise AuditError("an existing active audit session is required")
        if any(session[key] != hello[key] for key in ("app_instance", "challenge")):
            raise AuditError("session changed during audit read")
        return validate_run(self.read("audit-state.json"), session, self.adb.package)


@contextmanager
def case_lock(path: Path):
    """OS lock survives atomic case replacement and is released on process exit."""
    lock_path = path.with_name(path.name + ".lock")
    if path.is_symlink() or lock_path.is_symlink():
        raise AuditError("case/lock must not be symlinks")
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise AuditError("case record is already in use") from error
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def seal_case(body: dict) -> dict:
    return {**body, "checksum_sha256": digest(body)}


def read_case(path: Path) -> dict:
    if path.is_symlink():
        raise AuditError("case must not be a symlink")
    with path.open("rb") as handle:
        value = decode(handle.read(MAX_CASE_BYTES + 1), MAX_CASE_BYTES)
    fields(value, {"version", "case_id", "policy", "revision", "reservations", "checksum_sha256"})
    body = {key: item for key, item in value.items() if key != "checksum_sha256"}
    if value["checksum_sha256"] != digest(body):
        raise AuditError("case checksum mismatch; reconcile the existing record")
    if type(value["version"]) is not int or value["version"] != 1:
        raise AuditError("unknown case version")
    name(value["case_id"])
    policy = value["policy"]
    validate_intent({**policy, "quantity": 1})
    fields(policy, INTENT_KEYS - {"quantity"})
    if type(value["reservations"]) is not list or integer(value["revision"]) != len(value["reservations"]):
        raise AuditError("invalid case revision/reservations")
    seen = set()
    amount = quantity = 0
    for item in value["reservations"]:
        fields(item, {"status", "channel", "stream_id", "sequence", "source_nonce", "intent", "intent_sha256",
                      "source_boottime_ms", "host_received_at", "source_envelope"})
        if item["status"] != "pending_unconfirmed":
            raise AuditError("unknown reservation state; manual reconciliation required")
        validate_channel(item["channel"])
        token(item["stream_id"])
        integer(item["sequence"], 1)
        integer(item["source_boottime_ms"])
        token(item["source_nonce"])
        if item["source_nonce"] in seen:
            raise AuditError("duplicate persisted source nonce")
        seen.add(item["source_nonce"])
        intent = validate_intent(item["intent"])
        if {key: intent[key] for key in policy} != policy or digest(intent) != item["intent_sha256"]:
            raise AuditError("reservation policy/hash mismatch")
        if type(item["host_received_at"]) is not str or type(item["source_envelope"]) is not dict:
            raise AuditError("missing reservation evidence")
        amount += intent["unit_price"] * intent["quantity"]
        quantity += intent["quantity"]
    if amount > policy["budget_amount"] or quantity > policy["budget_quantity"]:
        raise AuditError("persisted reservations exceed budget")
    return value


def authorization_root() -> Path:
    # Fixed to this installed host tool, never selected by case path, attach,
    # environment or a caller's proposed cycle string. Tests inject this function.
    return Path(__file__).resolve().parents[1] / "debug" / "audit-purchase-authorizations"


def authorization_path(case_id: str, target: str) -> Path:
    root = authorization_root()
    for component in (root, *root.parents):
        if component.is_symlink():
            raise AuditError("authorization directory must not be linked")
    root.mkdir(parents=True, exist_ok=True)
    return root / (digest({"case_id": case_id, "target": target}) + ".json")


def create_case_file(path: Path, value: dict) -> None:
    # Exclusive creation never replaces a case; interrupted initialization is
    # intentionally corrupt and must be reconciled, not silently reset.
    with path.open("xb") as handle:
        handle.write(canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    if read_case(path) != value:
        raise AuditError("initial case read-back mismatch")
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def init_case(path: Path, case_id: str, policy: dict) -> None:
    name(case_id)
    fields(policy, INTENT_KEYS - {"quantity"})
    validate_intent({**policy, "quantity": 1})
    if path.exists():
        raise FileExistsError(path)
    value = seal_case({"version": 1, "case_id": case_id, "policy": policy, "revision": 0, "reservations": []})
    authority = authorization_path(case_id, policy["target"])
    with case_lock(authority):
        if authority.exists():
            original = read_case(authority)
            if original["case_id"] != case_id or original["policy"] != policy:
                raise AuditError("product authorization policy is frozen; no rollover/increase is supported")
        else:
            create_case_file(authority, value)
        create_case_file(path, value)


def authorized_case(path: Path) -> dict:
    value = read_case(path)
    authority = read_case(authorization_path(value["case_id"], value["policy"]["target"]))
    if authority["case_id"] != value["case_id"] or authority["policy"] != value["policy"]:
        raise AuditError("case differs from frozen product authorization")
    if any(item not in authority["reservations"] for item in value["reservations"]):
        raise AuditError("case reservation is absent from the durable product authorization")
    return value


def persist_case(path: Path, value: dict) -> None:
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_CASE_BYTES:
        raise AuditError("case evidence capacity exhausted")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
            if handle.read(MAX_CASE_BYTES + 1) != raw:
                raise AuditError("case read-back mismatch")
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def reserve(path: Path, channel: dict, envelope: dict, event: dict) -> None:
    value = authorized_case(path)
    intent = validate_intent(event["intent"])
    if event["case_id"] != value["case_id"] or {key: intent[key] for key in value["policy"]} != value["policy"]:
        raise AuditError("intent differs from initialized case policy")
    authority = authorization_path(value["case_id"], value["policy"]["target"])
    with case_lock(authority):
        # Reread under the product lock. Different case paths contend here too.
        value = authorized_case(path)
        allowance = read_case(authority)
        _reserve_locked(path, value, authority, allowance, channel, envelope, event, intent)


def _reserve_locked(path, value, authority, allowance, channel, envelope, event, intent):
    old = allowance["reservations"]
    if any(item["source_nonce"] == event["source_nonce"] for item in old):
        raise AuditError("source nonce already reserved; reconcile pending outcome")
    amount = sum(item["intent"]["unit_price"] * item["intent"]["quantity"] for item in old)
    quantity = sum(item["intent"]["quantity"] for item in old)
    if amount + intent["unit_price"] * intent["quantity"] > intent["budget_amount"] or quantity + intent["quantity"] > intent["budget_quantity"]:
        raise AuditError("pending reservations exhaust the cumulative budget")
    item = {"status": "pending_unconfirmed", "channel": channel, "stream_id": envelope["stream_id"],
            "sequence": envelope["sequence"], "source_nonce": event["source_nonce"], "intent": intent,
            "intent_sha256": event["intent_sha256"], "source_boottime_ms": event["source_boottime_ms"],
            "host_received_at": datetime.now(timezone.utc).isoformat(), "source_envelope": envelope}
    for destination, original in ((authority, allowance), (path, value)):
        body = {key: entry for key, entry in original.items() if key != "checksum_sha256"}
        body["reservations"] = [*original["reservations"], item]
        body["revision"] += 1
        # Authorization is the write-ahead reservation. A later case write or
        # ACK failure cannot refund it, even if the operator picks another file.
        persist_case(destination, seal_case(body))


def evidence_png(path: Path, expected: dict) -> None:
    """Bind actual PNG bytes/dimensions, not an arbitrary file's hash."""
    if path.is_symlink() or not path.is_file() or not 24 <= path.stat().st_size <= 32 * 1024 * 1024:
        raise AuditError("evidence must be a bounded regular PNG")
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or hashlib.sha256(raw).hexdigest() != expected["png_sha256"]:
        raise AuditError("evidence PNG signature/hash mismatch")
    position = 8
    chunks = []
    dimensions = None
    while position < len(raw):
        if position + 12 > len(raw):
            raise AuditError("truncated evidence PNG")
        length = int.from_bytes(raw[position:position + 4], "big")
        kind = raw[position + 4:position + 8]
        end = position + 12 + length
        if end > len(raw):
            raise AuditError("truncated evidence PNG chunk")
        data = raw[position + 8:position + 8 + length]
        crc = int.from_bytes(raw[position + 8 + length:end], "big")
        if zlib.crc32(kind + data) & 0xffffffff != crc:
            raise AuditError("evidence PNG checksum mismatch")
        if not chunks:
            if kind != b"IHDR" or len(data) != 13:
                raise AuditError("invalid evidence PNG header")
            dimensions = (int.from_bytes(data[:4], "big"), int.from_bytes(data[4:8], "big"))
        chunks.append(kind)
        position = end
        if kind == b"IEND":
            if length != 0 or position != len(raw):
                raise AuditError("invalid evidence PNG end")
            break
    if (not chunks or chunks[-1] != b"IEND" or b"IDAT" not in chunks or
            dimensions != (expected["width"], expected["height"])):
        raise AuditError("evidence PNG dimensions/content mismatch")


def checked_record_path(path: Path) -> None:
    for component in (path.absolute(), *path.absolute().parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise AuditError("record path is linked or redirected")


def sync_record_directory(path: Path) -> None:
    checked_record_path(path)
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise AuditError("record parent is not a directory")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_record_exclusive(path: Path, value: dict, *, maximum=MAX_CASE_BYTES, verify_existing=False) -> None:
    """Create once, or explicitly re-prove an identical existing record's durability.

    A readable file can be the residue of a failed final fsync. The retry path
    performs the same checked-descriptor fsync/readback and directory sync; it
    never edits, replaces or removes an existing record.
    """
    raw = canonical(value) + b"\n"
    if len(raw) > maximum:
        raise AuditError("record exceeds bound")
    checked_record_path(path)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    created = True
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not verify_existing:
            raise
        created = False
        descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r+b") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AuditError("record must be a regular file with a single link")
        if created:
            stream.write(raw)
            stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        if stream.read(maximum + 1) != raw:
            raise AuditError("record read-back mismatch")
        checked_record_path(path)
        current = path.stat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise AuditError("record changed during durability verification")
        sync_record_directory(path.parent)


def prepare_reconciliation(adapter, case_path, slot, effect_path, before_image, after_image, request_path, *, revalidate=False):
    """Explicit manual decision; no phone marker mutations or game inputs."""
    with case_lock(case_path):
        case = authorized_case(case_path)
        pending = decode(adapter.marker(slot, "pending.json"))
        claim = adapter.marker(slot, "pending.claim")
        reconciliation.validate_pending(pending)
        if case["case_id"] != pending["case_id"]:
            raise AuditError("case record belongs to another original purchase")
        reservations = [item for item in case["reservations"] if item["source_nonce"] == pending["source_nonce"]]
        if len(reservations) != 1:
            raise AuditError("original reservation not found exactly once")
        with effect_path.open("rb") as stream:
            effect = decode(stream.read(reconciliation.MAX_RECONCILE_BYTES + 1), reconciliation.MAX_RECONCILE_BYTES)
        reconciliation.validate_effect(effect, pending["intent"])
        evidence_png(before_image, effect["before"])
        evidence_png(after_image, effect["after"])
        current = adapter.current()
        identity = {key: current[key] for key in IDENTITY_KEYS}
        runtime = reconciliation.loaded_runtime(adapter.read("source-state.json"), adapter.read("reload-state.json"), identity)
        started = time.monotonic()
        upper = adapter.boottime_ms()
        # Derive a conservative lower-bound expiry just as source challenges do;
        # host monotonic is never directly compared with phone CLOCK_BOOTTIME.
        uncertainty = int((time.monotonic() - started) * 1000 + 1)
        request = dict(version=1, operation="reconcile_purchase", request_id=uuid.uuid4().hex,
            maintenance_identity=identity, runtime=runtime, pending_slot=slot,
            pending_sha256=reconciliation.digest(pending), claim_sha256=hashlib.sha256(claim).hexdigest(),
            case_record_sha256=digest(case), reservation=reservations[0], effect=effect,
            expires_boottime_ms=upper - uncertainty - SAFETY_MS + 30_000)
        if revalidate:
            prior = decode(adapter.marker(slot, "reconciled.json"), reconciliation.MAX_RECONCILE_BYTES)
            reconciliation.validate_receipt(prior, pending, claim)
            if (adapter.marker(slot, "reconcile.claim") != (prior["request"]["request_id"] + "\n").encode("ascii") or
                    decode(adapter.marker(slot, "reconciled.done")) != {"receipt_sha256": reconciliation.digest(prior)}):
                raise AuditError("original reconciliation witnesses are incomplete")
            request["operation"] = "revalidate_purchase"
            request["prior_receipt_sha256"] = reconciliation.digest(prior)
            reconciliation.validate_revalidation(request, prior, pending, claim)
        reconciliation.validate_request(request, pending, claim)
        # Persist the exact original case snapshot and evidence decision before
        # publication. This command never overwrites an earlier request record.
        record = dict(version=1, request=request, case_snapshot=case, pending=pending)
        write_record_exclusive(request_path, record)
        adapter.publish_reconciliation(request)
        return record


def finish_reconciliation(adapter, case_path, request_path, receipt_path, *, run_log_filename):
    """Read explicit Agent witnesses and append a host index; budget stays used."""
    with case_lock(case_path):
        case = authorized_case(case_path)
        with request_path.open("rb") as stream:
            record = decode(stream.read(MAX_CASE_BYTES + 1), MAX_CASE_BYTES)
        fields(record, {"version", "request", "case_snapshot", "pending"})
        if type(record["version"]) is not int or record["version"] != 1:
            raise AuditError("unknown reconciliation request-record version")
        request, pending = record["request"], record["pending"]
        reconciliation.validate_request(request, pending, reconciliation.CLAIM_BYTES)
        snapshot = record["case_snapshot"]
        fields(snapshot, {"version", "case_id", "policy", "revision", "reservations", "checksum_sha256"})
        if (type(snapshot["version"]) is not int or snapshot["version"] != 1 or
                type(snapshot["policy"]) is not dict or type(snapshot["reservations"]) is not list or
                type(snapshot["revision"]) is not int or snapshot["revision"] != len(snapshot["reservations"])):
            raise AuditError("invalid original case snapshot")
        if digest(snapshot) != request["case_record_sha256"] or snapshot["case_id"] != case["case_id"]:
            raise AuditError("original case snapshot/hash mismatch")
        if (case["case_id"] != pending["case_id"] or case["policy"] != snapshot["policy"] or
                sum(item == request["reservation"] for item in case["reservations"]) != 1 or
                sum(item == request["reservation"] for item in snapshot["reservations"]) != 1):
            raise AuditError("original reservation or policy changed")
        slot = request["pending_slot"]
        actual_pending = decode(adapter.marker(slot, "pending.json"))
        claim = adapter.marker(slot, "pending.claim")
        if actual_pending != pending:
            raise AuditError("phone pending changed after request")
        receipt = decode(adapter.marker(slot, "reconciled.json"), reconciliation.MAX_RECONCILE_BYTES)
        reconciliation.validate_receipt(receipt, pending, claim)
        if request["operation"] == "revalidate_purchase":
            reconciliation.validate_revalidation(request, receipt, pending, claim)
        elif receipt["request"] != request:
            raise AuditError("phone receipt belongs to another reconciliation")
        if (adapter.marker(slot, "reconcile.claim") != (receipt["request"]["request_id"] + "\n").encode("ascii") or
                decode(adapter.marker(slot, "reconciled.done")) != {"receipt_sha256": reconciliation.digest(receipt)}):
            raise AuditError("phone reconciliation completion witness missing")
        adapter.finished_binding(request["maintenance_identity"], request["runtime"])
        raw_log = adapter.maintenance_log(run_log_filename)
        if raw_log != adapter.maintenance_log(run_log_filename):
            raise AuditError("maintenance log changed during the terminal read")
        acceptance = reconciliation.acceptance_for(request, receipt, pending, claim,
                                                   run_log_filename, raw_log.decode("utf-8"))
        acceptance_path = receipt_path.with_name(receipt_path.name + ".acceptance.json")
        write_record_exclusive(acceptance_path, acceptance,
                               maximum=reconciliation.MAX_ACCEPTANCE_BYTES, verify_existing=True)
        # Only the actual post-return terminal/footer authorizes this final
        # artifact. Its presence, not a Python action's intermediate vote, is
        # the phone's durable permission to cross the old pending boundary.
        adapter.publish_acceptance(slot, acceptance)
        index_path = case_path.with_name(case_path.name + ".reconciliations.json")
        if index_path.is_symlink() or receipt_path.is_symlink():
            raise AuditError("host reconciliation output must not be linked")
        if index_path.exists():
            with index_path.open("rb") as stream:
                index = decode(stream.read(MAX_CASE_BYTES + 1), MAX_CASE_BYTES)
            fields(index, {"version", "case_id", "receipts", "checksum_sha256"})
            body = {key: item for key, item in index.items() if key != "checksum_sha256"}
            if (type(index["version"]) is not int or index["version"] != 1 or
                    index["case_id"] != case["case_id"] or type(index["receipts"]) is not list or
                    index["checksum_sha256"] != digest(body)):
                raise AuditError("invalid host reconciliation index")
        else:
            body = dict(version=1, case_id=case["case_id"], receipts=[])
        seen = set()
        for previous in body["receipts"]:
            fields(previous, {"source_nonce", "receipt", "pending"})
            token(previous["source_nonce"])
            if (previous["source_nonce"] in seen or type(previous["pending"]) is not dict or
                    previous["pending"].get("case_id") != case["case_id"]):
                raise AuditError("duplicate or cross-case host reconciliation")
            seen.add(previous["source_nonce"])
            reconciliation.validate_receipt(previous["receipt"], previous["pending"], reconciliation.CLAIM_BYTES)
            if previous["source_nonce"] != previous["pending"]["source_nonce"]:
                raise AuditError("host receipt/source nonce mismatch")
        existing = [item for item in body["receipts"] if item.get("source_nonce") == pending["source_nonce"]]
        entry = dict(source_nonce=pending["source_nonce"], receipt=receipt, pending=pending)
        if existing:
            if existing != [entry]:
                raise AuditError("host already holds another receipt for this nonce")
        else:
            body["receipts"] = [*body["receipts"], entry]
            persist_case(index_path, seal_case(body))
        if receipt_path.exists():
            with receipt_path.open("rb") as stream:
                if decode(stream.read(MAX_CASE_BYTES + 1), MAX_CASE_BYTES) != entry:
                    raise AuditError("receipt record already exists with another value")
        else:
            write_record_exclusive(receipt_path, entry)
        # Never mark a reservation refunded, remove it or lower its quantity.
        if read_case(case_path) != case:
            raise AuditError("case changed during reconciliation finalization")
        return entry


def parse_line(raw: bytes) -> dict | None:
    if len(raw) > MAX_BYTES:
        raise AuditError("logcat line exceeds bound")
    try:
        match = LOG_PREFIX.fullmatch(raw.decode("utf-8").rstrip("\r\n"))
    except UnicodeError as error:
        raise AuditError("invalid logcat UTF-8") from error
    if match is None:
        return None
    envelope = decode(match[1].encode("utf-8"))
    if envelope.get("event") != "maa_punish_audit":
        return None
    expected = {"event", "session_id", "stream_id", "sequence", "monotonic_ns", "level", "logger", "message"}
    if "exception" in envelope:
        expected.add("exception")
    fields(envelope, expected)
    token(envelope["session_id"])
    token(envelope["stream_id"])
    integer(envelope["sequence"], 1)
    if type(envelope["monotonic_ns"]) is not int or not 0 <= envelope["monotonic_ns"] < 2**63:
        raise AuditError("invalid source monotonic timestamp")
    for key in expected - {"sequence", "monotonic_ns"}:
        if type(envelope[key]) is not str:
            raise AuditError("invalid audit envelope text")
    return envelope


class Collector:
    def __init__(self, adapter: AuditUsb, case_path: Path, channel: dict, clock=time.monotonic):
        validate_channel(channel)
        self.adapter, self.case_path, self.channel, self.clock = adapter, case_path, channel, clock
        self.stream = None
        self.sequence = None
        self.seen_nonces: set[str] = set()
        self.challenge_expires_ms: int | None = None
        self.challenge_started: float | None = None

    def request(self, operation: str, **extra) -> dict:
        return {"version": 1, **self.channel, "request_id": uuid.uuid4().hex, "operation": operation, **extra}

    def check_current(self) -> dict:
        current = self.adapter.current()
        if any(current[key] != self.channel[key] for key in IDENTITY_KEYS):
            raise AuditError("active run changed; collector cannot transfer to another run")
        return current

    def fresh(self, source: int, received: float) -> float:
        integer(source)
        phone_now = self.adapter.boottime_ms()
        now = self.clock()
        if source > phone_now or phone_now - source + SAFETY_MS >= SOURCE_AGE_MS:
            raise AuditError("source event is old or from the future")
        if (now - received) * 1000 + SAFETY_MS >= SOURCE_AGE_MS:
            raise AuditError("local evidence processing exceeded its budget")
        if self.challenge_expires_ms is not None and phone_now + SAFETY_MS >= self.challenge_expires_ms:
            raise AuditError("collector challenge expired")
        if self.challenge_started is not None and (now - self.challenge_started) * 1000 + SAFETY_MS >= CHALLENGE_MS:
            raise AuditError("collector challenge exceeded its local budget")
        # This is only a conservative local deadline for the next USB calls.
        # The phone remains authoritative if an OS call runs beyond its timeout.
        remaining_ms = min(SOURCE_AGE_MS - (phone_now - source),
                           SOURCE_AGE_MS - (now - received) * 1000) - SAFETY_MS
        if self.challenge_expires_ms is not None:
            remaining_ms = min(remaining_ms, self.challenge_expires_ms - phone_now - SAFETY_MS)
        deadline = now + remaining_ms / 1000
        if self.challenge_started is not None:
            deadline = min(deadline, self.challenge_started + (CHALLENGE_MS - SAFETY_MS) / 1000)
        return deadline

    def begin(self) -> dict:
        self.check_current()
        self.challenge_started = self.clock()
        phone_upper = self.adapter.boottime_ms()
        # Freshness needs the conservative upper bound, but App challenge
        # admission requires expiry <= its actual now + CHALLENGE_MS. Remove
        # the entire lookup uncertainty (including USB checks) for this bound.
        uncertainty_ms = int((self.clock() - self.challenge_started) * 1000 + 1)
        phone_lower = max(0, phone_upper - uncertainty_ms)
        self.challenge_expires_ms = phone_lower + CHALLENGE_MS
        message = self.request("source_challenge", expires_boottime_ms=self.challenge_expires_ms)
        self.adapter.write(message)
        return message

    def accept(self, envelope: dict, received: float) -> dict | None:
        if envelope["session_id"] != self.channel["session_id"]:
            if self.stream is not None:
                raise AuditError("audit session changed in log stream")
            return None
        if self.stream is not None:
            if envelope["stream_id"] != self.stream or envelope["sequence"] != self.sequence + 1:
                raise AuditError("audit stream changed, repeated or has a sequence gap")
            self.sequence = envelope["sequence"]
        message = envelope["message"]
        if not message.startswith("{"):
            return None
        event = decode(message.encode("utf-8"))
        kind = event.get("audit_event")
        if kind not in {"source_anchor", "prepare_commit"}:
            return None
        expected = SOURCE_KEYS if kind == "source_anchor" else SOURCE_KEYS | {"case_id", "source_nonce", "intent", "intent_sha256"}
        fields(event, expected)
        matches = all(event[key] == self.channel[key] for key in ("execution_id", "run_nonce", "collector_id", "source_challenge"))
        if not matches:
            if self.stream is None:
                return None
            raise AuditError("source event belongs to a different run/challenge")
        current = self.check_current()
        if integer(event["source_boottime_ms"]) < current["started_boottime_ms"]:
            raise AuditError("source event predates the bound run")
        self.fresh(event["source_boottime_ms"], received)
        if kind == "source_anchor":
            if self.stream is not None:
                raise AuditError("source anchor may be established only once")
            self.stream, self.sequence = envelope["stream_id"], envelope["sequence"]
            return None
        if self.stream is None:
            raise AuditError("prepare_commit requires a fresh source anchor")
        token(event["source_nonce"])
        name(event["case_id"])
        validate_intent(event["intent"])
        if event["intent_sha256"] != digest(event["intent"]):
            raise AuditError("source intent hash mismatch")
        if event["source_nonce"] in self.seen_nonces:
            raise AuditError("source nonce already observed")
        self.seen_nonces.add(event["source_nonce"])
        reserve(self.case_path, self.channel, envelope, event)
        # No rollback after this point: an uncertain ACK or input stays charged.
        self.check_current()
        deadline = self.fresh(event["source_boottime_ms"], received)
        acknowledgement = self.request("persist_ack", stream_id=self.stream, sequence=self.sequence,
                                       source_nonce=event["source_nonce"], intent_sha256=event["intent_sha256"],
                                       source_boottime_ms=event["source_boottime_ms"],
                                       expires_boottime_ms=event["source_boottime_ms"] + SOURCE_AGE_MS)
        self.adapter.write(acknowledgement, deadline=deadline)
        return acknowledgement


class Logcat:
    """Bounded reader for one owned ADB process; never clears the shared buffer."""

    def __init__(self, adb: UsbAdb, popen=subprocess.Popen):
        adb.verify()
        self.process = popen([adb.adb, "-s", adb.serial, "logcat", "-v", "threadtime", "-T", "1", "MaaFw:I", "*:S"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.overflow = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True, name="maa-audit-logcat")
        self.reader.start()

    def _read(self):
        try:
            while True:
                raw = self.process.stdout.readline(MAX_BYTES + 1)
                self.messages.put_nowait((raw, time.monotonic()))
                if not raw or len(raw) > MAX_BYTES:
                    return
        except (OSError, ValueError, queue.Full):
            self.overflow.set()

    def next(self, timeout: float):
        if self.overflow.is_set():
            raise AuditError("logcat reader failed or evidence buffer overflowed")
        try:
            value = self.messages.get(timeout=timeout)
        except queue.Empty:
            return None
        if not value[0]:
            raise AuditError("owned logcat stream ended")
        return value

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=2)
        self.process.stdout.close()


def collect(adapter: AuditUsb, case_path: Path, logcat_factory=Logcat) -> None:
    with case_lock(case_path):
        authorized_case(case_path)  # Missing/corrupt authorization never opens a phone channel.
        current = adapter.current()
        channel = {key: current[key] for key in IDENTITY_KEYS}
        channel.update(collector_id=uuid.uuid4().hex, source_challenge=uuid.uuid4().hex)
        collector = Collector(adapter, case_path, channel)
        stream = logcat_factory(adapter.adb)
        started = False
        try:
            # Set before publication: a partial/uncertain write still revokes.
            started = True
            collector.begin()
            last_line = time.monotonic()
            last_status = last_line
            while True:
                item = stream.next(timeout=0.2)
                now = time.monotonic()
                if (now - collector.challenge_started) * 1000 + SAFETY_MS >= CHALLENGE_MS:
                    raise AuditError("collector challenge expired; start a new collector explicitly")
                if now - last_status >= 2:
                    collector.check_current()
                    last_status = time.monotonic()
                if now - last_line >= (30 if collector.stream is None else 10):
                    raise AuditError("source stream has not supplied fresh evidence")
                if item is None:
                    continue
                envelope = parse_line(item[0])
                if envelope is not None:
                    last_line = item[1]
                    ack = collector.accept(envelope, item[1])
                    if ack is not None:
                        print("persisted pending_unconfirmed; bound ACK published; game effect unconfirmed", flush=True)
        finally:
            try:
                if started:
                    adapter.write(collector.request("revoke"))
            except DebugError:
                pass  # Short phone permits are the fallback, never session close.
            finally:
                stream.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-case", help="explicitly create a new non-overwritable case budget")
    initialize.add_argument("--case-record", type=Path, required=True)
    initialize.add_argument("--case-id", required=True)
    for field in ("cycle-id", "target", "currency"):
        initialize.add_argument("--" + field, required=True)
    for field in ("unit-price", "budget-amount", "budget-quantity"):
        initialize.add_argument("--" + field, required=True, type=int)
    collecting = commands.add_parser("collect", help="consume the existing active audit run without owning its session")
    collecting.add_argument("--case-record", type=Path, required=True)
    collecting.add_argument("--serial", required=True)
    collecting.add_argument("--package", required=True)
    collecting.add_argument("--adb", default="adb")
    reconcile = commands.add_parser("prepare-reconciliation", help="publish an explicit annotated confirmed-consumed decision to a zero-input maintenance run")
    reconcile.add_argument("--case-record", type=Path, required=True)
    reconcile.add_argument("--pending-slot", default="legacy")
    reconcile.add_argument("--effect-record", type=Path, required=True)
    reconcile.add_argument("--before-image", type=Path, required=True)
    reconcile.add_argument("--after-image", type=Path, required=True)
    reconcile.add_argument("--request-record", type=Path, required=True)
    reconcile.add_argument("--revalidate", action="store_true", help="fresh current-run check of the exact original completed receipt after an interrupted maintenance run")
    finish = commands.add_parser("finish-reconciliation", help="verify Agent witnesses and archive confirmed consumption without refunding budget")
    finish.add_argument("--case-record", type=Path, required=True)
    finish.add_argument("--request-record", type=Path, required=True)
    finish.add_argument("--receipt-record", type=Path, required=True)
    finish.add_argument("--run-log", required=True, help="actual single-task App JSONL filename read from the device; local summaries are not accepted")
    for command in (reconcile, finish):
        command.add_argument("--serial", required=True)
        command.add_argument("--package", required=True)
        command.add_argument("--adb", default="adb")
    args = parser.parse_args(argv)
    try:
        if args.command == "init-case":
            policy = {key: getattr(args, key) for key in INTENT_KEYS - {"quantity"}}
            init_case(args.case_record, args.case_id, policy)
            print("created explicit case budget; no phone operation performed")
        elif args.command == "collect":
            collect(AuditUsb(UsbAdb(args.adb, args.serial, args.package)), args.case_record)
        elif args.command == "prepare-reconciliation":
            prepare_reconciliation(AuditUsb(UsbAdb(args.adb, args.serial, args.package)), args.case_record,
                args.pending_slot, args.effect_record, args.before_image, args.after_image, args.request_record,
                revalidate=args.revalidate)
            print("published explicit confirmed-consumed reconciliation; no phone marker/game input performed")
        else:
            finish_reconciliation(AuditUsb(UsbAdb(args.adb, args.serial, args.package)), args.case_record,
                                  args.request_record, args.receipt_record, run_log_filename=args.run_log)
            print("verified Agent reconciliation witnesses; original reservation and budget remain fully used")
    except KeyboardInterrupt:
        return 130
    except (DebugError, OSError, ValueError) as error:
        print(f"android_audit: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
