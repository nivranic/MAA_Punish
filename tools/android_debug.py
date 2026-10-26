#!/usr/bin/env python3
"""USB-only MAA development session. Python 3.12, standard library only.

The phone must contain the matching Android debug-session overlay. Session and
status do not change PI. Explicit sync stages allowed source and asks the phone
to stop, replace, and reload it; no command installs an APK or starts a task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

PROTOCOL = 1
MAX_MESSAGE_BYTES = 16_384
HEARTBEAT_SECONDS = 2.0
HEARTBEAT_BUDGET_SECONDS = 5.0
# The phone owns the actual lease. This host bound starts before publication,
# not at ACK receipt; the reserve is conservative headroom, not an OS guarantee.
PHONE_LEASE_SECONDS = 8.0
LEASE_SAFETY_SECONDS = 0.5
# Fixed shell program only: JSON arrives on stdin, and all positional paths are
# derived from the validated package and this invocation's random temporary name.
HEARTBEAT_SCRIPT = (
    'trap \'rm -f "$1"\' 0; '
    'cat > "$1" && mv "$1" "$2" && sleep 0.3 && head -c 16385 "$3"'
)
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z")
SERIAL = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = REPO_ROOT / ".github/android/overlays/app/src/main/assets/dev-source-policy.json"


class DebugError(RuntimeError):
    """A bounded operation failed; no fallback target or transport is selected."""


class MailboxUnavailable(DebugError):
    """The matching phone process has not published a mailbox yet."""


def decode_object(raw: bytes, max_bytes: int = MAX_MESSAGE_BYTES) -> dict:
    if len(raw) > max_bytes:
        raise DebugError(f"mailbox message exceeds {max_bytes} bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise DebugError("invalid mailbox JSON") from error
    if not isinstance(value, dict):
        raise DebugError("mailbox message must be a JSON object")
    return value


class UsbAdb:
    """Re-check -d before every read/write; all actual operations pin -s serial.

    A host_id is only a session label. Trust comes from the already authorized
    USB ADB transport, not from this identifier or Android's ADB_ENABLED setting.
    See https://developer.android.com/tools/adb#directingcommands
    """

    def __init__(self, adb: str, serial: str, package: str, run: Callable = subprocess.run):
        if not SERIAL.fullmatch(serial) or serial.startswith("emulator-"):
            raise DebugError("--serial must identify a physical USB device, not wireless/emulator ADB")
        if not PACKAGE.fullmatch(package):
            raise DebugError("invalid Android package")
        self.adb = adb
        self.serial = serial
        self.package = package
        self.root = f"/sdcard/Android/data/{package}/files/.dev-session"
        self.run = run

    def _run(self, *arguments: str, timeout: float = 5, input: bytes | None = None) -> bytes:
        try:
            options = {"input": input} if input is not None else {}
            result = self.run(
                [self.adb, *arguments], capture_output=True, timeout=timeout, check=False,
                **options,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DebugError(f"ADB invocation failed: {type(error).__name__}") from error
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
            raise DebugError(f"ADB failed: {detail or result.returncode}")
        return result.stdout

    def verify(self) -> None:
        # -d rejects zero/multiple USB transports and does not select TCP ADB.
        detected = self._run("-d", "get-serialno").decode("utf-8").strip()
        if detected != self.serial:
            raise DebugError("USB transport differs from --serial; refusing operation")
        if self._run("-s", self.serial, "get-state").strip() != b"device":
            raise DebugError("USB device is not authorized and online")

    def _file(self, name: str) -> str:
        if name not in {"hello.json", "state.json", "request.json", "close.json",
                        "reload-request.json", "reload-state.json", "reload-ack.json", "source-state.json"}:
            raise DebugError("unknown mailbox file")
        return f"{self.root}/{name}"

    def read(self, name: str) -> dict:
        self.verify()
        max_bytes = 1_048_576 if name == "source-state.json" else MAX_MESSAGE_BYTES
        # A fixed, quoted path and bounded read; never run caller-supplied shell.
        try:
            raw = self._run(
                # shell v2 preserves the remote failure code/stderr. exec-out
                # can return exit 0 with a missing-file error in stdout.
                "-s", self.serial, "shell", "-T", "head", "-c", str(max_bytes + 1),
                shlex.quote(self._file(name)),
            )
        except DebugError as error:
            raise MailboxUnavailable("phone mailbox unavailable; open the matching MAA build") from error
        return decode_object(raw, max_bytes)

    def write(self, message: dict, destination: str | None = None, *,
              on_publish: Callable[[float], None] | None = None) -> None:
        self.verify()
        raw = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(raw) > MAX_MESSAGE_BYTES:
            raise DebugError("request exceeds 16 KiB")
        destination = destination or ("close.json" if message.get("operation") == "close" else "request.json")
        if destination not in {"request.json", "close.json", "reload-request.json"}:
            raise DebugError("mailbox is not host writable")
        remote_temp = f"{self.root}/.{destination}-{uuid.uuid4().hex}.tmp"
        # Do not mkdir here: only a running phone coordinator creates the mailbox.
        with tempfile.TemporaryDirectory(prefix="maa-usb-request-") as directory:
            local = Path(directory) / "request.json"
            local.write_bytes(raw)
            try:
                self._run("-s", self.serial, "push", str(local), remote_temp)
                # Recheck after upload as well; no promotion after losing USB binding.
                self.verify()
                if on_publish is not None:
                    on_publish(time.monotonic())
                self._run(
                    "-s", self.serial, "shell", "mv", shlex.quote(remote_temp),
                    shlex.quote(self._file(destination)),
                )
            finally:
                # Only this invocation's random temp file, never a tree or user PI.
                try:
                    self.verify()
                    self._run("-s", self.serial, "shell", "rm", "-f", shlex.quote(remote_temp))
                except DebugError:
                    pass

    def _heartbeat_run(self, stage: str, deadline: float, timings: list[tuple[str, float]],
                       *arguments: str, input: bytes | None = None) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DebugError("phone did not acknowledge heartbeat within its operation budget")
        started = time.monotonic()
        try:
            result = self._run(*arguments, timeout=min(5, remaining), input=input)
            # Process creation/timeout cleanup can overrun on Windows. Never
            # accept a late result or launch the next operation after that.
            if time.monotonic() >= deadline:
                raise DebugError("phone did not acknowledge heartbeat within its operation budget")
            return result
        finally:
            timings.append((stage, time.monotonic() - started))

    def _verify_heartbeat(self, deadline: float, timings: list[tuple[str, float]]) -> None:
        # Keep the same USB and authorization checks as every other operation.
        detected = self._heartbeat_run("usb_serial", deadline, timings,
                                       "-d", "get-serialno").decode("utf-8").strip()
        if detected != self.serial:
            raise DebugError("USB transport differs from --serial; refusing operation")
        online = self._heartbeat_run("usb_state", deadline, timings,
                                     "-s", self.serial, "get-state").strip()
        if online != b"device":
            raise DebugError("USB device is not authorized and online")

    def exchange_heartbeat(self, message: dict, deadline: float,
                           timings: list[tuple[str, float]], *,
                           on_publish: Callable[[float], None] | None = None) -> dict:
        """Publish one heartbeat atomically and observe status in one USB shell.

        This finite transaction cannot write close/reload mailboxes. A separate
        bounded status read is needed if the phone has not acknowledged it yet.
        """
        if message.get("operation") != "heartbeat":
            raise DebugError("heartbeat exchange only accepts heartbeat requests")
        raw = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(raw) > MAX_MESSAGE_BYTES:
            raise DebugError("request exceeds 16 KiB")
        self._verify_heartbeat(deadline, timings)
        remote_temp = f"{self.root}/.request.json-{uuid.uuid4().hex}.tmp"
        if on_publish is not None:
            on_publish(time.monotonic())
        raw_state = self._heartbeat_run(
            "mailbox_exchange", deadline, timings,
            "-s", self.serial, "shell", "-T", "sh", "-c", shlex.quote(HEARTBEAT_SCRIPT),
            "maa-heartbeat", shlex.quote(remote_temp), shlex.quote(self._file("request.json")),
            shlex.quote(self._file("state.json")), input=raw,
        )
        return decode_object(raw_state)

    def read_heartbeat_state(self, deadline: float, timings: list[tuple[str, float]]) -> dict:
        self._verify_heartbeat(deadline, timings)
        raw = self._heartbeat_run(
            "ack_read", deadline, timings, "-s", self.serial, "shell", "-T", "head", "-c",
            str(MAX_MESSAGE_BYTES + 1), shlex.quote(self._file("state.json")),
        )
        return decode_object(raw)

    def apk_version(self) -> int:
        self.verify()
        raw = self._run("-s", self.serial, "shell", "-T", "cmd", "package", "list", "packages",
                        "--show-versioncode", self.package).decode("utf-8", errors="strict")
        pattern = re.compile(r"package:" + re.escape(self.package) + r" versionCode:(\d+)")
        versions = [int(match.group(1)) for line in raw.splitlines() if (match := pattern.fullmatch(line.strip()))]
        if len(versions) != 1 or versions[0] < 1:
            raise DebugError("cannot identify exactly one installed target APK version")
        return versions[0]

    def pi_version(self) -> int:
        self.verify()
        # The one allowed read outside the session mailbox is PI's installation
        # marker, never a caller-selected file or the user configuration tree.
        marker = self.root.rsplit("/", 1)[0] + "/pi.version"
        raw = self._run("-s", self.serial, "shell", "-T", "head", "-c", "33", shlex.quote(marker))
        if len(raw) > 32 or not re.fullmatch(rb"[0-9]+\s*", raw):
            raise DebugError("invalid installed PI version marker")
        return int(raw.strip())

    def upload_stage(self, snapshot: Path, session_id: str, operation_id: str) -> None:
        if not TOKEN.fullmatch(session_id) or not TOKEN.fullmatch(operation_id):
            raise DebugError("invalid stage identity")
        self.verify()
        operation_root = f"{self.root}/reload/{session_id}/{operation_id}"
        # Fixed script and positional, quoted paths only. Every existing parent
        # must be a real directory; never stage through a symlink into live PI.
        script = 'for p do if [ -L "$p" ]; then exit 9; fi; done; mkdir -p "$3" && mkdir "$4"'
        parents = [self.root, f"{self.root}/reload", f"{self.root}/reload/{session_id}", operation_root]
        self._run("-s", self.serial, "shell", "-T", "sh", "-c", shlex.quote(script), "maa-stage",
                  *(shlex.quote(path) for path in parents))
        self.verify()
        # Only our immutable delta snapshot is copied, never an entire PI tree.
        self._run("-s", self.serial, "push", str(snapshot / "stage"), operation_root, timeout=300)
        self.verify()
        self._run("-s", self.serial, "push", str(snapshot / "manifest.json"), operation_root + "/manifest.tmp")
        self.verify()
        self._run("-s", self.serial, "shell", "mv", shlex.quote(operation_root + "/manifest.tmp"),
                  shlex.quote(operation_root + "/manifest.json"))


def validate_hello(value: dict, package: str) -> dict:
    required = {"version", "package", "app_instance", "challenge", "max_message_bytes", "audit_logging_supported"}
    if set(value) != required or type(value["version"]) is not int or value["version"] != PROTOCOL:
        raise DebugError("incompatible phone session protocol")
    if value["package"] != package or value["max_message_bytes"] != MAX_MESSAGE_BYTES:
        raise DebugError("phone package/protocol limits mismatch")
    if value["audit_logging_supported"] is not True:
        raise DebugError("phone does not support the audit logging session contract")
    for key in ("app_instance", "challenge"):
        if not isinstance(value[key], str) or not TOKEN.fullmatch(value[key]):
            raise DebugError(f"invalid phone {key}")
    return value


def validate_state(value: dict, package: str) -> dict:
    required = {
        "version", "package", "app_instance", "challenge", "phase", "session_id", "host_id",
        "last_sequence", "lease_remaining_ms", "request_id", "result", "reason",
        "screen_saver_status",
        "audit_logging",
    }
    if set(value) != required or type(value["version"]) is not int or value["version"] != PROTOCOL:
        raise DebugError("incompatible phone status protocol")
    if value["package"] != package:
        raise DebugError("status package mismatch")
    if type(value["audit_logging"]) is not bool:
        raise DebugError("invalid audit logging status")
    for key in ("app_instance", "challenge"):
        if not isinstance(value[key], str) or not TOKEN.fullmatch(value[key]):
            raise DebugError(f"invalid status {key}")
    for key in ("session_id", "host_id", "request_id"):
        if value[key] != "" and (not isinstance(value[key], str) or not TOKEN.fullmatch(value[key])):
            raise DebugError(f"invalid status {key}")
    if not isinstance(value["phase"], str) or value["phase"] not in {"idle", "active", "closed", "expired"}:
        raise DebugError("invalid session phase")
    if not isinstance(value["result"], str) or value["result"] not in {"", "accepted", "rejected"} or \
            not isinstance(value["reason"], str) or not isinstance(value["screen_saver_status"], str):
        raise DebugError("invalid session result")
    if any(type(value[key]) is not int or value[key] < 0 for key in ("last_sequence", "lease_remaining_ms")):
        raise DebugError("invalid session sequence/lease")
    if value["phase"] == "active" and (not value["session_id"] or not value["host_id"]):
        raise DebugError("active session has no owner")
    if value["phase"] != "active" and value["audit_logging"]:
        raise DebugError("inactive session cannot enable audit logging")
    return value


class Session:
    def __init__(self, adb: UsbAdb, hello: dict, *, host_id: str | None = None,
                 session_id: str | None = None, audit_logging: bool = False):
        self.adb = adb
        self.hello = validate_hello(hello, adb.package)
        self.host_id = host_id or uuid.uuid4().hex
        self.session_id = session_id or uuid.uuid4().hex
        if type(audit_logging) is not bool:
            raise DebugError("audit logging selection must be boolean")
        self.audit_logging = audit_logging
        self.sequence = 0
        self.heartbeat_timings: list[tuple[str, float]] = []
        self.last_confirmed_publication_started: float | None = None
        self.publication_started: float | None = None
        self.heartbeat_deadline: float | None = None
        self.heartbeat_lease_deadline: float | None = None

    def _record_publication(self, started: float) -> None:
        # A candidate is not evidence of renewal until its exact ACK is accepted.
        if self.publication_started is None:
            self.publication_started = started

    def send(self, operation: str, *, wait_seconds: float | None = None) -> dict:
        if operation not in {"open", "heartbeat", "close"}:
            raise DebugError("operation is not supported by this protocol")
        self.sequence += 1
        request_id = uuid.uuid4().hex
        message = {
            "version": PROTOCOL, "package": self.adb.package,
            "app_instance": self.hello["app_instance"], "challenge": self.hello["challenge"],
            "host_id": self.host_id, "session_id": self.session_id,
            "request_id": request_id, "sequence": 0 if operation == "close" else self.sequence,
            "operation": operation,
            "audit_logging": self.audit_logging,
        }
        self.publication_started = None
        if operation == "heartbeat":
            return self._heartbeat(message, HEARTBEAT_BUDGET_SECONDS if wait_seconds is None else wait_seconds)
        self.last_confirmed_publication_started = None
        if operation == "open":
            self.adb.write(message, on_publish=self._record_publication)
            if self.publication_started is None:
                raise DebugError("open publication start is unknown; refusing heartbeat renewal")
        else:
            self.adb.write(message)
        deadline = time.monotonic() + (3 if wait_seconds is None else wait_seconds)
        if operation == "open":
            deadline = min(deadline, self.publication_started + PHONE_LEASE_SECONDS - LEASE_SAFETY_SECONDS)
            if time.monotonic() >= deadline:
                raise DebugError("open publication exceeded its conservative lease budget")
        while time.monotonic() < deadline:
            state = validate_state(self.adb.read("state.json"), self.adb.package)
            # Close has its own fixed mailbox and sequence space. A concurrent
            # heartbeat may replace the general acknowledgement after closure;
            # observing this session's challenge retired also proves it ended.
            if operation == "close" and state["app_instance"] == self.hello["app_instance"] and \
                    state["challenge"] != self.hello["challenge"]:
                return state
            if state["request_id"] == request_id:
                if state["result"] != "accepted":
                    raise DebugError(f"phone rejected {operation}: {state['reason']}")
                if operation == "open" and (
                    any(state[key] != message[key] for key in ("app_instance", "challenge", "session_id", "host_id")) or
                    state["phase"] != "active" or state["last_sequence"] != message["sequence"] or
                    state["audit_logging"] != self.audit_logging
                ):
                    raise DebugError("open acknowledgement audit mode or binding mismatch")
                if operation == "open":
                    if time.monotonic() >= deadline:
                        raise DebugError("open acknowledgement arrived after its operation budget")
                    self.last_confirmed_publication_started = self.publication_started
                return state
            time.sleep(0.1)
        if operation == "open" and time.monotonic() >= \
                self.publication_started + PHONE_LEASE_SECONDS - LEASE_SAFETY_SECONDS:
            raise DebugError("open publication exceeded its conservative lease budget")
        raise DebugError(f"phone did not acknowledge {operation}")

    def _heartbeat(self, message: dict, wait_seconds: float) -> dict:
        self.heartbeat_timings = []
        self.heartbeat_deadline = None
        self.heartbeat_lease_deadline = None
        previous_publication = self.last_confirmed_publication_started
        # Any failed/unconfirmed attempt ends the renewable session. The
        # independent close mailbox remains available for best-effort cleanup.
        self.last_confirmed_publication_started = None
        if previous_publication is None:
            raise DebugError("confirmed publication start is unknown; refusing heartbeat renewal")
        self.heartbeat_lease_deadline = previous_publication + PHONE_LEASE_SECONDS - LEASE_SAFETY_SECONDS
        deadline = min(time.monotonic() + min(wait_seconds, HEARTBEAT_BUDGET_SECONDS),
                       self.heartbeat_lease_deadline)
        self.heartbeat_deadline = deadline
        if time.monotonic() >= deadline:
            raise DebugError("phone did not acknowledge heartbeat within its operation budget")
        state = self.adb.exchange_heartbeat(message, deadline, self.heartbeat_timings,
                                            on_publish=self._record_publication)
        while True:
            state = validate_state(state, self.adb.package)
            if state["app_instance"] != self.hello["app_instance"] or \
                    state["challenge"] != self.hello["challenge"] or \
                    state["session_id"] != self.session_id or state["host_id"] != self.host_id or \
                    state["phase"] != "active":
                raise DebugError(f"phone session ended during heartbeat: {state['reason'] or state['phase']}")
            if state["audit_logging"] != self.audit_logging:
                raise DebugError("heartbeat acknowledgement audit mode mismatch")
            if state["result"] == "rejected":
                raise DebugError(f"phone rejected heartbeat: {state['reason']}")
            if state["request_id"] == message["request_id"]:
                if state["result"] != "accepted":
                    raise DebugError(f"phone rejected heartbeat: {state['reason']}")
                if state["last_sequence"] != message["sequence"]:
                    raise DebugError("heartbeat acknowledgement sequence mismatch")
                if time.monotonic() >= deadline:
                    raise DebugError("phone did not acknowledge heartbeat within its operation budget")
                if self.publication_started is None:
                    raise DebugError("heartbeat publication start is unknown; refusing heartbeat renewal")
                self.last_confirmed_publication_started = self.publication_started
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DebugError("phone did not acknowledge heartbeat within its operation budget")
            time.sleep(min(0.1, remaining))
            state = self.adb.read_heartbeat_state(deadline, self.heartbeat_timings)


def run_session(adb: UsbAdb, wait_seconds: float, *, audit_logging: bool = False) -> None:
    deadline = time.monotonic() + wait_seconds
    print("Waiting for MAA to open and publish its USB session challenge…", flush=True)
    session = None
    opened = False
    try:
        while not opened:
            try:
                hello = validate_hello(adb.read("hello.json"), adb.package)
                session = Session(adb, hello, audit_logging=audit_logging)
                session.send("open")
                opened = True
            except DebugError as error:
                # A dead process may leave a well-formed old hello on disk.
                # Retry stale/missing mailboxes only before the first handshake.
                retryable = isinstance(error, MailboxUnavailable) or any(
                    text in str(error) for text in ("did not acknowledge open", "stale_challenge" )
                )
                if not retryable:
                    raise
                if time.monotonic() >= deadline:
                    raise DebugError("no phone coordinator; open a build containing the USB session feature") from error
                time.sleep(0.5)
        print(f"USB session active: {session.session_id}; audit_logging={session.audit_logging}; "
              "Ctrl+C to end. No PI changes. Audit mode applies to newly spawned agents only.", flush=True)
        last_ack = time.monotonic()
        next_heartbeat = last_ack + HEARTBEAT_SECONDS
        while True:
            remaining = next_heartbeat - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            started = time.monotonic()
            schedule_lag = max(0.0, started - next_heartbeat)
            state = None
            # Losing/closing a session ends this command. It never silently opens a new one.
            try:
                state = session.send("heartbeat")
            finally:
                observed = time.monotonic()
                stages = ",".join(f"{name}:{elapsed * 1000:.0f}" for name, elapsed in session.heartbeat_timings)
                budget_remaining = (f"{(session.heartbeat_deadline - observed) * 1000:.0f}"
                                    if session.heartbeat_deadline is not None else "unknown")
                lease_deadline = (f"{session.heartbeat_lease_deadline:.3f}"
                                  if session.heartbeat_lease_deadline is not None else "unknown")
                publication_started = (f"{session.publication_started:.3f}"
                                       if session.publication_started is not None else "none")
                print(f"Heartbeat wall_time={time.strftime('%Y-%m-%dT%H:%M:%S%z')} "
                      f"seq={session.sequence} result={'accepted' if state else 'failed'} "
                      f"round_end_monotonic_s={observed:.3f} "
                      f"ack_observed_monotonic_s={f'{observed:.3f}' if state else 'none'} "
                      f"schedule_lag_ms={schedule_lag * 1000:.0f} "
                      f"elapsed_ms={(observed - started) * 1000:.0f} "
                      f"since_last_ack_ms={(observed - last_ack) * 1000:.0f} "
                      f"lease_snapshot_ms={state['lease_remaining_ms'] if state else 'unknown'} "
                      f"budget_remaining_ms={budget_remaining} "
                      f"lease_bound_monotonic_s={lease_deadline} "
                      f"publication_started_monotonic_s={publication_started} "
                      f"adb_stages_ms=[{stages}]", flush=True)
            last_ack = observed
            next_heartbeat += HEARTBEAT_SECONDS
            # Skip missed ticks; a slow round must neither add another complete
            # interval nor cause an immediate burst of catch-up heartbeats.
            if next_heartbeat <= observed:
                missed = int((observed - next_heartbeat) // HEARTBEAT_SECONDS) + 1
                next_heartbeat += missed * HEARTBEAT_SECONDS
    finally:
        if opened and session is not None:
            try:
                session.send("close", wait_seconds=1)
            except DebugError:
                # The phone's monotonic 8-second lease also handles unplug/kill.
                pass


def close_session(adb: UsbAdb) -> dict:
    state = validate_state(adb.read("state.json"), adb.package)
    if state["phase"] != "active":
        return state
    hello = validate_hello(adb.read("hello.json"), adb.package)
    if hello["app_instance"] != state["app_instance"] or hello["challenge"] != state["challenge"]:
        raise DebugError("phone session changed while closing; retry")
    session = Session(adb, hello, host_id=state["host_id"], session_id=state["session_id"],
                      audit_logging=state["audit_logging"])
    session.sequence = state["last_sequence"]
    return session.send("close")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(131_072), b""):
            digest.update(block)
    return digest.hexdigest()


class SourcePolicy:
    """One finite policy asset consumed by both the PC and Android code."""

    def __init__(self, spec: dict | None = None):
        self.spec = spec if spec is not None else json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        if self.spec.get("version") != 1:
            raise DebugError("unsupported source policy version")
        self.limits = self.spec["limits"]

    def safe_path(self, path: str) -> bool:
        if not isinstance(path, str) or not path or len(path) > 512 or any(
            ord(char) < 32 or ord(char) > 0xFFFF or ord(char) == 127 or char in '\\:<>"|?*'
            for char in path
        ):
            return False
        parts = path.split("/")
        return all(part and part not in {".", ".."} and not part.startswith(".") and
                   not part.endswith((".", " ")) for part in parts)

    def traversable(self, path: str) -> bool:
        if not self.safe_path(path):
            return False
        parts = path.split("/")
        if any(part.lower() in self.spec["excluded_segments"] for part in parts):
            return False
        return not any((path.lower() + "/").startswith(prefix.lower()) for prefix in self.spec["excluded_prefixes"])

    def allowed(self, path: str) -> bool:
        if not self.traversable(path):
            return False
        if path in self.spec["files"]:
            return True
        parts = path.split("/")
        return len(parts) > 1 and Path(parts[-1]).suffix.lower() in self.spec["roots"].get(parts[0], [])

    def entries(self, values: object) -> list[dict]:
        if not isinstance(values, list) or len(values) > self.limits["max_files"]:
            raise DebugError("invalid source file count")
        result = []
        seen = set()
        total = 0
        for value in values:
            if not isinstance(value, dict) or set(value) != {"path", "size", "sha256"}:
                raise DebugError("invalid source file metadata")
            path, size, digest = value["path"], value["size"], value["sha256"]
            if not self.allowed(path) or type(size) is not int or not 0 <= size <= self.limits["max_file_bytes"] or \
                    not isinstance(digest, str) or not HASH.fullmatch(digest):
                raise DebugError("source path/size/hash is outside policy")
            if path.lower() in seen:
                raise DebugError("source path has a case collision or duplicate")
            seen.add(path.lower())
            total += size
            result.append({"path": path, "size": size, "sha256": digest})
        if total > self.limits["max_total_bytes"]:
            raise DebugError("source payload exceeds policy limit")
        return sorted(result, key=lambda entry: entry["path"])

    def collect(self, source_root: Path) -> list[dict]:
        root = source_root.resolve(strict=True)
        files = []
        candidates = [root / name for name in self.spec["files"]]
        for name in self.spec["roots"]:
            directory = root / name
            if directory.is_symlink() or directory.is_junction():
                raise DebugError(f"source root is a symlink: {name}")
            if not directory.exists():
                continue
            for current, directories, names in os.walk(directory, followlinks=False):
                relative = Path(current).relative_to(root)
                kept = []
                for child in directories:
                    child_path = relative.joinpath(child).as_posix()
                    if self.traversable(child_path):
                        if Path(current, child).is_symlink() or Path(current, child).is_junction():
                            raise DebugError(f"source directory is a symlink: {child_path}")
                        kept.append(child)
                directories[:] = kept
                candidates.extend(Path(current, child) for child in names)
        collected_paths = set()
        for path in candidates:
            relative = path.relative_to(root).as_posix()
            # Exact files can also be discovered under a traversed root. Only
            # deduplicate these internal candidates; entries() must still reject
            # external duplicates and case collisions.
            if relative in collected_paths:
                continue
            collected_paths.add(relative)
            if not self.allowed(relative):
                continue
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise DebugError(f"source is not a regular file: {relative}")
            size = path.stat().st_size
            if size > self.limits["max_file_bytes"]:
                raise DebugError(f"source exceeds per-file limit: {relative}")
            files.append({"path": relative, "size": size, "sha256": sha256_file(path)})
        return self.entries(files)


def source_revision(files: list[dict]) -> str:
    return hashlib.sha256(canonical_json(files)).hexdigest()


def source_baseline(value: dict, policy: SourcePolicy, package: str, apk_version: int) -> dict:
    expected = {"version", "package", "apk_version_code", "revision", "files"}
    if set(value) != expected or type(value["version"]) is not int or value["version"] != 1 or value["package"] != package or \
            type(value["apk_version_code"]) is not int or value["apk_version_code"] != apk_version:
        raise DebugError("source baseline belongs to another APK/package; establish a fresh session")
    entries = policy.entries(value["files"])
    if value["revision"] != source_revision(entries):
        raise DebugError("source baseline revision/hash mismatch")
    return {**value, "files": entries}


def build_manifest(policy: SourcePolicy, package: str, apk_version: int, session_id: str,
                   operation_id: str, files: list[dict], baseline: dict | None) -> dict:
    current = policy.entries(files)
    old = {entry["path"]: entry for entry in baseline["files"]} if baseline else {}
    new = {entry["path"]: entry for entry in current}
    delta = [{**entry, "delete": False} for path, entry in new.items() if old.get(path) != entry]
    delta.extend({**entry, "delete": True} for path, entry in old.items() if path not in new)
    manifest = {
        "version": 1, "package": package, "apk_version_code": apk_version,
        "session_id": session_id, "operation_id": operation_id,
        "base_revision": baseline["revision"] if baseline else "",
        "revision": source_revision(current), "files": sorted(delta, key=lambda entry: entry["path"]),
        "managed_files": current,
    }
    if len(canonical_json(manifest)) > policy.limits["max_manifest_bytes"]:
        raise DebugError("source manifest exceeds policy limit")
    return manifest


def snapshot_source(root: Path, directory: Path, manifest: dict) -> None:
    stage = directory / "stage"
    stage.mkdir()
    for entry in manifest["files"]:
        if entry["delete"]:
            continue
        source = root / entry["path"]
        if source.is_symlink() or source.is_junction() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise DebugError("source changed to a non-regular file during snapshot")
        target = stage / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as stream:
            data = stream.read(entry["size"] + 1)
        target.write_bytes(data)
        if target.stat().st_size != entry["size"] or sha256_file(target) != entry["sha256"]:
            raise DebugError("source changed during snapshot; retry sync")
    (directory / "manifest.json").write_bytes(canonical_json(manifest))


def active_binding(adb: UsbAdb) -> tuple[dict, dict]:
    status = validate_state(adb.read("state.json"), adb.package)
    hello = validate_hello(adb.read("hello.json"), adb.package)
    if status["phase"] != "active" or any(status[key] != hello[key] for key in ("app_instance", "challenge")):
        raise DebugError("no active matching USB session; keep the session command running")
    return hello, status


def sync_source(adb: UsbAdb, root: Path, wait_seconds: float = 120) -> dict:
    policy = SourcePolicy()
    hello, session = active_binding(adb)
    apk_version = adb.apk_version()
    try:
        raw_baseline = adb.read("source-state.json")
        if type(raw_baseline.get("version")) is int and raw_baseline.get("version") == 1 and \
                raw_baseline.get("package") == adb.package and \
                type(raw_baseline.get("apk_version_code")) is int and \
                0 < raw_baseline["apk_version_code"] < apk_version:
            if adb.pi_version() != apk_version:
                raise DebugError("APK upgraded but its PI is not fully installed; open MAA and retry")
            baseline = None
        else:
            baseline = source_baseline(raw_baseline, policy, adb.package, apk_version)
    except MailboxUnavailable:
        baseline = None
    operation_id = uuid.uuid4().hex
    manifest = build_manifest(policy, adb.package, apk_version, session["session_id"], operation_id,
                              policy.collect(root), baseline)
    request = {
        "version": 1, "package": adb.package, "app_instance": hello["app_instance"],
        "challenge": hello["challenge"], "host_id": session["host_id"], "session_id": session["session_id"],
        "request_id": uuid.uuid4().hex, "operation": "apply", "operation_id": operation_id,
        "revision": manifest["revision"], "manifest_sha256": hashlib.sha256(canonical_json(manifest)).hexdigest(),
    }
    print(f"Staging {sum(not item['delete'] for item in manifest['files'])} changed file(s); "
          f"revision {manifest['revision'][:12]}. No task will be started.", flush=True)
    with tempfile.TemporaryDirectory(prefix="maa-source-stage-") as directory:
        snapshot = Path(directory)
        snapshot_source(root.resolve(), snapshot, manifest)
        adb.upload_stage(snapshot, session["session_id"], operation_id)
    _, still_active = active_binding(adb)
    if still_active["session_id"] != session["session_id"] or still_active["app_instance"] != hello["app_instance"]:
        raise DebugError("USB session changed during upload; staged source was not applied")
    adb.write(request, "reload-request.json")
    try:
        return wait_for_reload(adb, policy, manifest, request, apk_version, wait_seconds)
    except (DebugError, KeyboardInterrupt):
        # Cancellation is advisory: after promotion the phone must finish its
        # own commit/rollback even if this terminal or the USB connection dies.
        try:
            adb.write({**request, "operation": "cancel", "request_id": uuid.uuid4().hex}, "reload-request.json")
        except DebugError:
            pass
        raise


def wait_for_reload(adb: UsbAdb, policy: SourcePolicy, manifest: dict, request: dict,
                    apk_version: int, wait_seconds: float) -> dict:
    operation_id = request["operation_id"]
    deadline = time.monotonic() + wait_seconds
    last_phase = ""
    while time.monotonic() < deadline:
        try:
            value = adb.read("reload-state.json")
        except MailboxUnavailable:
            value = {}
        if value.get("operation_id") != operation_id:
            try:
                ack = adb.read("reload-ack.json")
            except MailboxUnavailable:
                ack = {}
            if ack.get("operation_id") == operation_id and ack.get("session_id") == request["session_id"] and \
                    ack.get("phase") == "rejected":
                raise DebugError(f"phone rejected reload: {ack.get('reason', '')}")
            time.sleep(0.25)
            continue
        if type(value.get("version")) is not int or value.get("version") != 1 or value.get("session_id") != request["session_id"]:
            raise DebugError("reload result does not match the submitted operation")
        phase = value.get("phase")
        if not isinstance(phase, str):
            raise DebugError("invalid reload phase")
        # Failed maintenance reports the restored baseline even when promotion
        # never began. Preserve its reason without relaxing any success check.
        if value.get("revision") != manifest["revision"] and not (
                phase == "failed" and value.get("revision") == manifest["base_revision"]):
            raise DebugError("reload result does not match the submitted operation")
        if phase != last_phase:
            print(f"Phone reload: {phase}", flush=True)
            last_phase = phase
        if phase == "loaded":
            # Stage/copy/project parsing alone is never a successful sync.
            if value.get("resource_loaded") is not True or value.get("agent_loaded") is not True or \
                    type(value.get("native_generation")) is not int or value["native_generation"] <= 0 or \
                    type(value.get("agent_generation")) is not int or value["agent_generation"] <= 0:
                raise DebugError("phone reported loaded without resource/Agent preparation evidence")
            if value.get("controller_reused") is not True or type(value.get("display_id")) is not int or value["display_id"] < 0:
                raise DebugError("phone reported loaded without preserving the Controller/display")
            committed = source_baseline(adb.read("source-state.json"), policy, adb.package, apk_version)
            if committed["revision"] != manifest["revision"]:
                raise DebugError("phone committed revision differs from loaded revision")
            return value
        if phase in {"failed", "cancelled", "blocked", "rolled_back", "rejected"}:
            raise DebugError(f"phone reload {phase}: {value.get('reason', '')}")
        time.sleep(0.25)
    raise DebugError("reload timed out; phone may still be converging safely; inspect status before retrying")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True, help="ADB executable path")
    parser.add_argument("--serial", required=True, help="explicit physical USB serial; checked against adb -d")
    parser.add_argument("--package", required=True, help="installed target application ID")
    commands = parser.add_subparsers(dest="command", required=True)
    session_parser = commands.add_parser("session", help="hold a temporary USB session until Ctrl+C")
    session_parser.add_argument("--wait-seconds", type=float, default=60)
    session_parser.add_argument("--audit-logging", action="store_true",
                                help="explicit stdout-only audit logging for agents spawned under this lease")
    status_parser = commands.add_parser("status", help="read phone session status without renewing")
    status_parser.add_argument("--reload", action="store_true", help="read last maintenance status")
    commands.add_parser("close", help="close the current session; the running helper then exits")
    sync_parser = commands.add_parser("sync", help="stage allowed source, stop and reload; keep session running separately")
    sync_parser.add_argument("--source", type=Path, default=REPO_ROOT, help="repository source root")
    sync_parser.add_argument("--wait-seconds", type=float, default=120, help="phone maintenance timeout, excluding upload")
    args = parser.parse_args(argv)
    try:
        adb = UsbAdb(args.adb, args.serial, args.package)
        if args.command == "session":
            if not 0 < args.wait_seconds <= 3600:
                raise DebugError("--wait-seconds must be between 0 and 3600")
            run_session(adb, args.wait_seconds, audit_logging=args.audit_logging)
        elif args.command == "sync":
            if not 0 < args.wait_seconds <= 3600:
                raise DebugError("--wait-seconds must be between 0 and 3600")
            print(json.dumps(sync_source(adb, args.source, args.wait_seconds), ensure_ascii=False, indent=2))
        else:
            state = close_session(adb) if args.command == "close" else (
                adb.read("reload-state.json") if args.reload else validate_state(adb.read("state.json"), adb.package)
            )
            print(json.dumps(state, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("Command interrupted; the phone expires its lease or completes any active commit/rollback.")
    except (DebugError, OSError) as error:
        print(f"android_debug: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
