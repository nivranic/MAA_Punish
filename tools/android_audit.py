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
import subprocess
import sys
import threading
import time
import uuid

from android_debug import DebugError, TOKEN, UsbAdb, validate_hello, validate_state

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
        if filename in {"hello.json", "state.json"}:
            return self.adb.read(filename)
        if filename not in {"audit-state.json", "audit-permit.json"}:
            raise AuditError("audit mailbox is not readable")
        self.adb.verify()
        path = f"{self.adb.root}/{filename}"
        raw = self.adb._run("-s", self.adb.serial, "shell", "-T", "head", "-c",
                            str(MAX_BYTES + 1), shlex.quote(path), timeout=2)
        return decode(raw)

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


def init_case(path: Path, case_id: str, policy: dict) -> None:
    name(case_id)
    fields(policy, INTENT_KEYS - {"quantity"})
    validate_intent({**policy, "quantity": 1})
    value = seal_case({"version": 1, "case_id": case_id, "policy": policy, "revision": 0, "reservations": []})
    # Exclusive creation never replaces a case; interrupted initialization is
    # intentionally corrupt and must be reconciled, not silently reset.
    with path.open("xb") as handle:
        handle.write(canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


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
    value = read_case(path)
    intent = validate_intent(event["intent"])
    if event["case_id"] != value["case_id"] or {key: intent[key] for key in value["policy"]} != value["policy"]:
        raise AuditError("intent differs from initialized case policy")
    old = value["reservations"]
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
    body = {key: item for key, item in value.items() if key != "checksum_sha256"}
    body["reservations"] = [*old, item]
    body["revision"] += 1
    persist_case(path, seal_case(body))


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
        read_case(case_path)  # Missing/corrupt records never open a phone channel.
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
    args = parser.parse_args(argv)
    try:
        if args.command == "init-case":
            policy = {key: getattr(args, key) for key in INTENT_KEYS - {"quantity"}}
            init_case(args.case_record, args.case_id, policy)
            print("created explicit case budget; no phone operation performed")
        else:
            collect(AuditUsb(UsbAdb(args.adb, args.serial, args.package)), args.case_record)
    except KeyboardInterrupt:
        return 130
    except (DebugError, OSError, ValueError) as error:
        print(f"android_audit: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
