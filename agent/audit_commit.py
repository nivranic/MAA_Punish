"""Purchase persist-before-input boundaries. No guesses or automatic settlement.

Wire protocol: docs/plans/2026-10-23-iqoo16-persist-ack.md.
Observation calibration must come from real confirmation pages. Uncalibrated
cases fail closed; dorm purchases have separate producers and submit points.
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
import purchase_reconciliation_contract as reconciliation

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
# Persisted prepared bytes cannot prove that the writer survived its final I/O
# and current-run checks. These votes permit same-run idempotent maintenance
# only. Purchase input requires the separate post-return accepted.json artifact.
_RECONCILIATION_ACCEPTED = {}
_RECONCILIATION_FAILED = set()


@dataclass(frozen=True)
class PurchaseCase:
    action_node: str
    observation_node: str
    observation_action: str


# D13 and D14 share the real final button, but have different same-frame product
# producers selected by their actual task option. D14's earlier click is Max.
PURCHASE_CASES = {
    "D12": PurchaseCase("购买", OBSERVATION_NODE, OBSERVATION_ACTION),
    "D13": PurchaseCase("点击购买_宿舍", "识别D13购买意图", "D13PurchaseObservation"),
    "D14": PurchaseCase("点击购买_宿舍", "识别D14购买意图", "D14PurchaseObservation"),
}


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


def decode(raw, maximum=MAX_BYTES):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise CommitDenied("duplicate_json_key")
            value[key] = item
        return value
    if len(raw) > maximum:
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


def read_object(path: Path, maximum=MAX_BYTES):
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CommitDenied("non_regular_mailbox_file")
        return decode(stream.read(maximum + 1), maximum)


def _storage_writer_identity(parent):
    """Credentials belong to this Python writer, not the App-owned files dir."""
    if os.name == "nt":
        return None  # Windows keeps its existing namespace/locking contract.
    if any(not callable(getattr(os, key, None)) for key in
           ("getuid", "geteuid", "getgid", "getegid", "getpid")):
        raise CommitDenied("storage_writer_identity_unavailable")
    uid, euid, gid, egid = os.getuid(), os.geteuid(), os.getgid(), os.getegid()
    uid_columns = gid_columns = None
    # Embedded Android Python can report sys.platform == "linux". The installed
    # code-derived files namespace, rather than an attach or env guess, selects
    # the extra fsuid check even when ANDROID_ROOT is absent.
    if runtime_paths().files_root == parent:
        credentials = {}
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            key, separator, value = line.partition(":")
            if separator and key in {"Uid", "Gid"}:
                if key in credentials:
                    raise CommitDenied("storage_writer_status_invalid")
                credentials[key] = tuple(int(part) for part in value.split())
        uid_columns, gid_columns = credentials.get("Uid"), credentials.get("Gid")
        if (uid_columns is None or gid_columns is None or len(uid_columns) != 4 or len(gid_columns) != 4 or
                uid_columns[:2] != (uid, euid) or gid_columns[:2] != (gid, egid) or uid_columns[3] != euid):
            raise CommitDenied("storage_writer_fsuid_mismatch")
    return os.getpid(), uid, euid, gid, egid, uid_columns, gid_columns


class DirectoryIdentityGuard:
    """One parent anchor and one exact writer UID; no per-object UID fallback."""

    @staticmethod
    def _anchor(info):
        return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode

    @classmethod
    def capture(cls, parent):
        safe_path(parent)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise CommitDenied("storage_parent_not_directory")
        guard = cls()
        guard.parent = parent
        guard.anchor = cls._anchor(info)
        guard.writer = _storage_writer_identity(parent)
        guard.owner_uid = info.st_uid if guard.writer is None else guard.writer[2]
        guard.objects = {}
        guard.check_parent()
        return guard

    def check_parent(self):
        if _storage_writer_identity(self.parent) != self.writer:
            raise CommitDenied("storage_writer_identity_changed")
        safe_path(self.parent)
        info = self.parent.lstat()
        if self._anchor(info) != self.anchor:
            raise CommitDenied("storage_parent_identity_changed")
        return info

    def _check_child(self, path, info, is_directory):
        valid_type = stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode)
        if not valid_type or info.st_uid != self.owner_uid or info.st_dev != self.anchor[0]:
            raise CommitDenied("storage_object_owner_or_filesystem_mismatch")
        # A setgid files parent gives new objects its storage GID (1078 on the
        # observed device), not the Python egid. Pin the actual GID with inode;
        # never substitute it for the UID authorization check.
        identity = self._anchor(info)
        if path in self.objects and self.objects[path] != identity:
            raise CommitDenied("storage_object_identity_changed")
        self.objects[path] = identity
        return info

    def check_directory(self, path):
        parent = self.check_parent()
        if path == self.parent:
            return parent
        safe_path(path)
        if not path.is_relative_to(self.parent):
            raise CommitDenied("storage_path_outside_parent")
        return self._check_child(path, path.lstat(), True)

    def check_file(self, path, descriptor=None):
        self.check_parent()
        safe_path(path)
        if not path.is_relative_to(self.parent):
            raise CommitDenied("storage_path_outside_parent")
        actual = path.lstat()
        if descriptor is not None:
            opened = os.fstat(descriptor)
            if self._anchor(opened) != self._anchor(actual):
                raise CommitDenied("storage_file_identity_changed")
            actual = opened
        return self._check_child(path, actual, False)


class PendingStore:
    """One sticky local reservation, shared across modes, sessions and restarts.

    The exclusive claim is itself a permanent blocker if writing or syncing the
    atomic JSON fails. Neither successful Controller work nor a timeout deletes it.
    """

    def __init__(self, root):
        self.root = root
        self._directory_identity = None
        self._lock_descriptor = None

    def _check_identity(self):
        if self._directory_identity is None:
            raise CommitDenied("purchase_boundary_not_active")
        self._directory_identity.check_directory(self.root)
        if self._lock_descriptor is not None:
            self._directory_identity.check_file(self.root / "boundary.lock", self._lock_descriptor)

    @contextmanager
    def boundary(self, *, allow_pending=False):
        if not _BOUNDARY_LOCK.acquire(blocking=False):
            raise CommitDenied("purchase_boundary_busy")
        stream = None
        locked = False
        try:
            safe_path(self.root)
            safe_path(self.root.parent)
            if not self.root.parent.exists():
                if runtime_paths().files_root is not None:
                    raise CommitDenied("storage_parent_missing")
                # Preserve ordinary desktop debug/audit-commit initialization.
                self.root.parent.mkdir(parents=True, exist_ok=True)
            self._directory_identity = DirectoryIdentityGuard.capture(self.root.parent)
            self._directory_identity.check_parent()
            self.root.mkdir(exist_ok=True)
            self._directory_identity.check_directory(self.root)
            lock_path = self.root / "boundary.lock"
            safe_path(lock_path)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            stream = os.fdopen(descriptor, "r+b")
            self._lock_descriptor = stream.fileno()
            self._check_identity()
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
            self._check_identity()
            entries = self.entries()
            if not allow_pending and any(not item["accepted"] for item in entries):
                raise CommitDenied("pending_unconfirmed")
            self._check_identity()
            yield entries
            self._check_identity()
        finally:
            self._directory_identity = None
            self._lock_descriptor = None
            if stream is not None:
                if locked and os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                stream.close()
            _BOUNDARY_LOCK.release()

    def entries(self):
        """Append-only reservations; unknown/partial content is always a blocker."""
        known = {"boundary.lock", "commits", "pending.claim", "pending.json",
                 "reconcile.claim", "reconciled.json", "reconciled.done", "reconcile.failed", "accepted.json"}
        children = list(self.root.iterdir())
        if any(item.name not in known for item in children):
            raise CommitDenied("pending_unconfirmed")
        slots = []
        if any(item.name.startswith(("pending.", "reconcile")) for item in children):
            slots.append(("legacy", self.root))
        commits = self.root / "commits"
        if commits.exists():
            safe_path(commits)
            if not commits.is_dir():
                raise CommitDenied("pending_unconfirmed")
            directories = list(commits.iterdir())
            if len(directories) > 256:
                raise CommitDenied("pending_capacity_exceeded")
            for directory in sorted(directories):
                matching(directory.name, TOKEN)
                safe_path(directory)
                if not directory.is_dir():
                    raise CommitDenied("pending_unconfirmed")
                slots.append((directory.name, directory))
        entries = []
        for slot, directory in slots:
            names = {item.name for item in directory.iterdir()}
            if slot == "legacy":
                names -= {"boundary.lock", "commits"}
            failed = ("reconcile.failed" in names or directory.resolve() in _RECONCILIATION_FAILED)
            if not {"pending.claim", "pending.json"} <= names or not names <= known - {"boundary.lock", "commits"}:
                raise CommitDenied("pending_unconfirmed")
            pending = read_object(directory / "pending.json")
            claim = self.claim_bytes(directory / "pending.claim")
            try:
                reconciliation.validate_pending(pending)
                if claim != reconciliation.CLAIM_BYTES:
                    raise ValueError("invalid pending claim")
                if slot != "legacy" and slot != pending["source_nonce"]:
                    raise ValueError("pending slot mismatch")
                receipt = None
                accepted = False
                if names & {"reconcile.claim", "reconciled.json", "reconciled.done"}:
                    if not {"reconcile.claim", "reconciled.json", "reconciled.done"} <= names:
                        raise ValueError("incomplete reconciliation")
                    receipt = read_object(directory / "reconciled.json", reconciliation.MAX_RECONCILE_BYTES)
                    reconciliation.validate_receipt(receipt, pending, claim)
                    if self.claim_bytes(directory / "reconcile.claim") != (receipt["request"]["request_id"] + "\n").encode("ascii"):
                        raise ValueError("reconciliation claim mismatch")
                    done = read_object(directory / "reconciled.done")
                    if done != {"receipt_sha256": reconciliation.digest(receipt)}:
                        raise ValueError("incomplete reconciliation witness")
                    # A readable final witness can survive a failed fsync. Prove
                    # persistence again before considering the in-process vote.
                    for filename in ("reconcile.claim", "reconciled.json", "reconciled.done"):
                        path = directory / filename
                        self.verify_durable(path, path.read_bytes())
                    if "accepted.json" in names:
                        acceptance = read_object(directory / "accepted.json", reconciliation.MAX_ACCEPTANCE_BYTES)
                        reconciliation.validate_acceptance(acceptance, receipt, pending, claim)
                        self.verify_durable(directory / "accepted.json", canonical(acceptance))
                        accepted = True
                    self.sync_directory(directory)
                    if directory != self.root:
                        self.sync_directory(directory.parent)
                        self.sync_directory(self.root)
            except (ValueError, OSError) as error:
                raise CommitDenied("pending_unconfirmed") from error
            if "accepted.json" in names and receipt is None:
                raise CommitDenied("acceptance_missing_original_receipt")
            prepared = (receipt is not None and
                        _RECONCILIATION_ACCEPTED.get(directory.resolve()) == digest(receipt))
            entries.append(dict(slot=slot, directory=directory, pending=pending, claim=claim,
                                receipt=receipt, accepted=accepted, prepared=prepared, failed=failed))
        return entries

    @staticmethod
    def claim_bytes(path):
        safe_path(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CommitDenied("non_regular_pending_claim")
            return stream.read(129)

    @staticmethod
    def sync_directory(path):
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def verify_durable(path, raw):
        safe_path(path)
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r+b") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CommitDenied("non_regular_reconciliation_witness")
            os.fsync(stream.fileno())
            if stream.read(len(raw) + 1) != raw:
                raise CommitDenied("marker_readback_mismatch")

    @staticmethod
    def write_durable(path, raw):
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        PendingStore.verify_durable(path, raw)
        PendingStore.sync_directory(path.parent)

    @staticmethod
    def fail_reconciliation(directory):
        """A failed attempt cannot leave automatically usable completion bytes."""
        key = directory.resolve()
        _RECONCILIATION_ACCEPTED.pop(key, None)
        _RECONCILIATION_FAILED.add(key)
        # Preserve even a partial/unsynced failure file. If storage cannot create
        # it, this process still refuses; a restart has no positive vote either.
        path = directory / "reconcile.failed"
        if not path.exists():
            PendingStore.write_durable(path, b"reconciliation_failed\n")

    @staticmethod
    def accept_reconciliation(directory, receipt, *, revalidated=False):
        key = directory.resolve()
        if not revalidated and (key in _RECONCILIATION_FAILED or (directory / "reconcile.failed").exists()):
            raise CommitDenied("reconciliation_failed")
        _RECONCILIATION_ACCEPTED[key] = digest(receipt)

    @staticmethod
    def check_budget(entries, case_id, intent):
        """Freeze the first product policy; confirmed and unknown uses both count.

        There is deliberately no rollover or increase operation. A caller's new
        cycle string is a proposal, not evidence of a newly authorized allowance.
        """
        previous = [item["pending"]["intent"] for item in entries
                    if item["pending"]["case_id"] == case_id and
                    item["pending"]["intent"]["target"] == intent["target"]]
        policy = {key: value for key, value in intent.items() if key != "quantity"}
        if any({key: value for key, value in old.items() if key != "quantity"} != policy for old in previous):
            raise CommitDenied("purchase_authorization_policy_frozen")
        amount = sum(old["unit_price"] * old["quantity"] for old in previous)
        quantity = sum(old["quantity"] for old in previous)
        if (amount + intent["unit_price"] * intent["quantity"] > intent["budget_amount"] or
                quantity + intent["quantity"] > intent["budget_quantity"]):
            raise CommitDenied("purchase_cumulative_budget_exhausted")

    def reserve(self, value):
        self._check_identity()
        directory = self.root
        if (self.root / "pending.claim").exists():
            matching(value["source_nonce"], TOKEN)
            commits = self.root / "commits"
            commits.mkdir(exist_ok=True)
            self._directory_identity.check_directory(commits)
            if len(list(commits.iterdir())) >= 256:
                raise CommitDenied("pending_capacity_exceeded")
            directory = commits / value["source_nonce"]
            directory.mkdir()  # A partial slot remains a permanent blocker.
        self._directory_identity.check_directory(directory)
        claim = directory / "pending.claim"
        with claim.open("xb") as stream:
            stream.write(b"pending_unconfirmed\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary = directory / (".pending-" + uuid4().hex + ".tmp")
        with temporary.open("xb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        destination = directory / "pending.json"
        os.replace(temporary, destination)
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())
        self.sync_directory(directory)
        if directory != self.root:
            # New nonce slots and the commits directory are new parent entries.
            # Syncing only the leaf would not persist their names on POSIX.
            self.sync_directory(directory.parent)
            self.sync_directory(self.root)
        if read_object(destination) != value:
            raise CommitDenied("marker_readback_mismatch")
        self._directory_identity.check_directory(directory)
        self._check_identity()


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

    Each calibrated producer supplies its own case's values from the input frame.
    Plans or a different case's complete observation cannot authorize input.
    """
    from maa.define import AndRecognitionResult, CustomRecognitionResult

    fields(attached, {"case_id", "intent"})
    case_id = attached["case_id"]
    case = PURCHASE_CASES.get(case_id) if type(case_id) is str else None
    if case is None or case.action_node != argv.node_name:
        raise CommitDenied("wrong_purchase_case")
    intent = validate_intent(attached["intent"])
    parent = argv.reco_detail
    if (parent.name != argv.node_name or parent.hit is not True or str(parent.algorithm) != "And" or
            not isinstance(parent.best_result, AndRecognitionResult)):
        raise CommitDenied("observation_not_ready")
    integer(parent.reco_id, 1)
    matches = [sub for sub in parent.best_result.sub_results if sub.name == case.observation_node]
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
    definition = context.get_node_data(case.observation_node)
    recognition = (definition or {}).get("recognition", {})
    if (recognition.get("type") != "Custom" or
            recognition.get("param", {}).get("custom_recognition") != case.observation_action):
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

    def skip_audit_maximum_selection(self, context, argv, node):
        """D14's existing Max control is not a purchase or a permission grant.

        Normal No retains the original native Click in execute(). In audit mode
        leave the selected count unchanged; the final product producer must read
        a real count of one before the actual shared purchase guard can commit.
        """
        if not self.config.enabled:
            raise CommitDenied("maximum_skip_requires_audit_mode")
        paths = self.paths or runtime_paths()
        with PendingStore(paths.marker_root).boundary():
            identity, root = validate_binding(node.get("attach", {}).get("audit_run"), self.config, paths)
            self.stopped(context)
            self.state(root, identity)
            if context.get_node_data(argv.node_name) != node:
                raise CommitDenied("purchase_node_changed")
            self.state(root, identity)
            self.stopped(context)
            return True

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
        with store.boundary() as entries:
            attach = node.get("attach", {})
            if type(attach) is not dict:
                raise CommitDenied("invalid_attach")
            binding = attach.get("audit_run")
            if not self.config.enabled:
                # A prepared one-shot plan must never inherit normal Click
                # fallback when its audit lease/process mode is closed or No.
                # Ordinary production tasks have no audit_intent key.
                if "audit_intent" in attach:
                    raise CommitDenied("audit_intent_requires_audit_mode")
                if binding is not None:
                    fields(binding, {"enabled"})
                    if binding["enabled"] is not False:
                        raise CommitDenied("unexpected_audit_run_in_normal_mode")
                store._check_identity()
                self.stopped(context)
                return click()
            identity, root = validate_binding(binding, self.config, paths)
            # Validate the explicit plan and real recognition evidence before any
            # challenge/prepare. The plan alone is never an observation.
            intent, observation = observed_intent(context, argv, attach.get("audit_intent"), identity, self.clock())
            case_id = attach["audit_intent"]["case_id"]
            store.check_budget(entries, case_id, intent)
            challenge = self.wait_challenge(context, root, identity)
            observed_intent(context, argv, attach.get("audit_intent"), identity, self.clock())
            common = {key: identity[key] for key in ("execution_id", "run_nonce")}
            common.update({key: challenge[key] for key in ("collector_id", "source_challenge")})
            self.logger.emit_audit(dict(common, audit_event="source_anchor", source_boottime_ms=self.clock()))
            source = self.clock()
            nonce = uuid4().hex
            event = dict(common, audit_event="prepare_commit", source_boottime_ms=source,
                         case_id=case_id, source_nonce=nonce, intent=intent, intent_sha256=digest(intent))
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
                               state="pending_unconfirmed", case_id=case_id, intent=intent,
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
            # Both stdout and identity verification can block. Complete them
            # before the final Stop/state/permit/frame-age checks; identity I/O
            # must not consume a permit after its last validation.
            store._check_identity()
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
