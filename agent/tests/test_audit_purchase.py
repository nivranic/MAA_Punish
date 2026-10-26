"""Offline boundary checks; synthetic observations never prove game/OCR behavior."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from maa.define import AndRecognitionResult, CustomRecognitionResult, RecognitionDetail

import audit_commit as commit
from audit_config import AuditConfig
from logger_component import AuditReceipt


IDENTITY = dict(package="com.example.maafw", app_instance="a" * 32,
                session_id="b" * 32, execution_id="run:1", run_nonce="c" * 32)
INTENT = dict(cycle_id="cycle1", target="fragment", currency="coins", unit_price=7,
              quantity=2, budget_amount=70, budget_quantity=10)


def reco(name, algorithm, number, result, raw):
    return RecognitionDetail(number, name, algorithm, True, [30, 40, 50, 60],
                             [result], [result], result, raw, None, [])


class Clock:
    value = 1_000

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += round(seconds * 1000)


class Tasker:
    stopping = False
    stops = 0

    def post_stop(self):
        self.stops += 1
        return SimpleNamespace(wait=lambda: pytest.fail("post_stop must not wait"))


class Context:
    def __init__(self, node):
        self.node = node
        self.tasker = Tasker()

    def get_node_data(self, name):
        if name == commit.OBSERVATION_NODE:
            return {"recognition": {"type": "Custom", "param": {
                "custom_recognition": commit.OBSERVATION_ACTION}}}
        return deepcopy(self.node)


class Emitter:
    def __init__(self, fixture):
        self.fixture = fixture
        self.events = []
        self.sequence = 90
        self.callback = None
        self.logger = SimpleNamespace(error=lambda *args: None)

    def emit_audit(self, event):
        self.events.append(deepcopy(event))
        self.sequence += 1
        receipt = AuditReceipt("d" * 32, self.sequence)
        if self.callback:
            self.callback(event, receipt)
        return receipt


class Fixture:
    def __init__(self, root):
        self.clock = Clock()
        self.root = root
        self.mailbox = root / ".dev-session"
        self.mailbox.mkdir()
        self.paths = commit.RuntimePaths(root / ".audit-commit", root, IDENTITY["package"])
        self.config = AuditConfig(True, IDENTITY["session_id"], "d" * 32)
        self.binding = dict(IDENTITY, enabled=True, mailbox_root=str(self.mailbox))
        self.node = {"action": {"type": "Custom", "param": {"custom_action": "AuditPurchase"}},
                     "attach": {"audit_run": self.binding,
                                "audit_intent": {"case_id": "D12", "intent": deepcopy(INTENT)}}}
        observation = dict(IDENTITY, observed_boottime_ms=1_000, frame_sha256="e" * 64,
                           **{key: INTENT[key] for key in ("target", "currency", "unit_price", "quantity")})
        child = reco(commit.OBSERVATION_NODE, "Custom", 2,
                     CustomRecognitionResult([30, 40, 50, 60], observation), {})
        parent = reco("购买", "And", 3, AndRecognitionResult([child]), [{"reco_id": 2}])
        self.argv = SimpleNamespace(node_name="购买", reco_detail=parent, box=[30, 40, 50, 60])
        self.context = Context(self.node)
        self.emitter = Emitter(self)
        self.inputs = []
        self.challenge = dict(IDENTITY, version=1, collector_id="f" * 32,
                              source_challenge="1" * 32, request_id="2" * 32,
                              operation="source_challenge", expires_boottime_ms=31_000,
                              phase="challenge", observed_boottime_ms=1_000,
                              permit_expires_boottime_ms=31_000)
        self.write("audit-state.json", dict(IDENTITY, version=1, phase="running",
                    started_boottime_ms=500, observed_boottime_ms=1_000, audit_persist_supported=True))
        self.write("audit-permit.json", self.challenge)

    def write(self, name, value):
        (self.mailbox / name).write_bytes(commit.canonical(value))

    def ack(self, event, receipt):
        if event["audit_event"] != "prepare_commit":
            return
        value = dict(self.challenge, operation="persist_ack", request_id="3" * 32,
                     phase="permitted", observed_boottime_ms=self.clock(),
                     permit_expires_boottime_ms=self.clock() + 2_000,
                     expires_boottime_ms=event["source_boottime_ms"] + 5_000,
                     stream_id=receipt.stream_id, sequence=receipt.sequence,
                     **{key: event[key] for key in ("source_nonce", "source_boottime_ms", "intent_sha256")})
        self.write("audit-permit.json", value)

    def click(self):
        marker = commit.read_object(self.paths.marker_root / "pending.json")
        assert marker["effect"] == "unconfirmed"
        self.inputs.append("native_click")
        return True

    def gate(self):
        return commit.PurchaseCommit(self.emitter, config=self.config, paths=self.paths,
                                     clock=self.clock, sleep=self.clock.sleep)

    def run(self, click=None):
        return self.gate().execute(self.context, self.argv, deepcopy(self.node), click or self.click)


@pytest.fixture
def f(tmp_path):
    return Fixture(tmp_path)


@pytest.mark.parametrize("project_name", ["pi", "PI"])
def test_runtime_paths_use_installed_android_pi_and_preserve_package_case(tmp_path, monkeypatch, project_name):
    package = "com.Example.maafw"
    files_root = tmp_path / "Android" / "data" / package / "files"
    source = files_root / project_name / "agent" / "audit_commit.py"
    source.parent.mkdir(parents=True)
    monkeypatch.setattr(commit, "__file__", str(source))
    monkeypatch.setenv("ANDROID_ROOT", "/system")

    paths = commit.runtime_paths()
    assert paths == commit.RuntimePaths(files_root / ".audit-commit", files_root, package)


@pytest.mark.parametrize("parts", [
    ("Android", "data", "files", "Pi", "agent"),
    ("Android", "data", "files", "pI", "agent"),
    ("Android", "data", "files", "other", "agent"),
    ("Android", "data", "files", "pi", "Agent"),
    ("Android", "data", "Files", "pi", "agent"),
    ("Android", "Data", "files", "pi", "agent"),
    ("android", "data", "files", "pi", "agent"),
])
def test_runtime_paths_reject_unknown_android_layouts(tmp_path, monkeypatch, parts):
    android, data, files, project, agent = parts
    source = tmp_path / android / data / "com.example.maafw" / files / project / agent / "audit_commit.py"
    source.parent.mkdir(parents=True)
    monkeypatch.setattr(commit, "__file__", str(source))
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    with pytest.raises(commit.CommitDenied, match="unrecognized_android_install_root"):
        commit.runtime_paths()


def test_runtime_paths_share_pending_between_current_and_legacy_pi_roots(tmp_path, monkeypatch):
    files_root = tmp_path / "Android" / "data" / "com.example.maafw" / "files"
    files_root.mkdir(parents=True)
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    roots = []
    for project in ("pi", "PI"):
        monkeypatch.setattr(commit, "__file__", str(files_root / project / "agent" / "audit_commit.py"))
        roots.append(commit.runtime_paths().marker_root)
    assert roots[0] == roots[1] == files_root / ".audit-commit"
    roots[0].mkdir()
    (roots[0] / "pending.claim").write_bytes(b"pending_unconfirmed\n")
    with pytest.raises(commit.CommitDenied, match="pending_unconfirmed"):
        with commit.PendingStore(roots[1]).boundary():
            pytest.fail("Legacy path must not bypass the current installation pending marker")


def test_valid_ack_reserves_before_one_input_and_never_settles(f):
    f.emitter.callback = f.ack
    assert f.run() is True
    assert f.inputs == ["native_click"]
    assert [event["audit_event"] for event in f.emitter.events] == [
        "source_anchor", "prepare_commit", "purchase_input", "purchase_input"]
    marker = commit.read_object(f.paths.marker_root / "pending.json")
    assert marker["sequence"] == 92  # Stream is already in progress.
    assert marker["intent_sha256"] == hashlib.sha256(commit.canonical(INTENT)).hexdigest()
    assert marker["controller_completed"] is None and marker["state"] == "pending_unconfirmed"
    assert f.emitter.events[-1]["controller_completed"] is True
    assert f.emitter.events[-1]["effect"] == "unconfirmed"
    with pytest.raises(commit.CommitDenied, match="pending_unconfirmed"):
        f.run()


def test_no_ack_bounded_zero_input(f):
    with pytest.raises(commit.CommitDenied, match="persist_ack_timeout"):
        f.run()
    assert f.clock() == 6_000
    assert f.inputs == []
    assert not (f.paths.marker_root / "pending.claim").exists()


@pytest.mark.parametrize("key,value", [
    ("session_id", "0" * 32), ("run_nonce", "0" * 32), ("execution_id", "old"),
    ("collector_id", "0" * 32), ("source_challenge", "0" * 32),
    ("stream_id", "0" * 32), ("sequence", 91), ("source_nonce", "0" * 32),
    ("intent_sha256", "0" * 64), ("permit_expires_boottime_ms", 1_000),
    ("source_boottime_ms", 999), ("sequence", True), ("extra", "not_allowed"),
])
def test_mismatched_expired_or_bad_ack_is_zero_input(f, key, value):
    def publish(event, receipt):
        f.ack(event, receipt)
        if event["audit_event"] == "prepare_commit":
            ack = commit.read_object(f.mailbox / "audit-permit.json")
            ack[key] = value
            f.write("audit-permit.json", ack)
    f.emitter.callback = publish
    with pytest.raises(commit.CommitDenied):
        f.run()
    assert not f.inputs


@pytest.mark.parametrize("change", ["stopping", "state", "node", "expiry"])
def test_final_recheck_after_marker_io_denies_and_keeps_marker(f, monkeypatch, change):
    f.emitter.callback = f.ack
    reserve = commit.PendingStore.reserve
    def slow_reserve(store, value):
        reserve(store, value)
        if change == "stopping":
            f.context.tasker.stopping = True
        elif change == "state":
            state = commit.read_object(f.mailbox / "audit-state.json")
            state["phase"] = "idle"
            f.write("audit-state.json", state)
        elif change == "node":
            f.node["attach"]["audit_run"] = {"enabled": False}
        else:
            f.clock.value += 2_000
    monkeypatch.setattr(commit.PendingStore, "reserve", slow_reserve)
    with pytest.raises(commit.CommitDenied):
        f.run()
    assert not f.inputs
    assert (f.paths.marker_root / "pending.claim").exists()


def test_stop_while_waiting_ack_is_prompt(f):
    def stop(seconds):
        f.clock.sleep(seconds)
        f.context.tasker.stopping = True
    gate = f.gate()
    gate.sleep = stop
    with pytest.raises(commit.CommitDenied, match="task_stopping"):
        gate.execute(f.context, f.argv, f.node, f.click)
    assert f.clock() == 1_025 and not f.inputs


@pytest.mark.parametrize("failure", ["replace", "fsync", "readback"])
def test_marker_failure_keeps_blocker_zero_input(f, monkeypatch, failure):
    f.emitter.callback = f.ack
    if failure in {"replace", "fsync"}:
        monkeypatch.setattr(commit.os, failure, lambda *args: (_ for _ in ()).throw(OSError("disk failed")))
    else:
        read = commit.read_object
        monkeypatch.setattr(commit, "read_object", lambda path: {} if path.name == "pending.json" else read(path))
    with pytest.raises((OSError, commit.CommitDenied)):
        f.run()
    assert not f.inputs
    assert (f.paths.marker_root / "pending.claim").exists()


@pytest.mark.parametrize("node_name", ["购买", "购买_宿舍", "点击购买_宿舍"])
def test_pending_survives_normal_mode_and_new_session(f, node_name):
    f.emitter.callback = f.ack
    f.run()
    f.argv.node_name = node_name
    f.config = AuditConfig()
    f.node["attach"] = {"audit_run": {"enabled": False}}
    with pytest.raises(commit.CommitDenied, match="pending_unconfirmed"):
        f.run(lambda: pytest.fail("normal mode must not bypass pending"))
    f.config = AuditConfig(True, "9" * 32, "8" * 32)
    with pytest.raises(commit.CommitDenied, match="pending_unconfirmed"):
        f.run()


@pytest.mark.parametrize("missing", ["intent", "observation", "price", "quantity"])
def test_missing_observation_or_explicit_plan_never_prepares(f, missing):
    if missing == "intent":
        f.node["attach"].pop("audit_intent")
    elif missing == "observation":
        f.argv.reco_detail.best_result.sub_results = []
    else:
        key = "unit_price" if missing == "price" else "quantity"
        del f.node["attach"]["audit_intent"]["intent"][key]
    with pytest.raises(commit.CommitDenied):
        f.run()
    assert not f.inputs and not f.emitter.events


@pytest.mark.parametrize("key,value", [("unit_price", 0), ("quantity", 1), ("run_nonce", "0" * 32),
                                      ("observed_boottime_ms", 1_001)])
def test_observation_must_match_current_intent_and_run(f, key, value):
    f.argv.reco_detail.best_result.sub_results[0].best_result.detail[key] = value
    with pytest.raises(commit.CommitDenied):
        f.run()
    assert not f.emitter.events and not f.inputs


@pytest.mark.parametrize("bad", [True, 2.0, -1, 0, 2**53])
def test_quantity_strict_positive_integer(f, bad):
    f.node["attach"]["audit_intent"]["intent"]["quantity"] = bad
    with pytest.raises(commit.CommitDenied):
        f.run()
    assert not f.inputs


def test_bad_json_duplicate_key_and_wrong_runtime_path_fail_closed(f):
    (f.mailbox / "audit-permit.json").write_bytes(b'{"version":1,"version":1}')
    with pytest.raises(commit.CommitDenied):
        f.run()
    f.node["attach"]["audit_run"]["mailbox_root"] = str(f.root / "elsewhere" / ".dev-session")
    with pytest.raises(commit.CommitDenied, match="mailbox_root_mismatch"):
        f.run()


@pytest.mark.parametrize("node_name", ["购买", "购买_宿舍", "点击购买_宿舍"])
def test_normal_mode_delegates_native_result_without_clock_or_audit(f, node_name):
    f.argv.node_name = node_name
    f.config = AuditConfig()
    f.node["attach"] = {"audit_run": {"enabled": False}}
    gate = f.gate()
    gate.clock = lambda: pytest.fail("normal mode needs no phone clock")
    inputs = []
    assert gate.execute(f.context, f.argv, f.node, lambda: inputs.append("click") or False) is False
    assert inputs == ["click"] and not f.emitter.events
    assert not (f.paths.marker_root / "pending.claim").exists()


@pytest.mark.parametrize("node_name", ["购买_宿舍", "点击购买_宿舍"])
@pytest.mark.parametrize("forged_d12", [False, True])
def test_dorm_audit_refuses_before_challenge_even_with_complete_d12_evidence(f, node_name, forged_d12):
    f.argv.node_name = node_name
    f.argv.reco_detail.name = node_name
    f.emitter.callback = f.ack
    if not forged_d12:
        f.node["attach"] = {}
    with pytest.raises(commit.CommitDenied, match="^purchase_observation_not_implemented$"):
        f.run()
    assert not f.inputs and not f.emitter.events
    assert not (f.paths.marker_root / "pending.claim").exists()


@pytest.mark.parametrize("pending", [False, True])
def test_fuse_owner_mapping_differs_from_child_uid_without_mode_bypass(f, monkeypatch, pending):
    f.config = AuditConfig()
    f.node["attach"] = {"audit_run": {"enabled": False}}
    f.paths.marker_root.mkdir()
    if pending:
        (f.paths.marker_root / "pending.claim").write_bytes(b"unconfirmed")
    # Model FUSE metadata as seen by a privileged child: every file in the
    # installed external namespace belongs to mapped UID 10042, child UID is 0.
    original_stat, original_fstat = Path.stat, commit.os.fstat
    def mapped(value):
        values = list(value)
        values[4] = 10042
        return os.stat_result(values)
    monkeypatch.setattr(Path, "stat", lambda path, **kwargs: mapped(original_stat(path, **kwargs)))
    monkeypatch.setattr(commit.os, "fstat", lambda descriptor: mapped(original_fstat(descriptor)))
    monkeypatch.setattr(commit.os, "getuid", lambda: 0, raising=False)
    inputs = []
    if pending:
        with pytest.raises(commit.CommitDenied, match="pending_unconfirmed"):
            f.run(lambda: inputs.append("click") or True)
        assert not inputs
    else:
        assert f.run(lambda: inputs.append("click") or True) is True
        assert inputs == ["click"]


def test_audit_process_cannot_accept_normal_binding(f):
    f.node["attach"]["audit_run"] = {"enabled": False}
    with pytest.raises(commit.CommitDenied):
        f.run()


def test_process_restart_observes_pending_on_disk(f):
    f.emitter.callback = f.ack
    f.run()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("MAA_PUNISH_AUDIT_")}
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-c", '''
import sys
from pathlib import Path
from audit_commit import PendingStore, CommitDenied
try:
    with PendingStore(Path(sys.argv[1])).boundary():
        raise AssertionError("restart bypassed pending")
except CommitDenied as error:
    assert str(error) == "pending_unconfirmed"
''', str(f.paths.marker_root)], env=environment, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key,value", [("run_nonce", "0" * 32), ("session_id", "0" * 32),
                                      ("phase", "idle"), ("audit_persist_supported", False)])
def test_old_or_inactive_run_state_never_prepares(f, key, value):
    state = commit.read_object(f.mailbox / "audit-state.json")
    state[key] = value
    f.write("audit-state.json", state)
    with pytest.raises(commit.CommitDenied):
        f.run()
    assert not f.emitter.events and not f.inputs


def test_audit_config_session_must_match_current_attach(f):
    f.config = AuditConfig(True, "0" * 32, "d" * 32)
    with pytest.raises(commit.CommitDenied, match="audit_session_mismatch"):
        f.run()


def test_stop_or_expiry_during_requested_log_keeps_marker_without_input(f):
    def delayed_emit(event, receipt):
        f.ack(event, receipt)
        if event["audit_event"] == "purchase_input":
            f.clock.value += 2_000
    f.emitter.callback = delayed_emit
    with pytest.raises(commit.CommitDenied, match="permit_expired_or_invalid"):
        f.run()
    assert not f.inputs and (f.paths.marker_root / "pending.claim").exists()


def test_missing_boottime_has_no_monotonic_fallback(monkeypatch):
    monkeypatch.delattr(commit.time, "CLOCK_BOOTTIME", raising=False)
    with pytest.raises(commit.CommitDenied, match="clock_boottime_unavailable"):
        commit.boottime_ms()


def test_wrapper_posts_stop_without_wait_on_direct_purchase_entry(f, monkeypatch):
    from action.basics import AuditPurchase as module
    monkeypatch.setattr(module, "LoggerComponent", lambda _: f.emitter)
    monkeypatch.setattr(module, "PurchaseCommit", lambda _: f.gate())
    f.node["attach"].pop("audit_intent")
    result = module.AuditPurchase().run(f.context, f.argv)
    assert result.success is False and f.context.tasker.stops == 1 and not f.inputs


def test_production_pipeline_and_language_overrides_keep_guard():
    import jsonc
    root = Path(__file__).resolve().parents[2]
    source = jsonc.loads((root / "resource/base/pipeline/Buy_Item.jsonc").read_text(encoding="utf-8"))
    assert source["购买"]["action"] == {"type": "Custom", "param": {"custom_action": "AuditPurchase"}}
    assert source["购买"]["recognition"]["param"]["all_of"] == ["识别购买界面逆元碎片", "识别购买界面购买"]
    for path in (root / "resource").glob("*/pipeline/Buy_Item.jsonc"):
        if path.parts[-3] != "base":
            assert "action" not in jsonc.loads(path.read_text(encoding="utf-8")).get("购买", {})
    registration = (root / "agent/agent_file.py").read_text(encoding="utf-8")
    assert '@AgentServer.custom_action("AuditPurchase")' in registration
