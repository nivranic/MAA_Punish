"""D12 persist-before-input boundary. No OCR guesses and no automatic settlement.

Wire protocol: docs/plans/2026-10-23-iqoo16-persist-ack.md.
The named observation producer is deliberately not implemented: until real game
images establish its recognizer, production audit purchases fail closed.
Dorm purchases have no observation/intent contract and cannot reuse D12's.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from uuid import uuid4

from audit_config import AUDIT_CONFIG

MAX_BYTES = 16_384
MAX_INTEGER = 2**53 - 1
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
PACKAGE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\Z")
IDENTITY_KEYS = {"package", "app_instance", "session_id", "execution_id", "run_nonce"}
INTENT_KEYS = {"cycle_id", "target", "currency", "unit_price", "quantity", "budget_amount", "budget_quantity"}
BASE_KEYS = IDENTITY_KEYS | {"version", "collector_id", "source_challenge", "request_id", "operation"}
PERMIT_KEYS = {"phase", "observed_boottime_ms", "permit_expires_boottime_ms"}
STATE_KEYS = IDENTITY_KEYS | {"version", "phase", "started_boottime_ms", "observed_boottime_ms", "audit_persist_supported"}
ACK_KEYS = {"stream_id", "sequence", "source_nonce", "intent_sha256", "source_boottime_ms", "expires_boottime_ms"}
OBSERVATION_NODE = "识别D12购买意图"
OBSERVATION_ACTION = "D12PurchaseObservation"
_BOUNDARY_LOCK = threading.Lock()


class CommitDenied(RuntimeError):
    """Missing or conflicting current evidence; no permission to input."""


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def fields(value, expected):
    if type(value) is not dict or set(value) != expected:
        raise CommitDenied("unexpected_fields")


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise CommitDenied("invalid_integer")
    return value


def matching(value, pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        raise CommitDenied("invalid_identifier")
    return value


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise CommitDenied("duplicate_json_key")
            value[key] = item
        return value
    if len(raw) > MAX_BYTES:
        raise CommitDenied("file_too_large")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CommitDenied("nonfinite_json")))
    except (ValueError, UnicodeError) as error:
        raise CommitDenied("invalid_json") from error
    if type(value) is not dict:
        raise CommitDenied("invalid_json_object")
    return value


def boottime_ms():
    # CLOCK_MONOTONIC omits suspend time on Android, so it is not a substitute.
    if not hasattr(time, "CLOCK_BOOTTIME"):
        raise CommitDenied("clock_boottime_unavailable")
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1_000_000


@dataclass(frozen=True)
class RuntimePaths:
    marker_root: Path
    files_root: Path | None = None
    package: str | None = None


def runtime_paths():
    source = Path(__file__).resolve()
    agent_root = source.parent
    project_root = agent_root.parent
    files_root = project_root.parent
    # This is derived from installed code, never a caller-selected attach path.
    if (agent_root.name == "agent" and project_root.name in {"pi", "PI"} and
            files_root.name == "files" and files_root.parent.parent.name == "data" and
            files_root.parent.parent.parent.name == "Android"):
        package = matching(files_root.parent.name, PACKAGE)
        return RuntimePaths(files_root / ".audit-commit", files_root, package)
    if sys.platform == "android" or "ANDROID_ROOT" in os.environ:
        raise CommitDenied("unrecognized_android_install_root")
    return RuntimePaths(project_root / "debug" / "audit-commit")


def safe_path(path: Path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise CommitDenied("linked_runtime_path")
    if path.absolute() != path.resolve():
        raise CommitDenied("redirected_runtime_path")


def read_object(path: Path):
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CommitDenied("non_regular_mailbox_file")
        return decode(stream.read(MAX_BYTES + 1))


class PendingStore:
    """One sticky local reservation, shared across modes, sessions and restarts.

    The exclusive claim is itself a permanent blocker if writing or syncing the
    atomic JSON fails. Neither successful Controller work nor a timeout deletes it.
    """

    def __init__(self, root):
        self.root = root

    @contextmanager
    def boundary(self):
        if not _BOUNDARY_LOCK.acquire(blocking=False):
            raise CommitDenied("purchase_boundary_busy")
        stream = None
        locked = False
        try:
            safe_path(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            safe_path(self.root)
            # Android external storage can expose the App's FUSE owner even to
            # a privileged native child. Compare the installed runtime namespace,
            # not the child's UID, which need not match that mapping.
            trusted = self.root.parent.stat()
            actual = self.root.stat()
            if actual.st_uid != trusted.st_uid or actual.st_dev != trusted.st_dev:
                raise CommitDenied("marker_owner_mismatch")
            lock_path = self.root / "boundary.lock"
            safe_path(lock_path)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            stream = os.fdopen(descriptor, "r+b")
            lock_stat = os.fstat(stream.fileno())
            if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != trusted.st_uid or
                    lock_stat.st_dev != trusted.st_dev):
                raise CommitDenied("marker_lock_owner_mismatch")
            if os.name == "nt":
                import msvcrt
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            # Any unexplained content is a blocker, including corrupt/incomplete
            # markers. Never infer that a previous input did not happen.
            if any(path.name != "boundary.lock" for path in self.root.iterdir()):
                raise CommitDenied("pending_unconfirmed")
            yield
        finally:
            if stream is not None:
                if locked and os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                stream.close()
            _BOUNDARY_LOCK.release()

    def reserve(self, value):
        claim = self.root / "pending.claim"
        with claim.open("xb") as stream:
            stream.write(b"pending_unconfirmed\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary = self.root / (".pending-" + uuid4().hex + ".tmp")
        with temporary.open("xb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        destination = self.root / "pending.json"
        os.replace(temporary, destination)
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())
        if os.name != "nt":
            descriptor = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if read_object(destination) != value:
            raise CommitDenied("marker_readback_mismatch")


def validate_binding(value, config, paths):
    fields(value, IDENTITY_KEYS | {"enabled", "mailbox_root"})
    if value["enabled"] is not True or not config.enabled or value["session_id"] != config.session_id:
        raise CommitDenied("audit_session_mismatch")
    for key in ("app_instance", "session_id", "run_nonce"):
        matching(value[key], TOKEN)
    matching(value["execution_id"], NAME)
    matching(value["package"], PACKAGE)
    if paths.files_root is None or value["package"] != paths.package:
        raise CommitDenied("audit_install_root_mismatch")
    root = Path(value["mailbox_root"]) if type(value["mailbox_root"]) is str else None
    if root is None or not root.is_absolute() or root.name != ".dev-session":
        raise CommitDenied("invalid_mailbox_root")
    # Android's /sdcard alias is legitimate, but arbitrary roots are not.
    if root.resolve() != paths.files_root / ".dev-session":
        raise CommitDenied("mailbox_root_mismatch")
    return {key: value[key] for key in IDENTITY_KEYS}, root.resolve()


def validate_intent(value):
    fields(value, INTENT_KEYS)
    for key in ("cycle_id", "target", "currency"):
        matching(value[key], NAME)
    for key in ("unit_price", "budget_amount", "budget_quantity"):
        integer(value[key])
    integer(value["quantity"], 1)
    amount = integer(value["unit_price"] * value["quantity"])
    if amount > value["budget_amount"] or value["quantity"] > value["budget_quantity"]:
        raise CommitDenied("intent_exceeds_budget")
    return value


def observed_intent(context, argv, attached, identity, now):
    """Only a current, named native And child can supply observed purchase data.

    A future calibrated Custom recognizer must produce this detail from its input
    frame. There is currently no production node/producer, hence no purchase.
    """
    from maa.define import AndRecognitionResult, CustomRecognitionResult

    fields(attached, {"case_id", "intent"})
    if attached["case_id"] != "D12":
        raise CommitDenied("wrong_purchase_case")
    intent = validate_intent(attached["intent"])
    parent = argv.reco_detail
    if (parent.name != argv.node_name or parent.hit is not True or str(parent.algorithm) != "And" or
            not isinstance(parent.best_result, AndRecognitionResult)):
        raise CommitDenied("observation_not_ready")
    integer(parent.reco_id, 1)
    matches = [sub for sub in parent.best_result.sub_results if sub.name == OBSERVATION_NODE]
    if len(matches) != 1:
        raise CommitDenied("observation_not_ready")
    child = matches[0]
    integer(child.reco_id, 1)
    if (child.reco_id == parent.reco_id or child.hit is not True or str(child.algorithm) != "Custom" or
            not isinstance(child.best_result, CustomRecognitionResult)):
        raise CommitDenied("invalid_observation_child")
    # Check the native parent detail really links this child's recognition ID.
    if not isinstance(parent.raw_detail, list) or sum(
            type(item) is dict and item.get("reco_id") == child.reco_id for item in parent.raw_detail) != 1:
        raise CommitDenied("unbound_observation_child")
    definition = context.get_node_data(OBSERVATION_NODE)
    recognition = (definition or {}).get("recognition", {})
    if (recognition.get("type") != "Custom" or
            recognition.get("param", {}).get("custom_recognition") != OBSERVATION_ACTION):
        raise CommitDenied("untrusted_observation_producer")
    detail = child.best_result.detail
    fields(detail, IDENTITY_KEYS | {"observed_boottime_ms", "frame_sha256", "target", "currency", "unit_price", "quantity"})
    if any(detail[key] != value for key, value in identity.items()):
        raise CommitDenied("observation_run_mismatch")
    observed = integer(detail["observed_boottime_ms"])
    if not 0 <= now - observed < 5_000:
        raise CommitDenied("stale_observation")
    matching(detail["frame_sha256"], HASH)
    for key in ("target", "currency"):
        matching(detail[key], NAME)
    integer(detail["unit_price"])
    integer(detail["quantity"], 1)
    if any(detail[key] != intent[key] for key in ("target", "currency", "unit_price", "quantity")):
        raise CommitDenied("observed_intent_mismatch")
    # Raw images are supplied only in framework debug mode. Do not enable that
    # mode or save screenshots here. When present, independently cross-check.
    for recognition_detail in (parent, child):
        frame = getattr(recognition_detail, "raw_image", None)
        if frame is not None and getattr(frame, "size", 0):
            if hashlib.sha256(frame.tobytes()).hexdigest() != detail["frame_sha256"]:
                raise CommitDenied("observation_frame_mismatch")
    return intent, detail


class PurchaseCommit:
    def __init__(self, logger, *, config=None, paths=None, clock=None, sleep=None):
        self.logger = logger
        self.config = AUDIT_CONFIG if config is None else config
        self.paths = paths
        self.clock = clock or boottime_ms
        self.sleep = sleep or time.sleep

    @staticmethod
    def stopped(context):
        if context.tasker.stopping:
            raise CommitDenied("task_stopping")

    def state(self, root, identity):
        value = read_object(root / "audit-state.json")
        fields(value, STATE_KEYS)
        if (type(value["version"]) is not int or value["version"] != 1 or
                value["phase"] != "running" or value["audit_persist_supported"] is not True or
                any(value[key] != item for key, item in identity.items())):
            raise CommitDenied("audit_run_not_current")
        start = integer(value["started_boottime_ms"])
        observed = integer(value["observed_boottime_ms"])
        if not start <= observed <= self.clock():
            raise CommitDenied("invalid_run_clock")
        return value

    def permit(self, root, identity):
        value = read_object(root / "audit-permit.json")
        operation = value.get("operation")
        extra = ({"expires_boottime_ms"} if operation == "source_challenge" else
                 ACK_KEYS if operation == "persist_ack" else set() if operation == "revoke" else None)
        if extra is None:
            raise CommitDenied("unknown_permit_operation")
        fields(value, BASE_KEYS | PERMIT_KEYS | extra)
        if (type(value["version"]) is not int or value["version"] != 1 or
                any(value[key] != item for key, item in identity.items())):
            raise CommitDenied("permit_run_mismatch")
        for key in ("collector_id", "source_challenge", "request_id"):
            matching(value[key], TOKEN)
        integer(value["observed_boottime_ms"])
        integer(value["permit_expires_boottime_ms"])
        if operation != "revoke":
            integer(value["expires_boottime_ms"])
        if operation == "persist_ack":
            for key in ("stream_id", "source_nonce"):
                matching(value[key], TOKEN)
            matching(value["intent_sha256"], HASH)
            integer(value["sequence"], 1)
            integer(value["source_boottime_ms"])
        return value

    def wait_challenge(self, context, root, identity):
        deadline = self.clock() + 5_000
        while True:
            self.stopped(context)
            state = self.state(root, identity)
            now = self.clock()
            if now >= deadline:
                raise CommitDenied("challenge_timeout")
            try:
                value = self.permit(root, identity)
            except FileNotFoundError:
                self.sleep(0.025)
                continue
            if value["phase"] == "revoked" and value["operation"] == "revoke":
                self.sleep(0.025)
                continue
            if (value["phase"] != "challenge" or value["operation"] != "source_challenge" or
                    not state["started_boottime_ms"] <= value["observed_boottime_ms"] <= now or
                    not now < value["permit_expires_boottime_ms"] == value["expires_boottime_ms"] <= now + 30_000):
                raise CommitDenied("invalid_challenge")
            return value

    def check_ack(self, value, challenge, expected, now):
        if any(value.get(key) != item for key, item in expected.items()):
            raise CommitDenied("permit_commit_mismatch")
        if any(value.get(key) != challenge[key] for key in ("collector_id", "source_challenge")):
            raise CommitDenied("permit_channel_mismatch")
        observed = value["observed_boottime_ms"]
        expiry = value["permit_expires_boottime_ms"]
        if (value["phase"] != "permitted" or value["operation"] != "persist_ack" or
                not expected["source_boottime_ms"] <= observed <= now or
                not now < expiry <= min(expected["expires_boottime_ms"], observed + 2_000,
                                         challenge["expires_boottime_ms"])):
            raise CommitDenied("permit_expired_or_invalid")

    def execute(self, context, argv, node, click):
        self.stopped(context)
        paths = self.paths or runtime_paths()
        store = PendingStore(paths.marker_root)
        with store.boundary():
            attach = node.get("attach", {})
            if type(attach) is not dict:
                raise CommitDenied("invalid_attach")
            binding = attach.get("audit_run")
            if not self.config.enabled:
                if binding is not None:
                    fields(binding, {"enabled"})
                    if binding["enabled"] is not False:
                        raise CommitDenied("unexpected_audit_run_in_normal_mode")
                self.stopped(context)
                return click()
            # D13/D14 share the normal native click and persistent pending
            # boundary, but their observations must never masquerade as D12.
            if argv.node_name != "购买":
                raise CommitDenied("purchase_observation_not_implemented")
            identity, root = validate_binding(binding, self.config, paths)
            # Validate the explicit plan and real recognition evidence before any
            # challenge/prepare. The plan alone is never an observation.
            intent, observation = observed_intent(context, argv, attach.get("audit_intent"), identity, self.clock())
            challenge = self.wait_challenge(context, root, identity)
            observed_intent(context, argv, attach.get("audit_intent"), identity, self.clock())
            common = {key: identity[key] for key in ("execution_id", "run_nonce")}
            common.update({key: challenge[key] for key in ("collector_id", "source_challenge")})
            self.logger.emit_audit(dict(common, audit_event="source_anchor", source_boottime_ms=self.clock()))
            source = self.clock()
            nonce = uuid4().hex
            event = dict(common, audit_event="prepare_commit", source_boottime_ms=source,
                         case_id="D12", source_nonce=nonce, intent=intent, intent_sha256=digest(intent))
            receipt = self.logger.emit_audit(event)
            expected = dict(stream_id=receipt.stream_id, sequence=receipt.sequence, source_nonce=nonce,
                            intent_sha256=event["intent_sha256"], source_boottime_ms=source,
                            expires_boottime_ms=source + 5_000)
            deadline = min(source + 5_000, challenge["expires_boottime_ms"])
            while True:
                self.stopped(context)
                self.state(root, identity)
                now = self.clock()
                if now >= deadline:
                    raise CommitDenied("persist_ack_timeout")
                permit = self.permit(root, identity)
                if permit == challenge:
                    self.sleep(0.025)
                    continue
                self.check_ack(permit, challenge, expected, now)
                break
            self.stopped(context)
            # Sticky local marker precedes input, and survives every failure from
            # here on. Host pending has independently been durably reserved.
            store.reserve(dict({**identity, **common, **expected}, version=1,
                               state="pending_unconfirmed", case_id="D12", intent=intent,
                               observation=observation, requested=True,
                               controller_completed=None, effect="unconfirmed"))
            # Filesystem latency, lease loss, mode/run changes and stop can all
            # invalidate a previously valid permit while the marker is syncing.
            self.stopped(context)
            latest = context.get_node_data(argv.node_name)
            if latest != node:
                raise CommitDenied("purchase_node_changed")
            self.state(root, identity)
            self.check_ack(self.permit(root, identity), challenge, expected, self.clock())
            observed_intent(context, argv, attach.get("audit_intent"), identity, self.clock())
            self.stopped(context)
            self.logger.emit_audit(dict(common, audit_event="purchase_input", source_nonce=nonce,
                                        requested=True, controller_completed=None, effect="unconfirmed"))
            # Emitting the request may block on stdout. Check the permit once more
            # after it, with no subsequent logging/wait before the native action.
            self.stopped(context)
            self.state(root, identity)
            if context.get_node_data(argv.node_name) != node:
                raise CommitDenied("purchase_node_changed")
            if not 0 <= self.clock() - observation["observed_boottime_ms"] < 5_000:
                raise CommitDenied("stale_observation")
            self.check_ack(self.permit(root, identity), challenge, expected, self.clock())
            self.stopped(context)
            completed = click()
            self.logger.emit_audit(dict(common, audit_event="purchase_input", source_nonce=nonce,
                                        requested=True, controller_completed=completed, effect="unconfirmed"))
            return completed
