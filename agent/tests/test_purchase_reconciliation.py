"""Synthetic manual effects/faults. No phone, purchase or real effect proof."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

import audit_commit as commit
from audit_config import AuditConfig
from action.basics import AuditPurchaseReconcile as action
import purchase_reconciliation_contract as contract
from test_audit_purchase import Fixture, IDENTITY
from test_support.native_lifecycle import stop_and_wait_idle


def number(value):
    return dict(value=value, text=str(value), box=[1, 1, 5, 5])


def manual_effect(purchase):
    def frame(sha, at, balance, inventory, limit):
        return dict(png_sha256=sha * 64, width=30, height=20,
            captured_at=f"2026-10-26T{at}+08:00", product_text="Synthetic product", currency_text="Synthetic coin",
            balance=number(balance), inventory=number(inventory), remaining_limit=number(limit))
    cost = purchase["unit_price"] * purchase["quantity"]
    count = purchase["quantity"]
    return dict(version=1, state="confirmed_consumed", reviewer="synthetic:test",
        reviewed_at="2026-10-26T22:32:00+08:00", inventory_units_per_purchase=1,
        **{key: purchase[key] for key in ("target", "currency", "unit_price", "quantity")},
        before=frame("a", "22:30:00", 100, 10, 8), after=frame("b", "22:31:00", 100 - cost, 10 + count, 8 - count))


def source_binding(identity):
    entries = [dict(path="agent/example.py", size=1, sha256="e" * 64)]
    revision = hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    source = dict(version=1, package=identity["package"], apk_version_code=2209, revision=revision, files=entries)
    reload = dict(version=1, session_id=identity["session_id"], operation_id="a" * 32, request_id="b" * 32,
        phase="loaded", revision=revision, reason="", rolled_back=False, native_generation=2,
        agent_generation=3, display_id=22, controller_reused=True, resource_loaded=True, agent_loaded=True)
    return source, reload


def request_for(f, pending):
    event = next(item for item in f.emitter.events if item["audit_event"] == "prepare_commit")
    envelope = dict(event="maa_punish_audit", session_id=pending["session_id"], stream_id=pending["stream_id"],
        sequence=pending["sequence"], monotonic_ns=1_000_000, level="INFO", logger="synthetic.purchase",
        message=contract.canonical(event).decode())
    reservation = dict(status="pending_unconfirmed", channel={key: pending[key] for key in
        contract.IDENTITY | {"collector_id", "source_challenge"}},
        **{key: pending[key] for key in ("stream_id", "sequence", "source_nonce", "intent", "intent_sha256", "source_boottime_ms")},
        host_received_at="2026-10-26T22:30:30+08:00", source_envelope=envelope)
    source, reload = source_binding(IDENTITY)
    return dict(version=1, operation="reconcile_purchase", request_id="9" * 32,
        maintenance_identity=deepcopy(IDENTITY), runtime=contract.loaded_runtime(source, reload, IDENTITY),
        pending_slot="legacy", pending_sha256=contract.digest(pending),
        claim_sha256=hashlib.sha256(contract.CLAIM_BYTES).hexdigest(), case_record_sha256="d" * 64,
        reservation=reservation, effect=manual_effect(pending["intent"]), expires_boottime_ms=30_000)


def synthetic_terminal_log(request, receipt, pending, claim=contract.CLAIM_BYTES):
    """Explicit fixture, not an App log or real native acceptance certificate."""
    ready = dict(audit_event="purchase_reconciliation_ready", maintenance_identity=request["maintenance_identity"],
        request_id=request["request_id"], receipt_sha256=contract.digest(receipt),
        pending_sha256=contract.digest(pending), claim_sha256=hashlib.sha256(claim).hexdigest(), runtime=request["runtime"])
    envelope = dict(event="maa_punish_audit", session_id=request["maintenance_identity"]["session_id"], stream_id="d" * 32,
        sequence=100, monotonic_ns=1000, level="INFO", logger="synthetic.maintenance", message=contract.canonical(ready).decode())
    terminal = dict(version=1, maintenance_identity=request["maintenance_identity"], entry=action.NODE, task_id=77,
        wait_status=3000, native_status=3000, node_count=1, completed_false_count=0,
        single_entry_completed=True, effective_success=True, outcome="COMPLETED")
    result = (f"task_result execution_id={request['maintenance_identity']['execution_id']} result_index=0 task=Screencap "
        "effective_success=true; succeeded; task_id=77; wait_status=3000; recovery_history=no_shared_recovery; native_status=3000")
    native_keys = ("entry", "task_id", "wait_status", "native_status", "node_count", "completed_false_count", "single_entry_completed")
    result += "; purchase_maintenance_native=" + contract.canonical({key: terminal[key] for key in native_keys}).decode()
    rows = [dict(type="header", startedAt=1000, tasks=["Screencap"])]
    for at, message in ((1001, contract.canonical(envelope).decode()),
            (1002, "purchase_maintenance_terminal " + contract.canonical(terminal).decode()), (1003, result)):
        rows.append(dict(type="line", atMillis=at, kind="Agent" if at == 1001 else "Info", text=message, detail=None))
    rows.append(dict(type="footer", endedAt=1004, outcome="COMPLETED"))
    return "".join(contract.canonical(row).decode() + "\n" for row in rows)


def finalize(prepared, request=None):
    root = prepared.f.paths.marker_root
    receipt = commit.read_object(root / "reconciled.json", contract.MAX_RECONCILE_BYTES)
    request = request or receipt["request"]
    raw = synthetic_terminal_log(request, receipt, prepared.pending)
    acceptance = contract.acceptance_for(request, receipt, prepared.pending, contract.CLAIM_BYTES,
                                        "run_20261026_223000_1.jsonl", raw)
    commit.PendingStore.write_durable(root / "accepted.json", contract.canonical(acceptance))
    return acceptance


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    # Independent probes import this fixture too; keep the fixed host ledger out
    # of the real checkout while testing cross-file authorization continuity.
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import android_audit as host
    monkeypatch.setattr(host, "authorization_root", lambda: tmp_path / "host-authorizations")
    f = Fixture(tmp_path)
    f.emitter.callback = f.ack
    assert f.run()
    pending = commit.read_object(f.paths.marker_root / "pending.json")
    request = request_for(f, pending)
    source, reload = source_binding(IDENTITY)
    f.write("source-state.json", source)
    f.write("reload-state.json", reload)
    f.write("purchase-reconcile.json", request)
    node = {"recognition": {"type": "DirectHit"}, "action": {"type": "Custom", "param": {
        "custom_action": "AuditPurchaseReconcile"}}, "attach": {"audit_run": f.binding}}
    original = f.context.get_node_data
    f.context.get_node_data = lambda name: deepcopy(node) if name == action.NODE else original(name)
    monkeypatch.setattr(action, "AUDIT_CONFIG", f.config)
    monkeypatch.setattr(action, "LoggerComponent", lambda _: f.emitter)
    monkeypatch.setattr(action, "runtime_paths", lambda: f.paths)
    monkeypatch.setattr(action, "boottime_ms", f.clock)
    monkeypatch.setattr(action, "PurchaseCommit", lambda *_, **__: commit.PurchaseCommit(
        None, config=f.config, paths=f.paths, clock=f.clock, sleep=f.clock.sleep))
    monkeypatch.setattr(action, "time", SimpleNamespace(sleep=f.clock.sleep))
    return SimpleNamespace(f=f, pending=pending, request=request, node=node)


def reconcile(prepared):
    return action.AuditPurchaseReconcile().run(prepared.f.context, SimpleNamespace(node_name=action.NODE))


def blocked(prepared):
    with pytest.raises((commit.CommitDenied, OSError)):
        with commit.PendingStore(prepared.f.paths.marker_root).boundary():
            pytest.fail("incomplete reconciliation must stay blocked")


def test_explicit_reconciliation_is_append_only_and_idempotent(prepared):
    root = prepared.f.paths.marker_root
    original = {name: (root / name).read_bytes() for name in ("pending.json", "pending.claim")}
    assert reconcile(prepared).success is True
    after = {path.name: path.read_bytes() for path in root.iterdir()}
    assert reconcile(prepared).success is True
    assert {path.name: path.read_bytes() for path in root.iterdir()} == after
    assert all((root / name).read_bytes() == raw for name, raw in original.items())
    blocked(prepared)  # A true custom return is still only a prepared receipt.
    finalize(prepared)
    with commit.PendingStore(root).boundary() as entries:
        assert len(entries) == 1 and entries[0]["receipt"]["state"] == "confirmed_consumed"
    assert prepared.f.inputs == ["native_click"]  # original synthetic purchase only


def test_next_purchase_appends_new_nonce_slot_without_replacing_old_pending(prepared):
    root = prepared.f.paths.marker_root
    original = (root / "pending.json").read_bytes()
    assert reconcile(prepared).success is True
    finalize(prepared)
    prepared.f.write("audit-permit.json", prepared.f.challenge)
    assert prepared.f.run()
    directories = list((root / "commits").iterdir())
    assert len(directories) == 1
    new_pending = commit.read_object(directories[0] / "pending.json")
    assert directories[0].name == new_pending["source_nonce"] != prepared.pending["source_nonce"]
    assert (root / "pending.json").read_bytes() == original
    assert prepared.f.inputs == ["native_click", "native_click"]
    blocked(prepared)


def test_new_nonce_reservation_syncs_leaf_and_both_new_parent_entries(prepared, monkeypatch):
    root = prepared.f.paths.marker_root
    assert reconcile(prepared).success is True
    finalize(prepared)
    prepared.f.write("audit-permit.json", prepared.f.challenge)
    calls = []
    monkeypatch.setattr(commit.PendingStore, "sync_directory", staticmethod(lambda path: calls.append(path)))
    assert prepared.f.run()
    leaf = next((root / "commits").iterdir())
    assert calls == [root, leaf, root / "commits", root]


def test_final_acceptance_survives_real_fresh_process_and_normal_no_mode(prepared):
    import os
    from pathlib import Path
    import subprocess
    import sys

    assert reconcile(prepared).success is True
    finalize(prepared)
    root = prepared.f.paths.marker_root
    commit._RECONCILIATION_ACCEPTED.clear()
    commit._RECONCILIATION_FAILED.clear()
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    probe = ("from audit_commit import PendingStore; from pathlib import Path; import sys; "
             "guard=PendingStore(Path(sys.argv[1])).boundary(); guard.__enter__(); guard.__exit__(None,None,None)")
    result = subprocess.run([sys.executable, "-c", probe, str(root)], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    prepared.f.config = AuditConfig()
    prepared.f.node["attach"] = {"audit_run": {"enabled": False}}
    assert prepared.f.run()
    assert prepared.f.inputs == ["native_click", "native_click"]


@pytest.mark.parametrize("change", ["exhausted", "amount", "quantity", "cycle", "currency", "price"])
def test_final_accepted_history_still_freezes_product_authorization_after_restart(prepared, change):
    assert reconcile(prepared).success is True
    finalize(prepared)
    commit._RECONCILIATION_ACCEPTED.clear()
    commit._RECONCILIATION_FAILED.clear()
    intent = prepared.f.node["attach"]["audit_intent"]["intent"]
    # Existing quantity=2 of ten. Ask for all ten again: the single request is
    # individually valid, but its historical total is twelve.
    intent["quantity"] = 10
    observation = prepared.f.argv.reco_detail.best_result.sub_results[0].best_result.detail
    observation["quantity"] = 10
    expected = "cumulative_budget"
    if change != "exhausted":
        key, value = {"amount": ("budget_amount", 140), "quantity": ("budget_quantity", 20),
            "cycle": ("cycle_id", "invented-next-cycle"), "currency": ("currency", "other-coins"),
            "price": ("unit_price", 6)}[change]
        intent[key] = value
        if key in observation:
            observation[key] = value
        expected = "policy_frozen"
    prepared.f.write("audit-permit.json", prepared.f.challenge)
    with pytest.raises(commit.CommitDenied, match=expected):
        prepared.f.run()
    assert prepared.f.inputs == ["native_click"]


def test_done_without_final_acceptance_never_recovers_by_restarting_or_replaying_old_request(prepared):
    assert reconcile(prepared).success is True
    commit._RECONCILIATION_ACCEPTED.clear()
    blocked(prepared)
    assert reconcile(prepared).success is False
    blocked(prepared)
    assert not (prepared.f.paths.marker_root / "accepted.json").exists()


def test_fresh_explicit_revalidation_can_finish_an_interrupted_original_in_new_run(prepared):
    assert reconcile(prepared).success is True
    root = prepared.f.paths.marker_root
    old = {name: (root / name).read_bytes() for name in ("pending.json", "pending.claim", "reconciled.json", "reconciled.done")}
    commit.PendingStore.fail_reconciliation(root)
    commit._RECONCILIATION_ACCEPTED.clear()
    commit._RECONCILIATION_FAILED.clear()
    blocked(prepared)
    identity = dict(IDENTITY, execution_id="maintenance:new", run_nonce="8" * 32)
    prepared.f.binding.update(identity)
    state = commit.read_object(prepared.f.mailbox / "audit-state.json")
    prepared.f.write("audit-state.json", dict(state, **identity))
    request = deepcopy(prepared.request)
    receipt = commit.read_object(root / "reconciled.json", contract.MAX_RECONCILE_BYTES)
    request.update(operation="revalidate_purchase", request_id="7" * 32,
        prior_receipt_sha256=contract.digest(receipt), maintenance_identity=identity)
    prepared.f.write("purchase-reconcile.json", request)
    assert reconcile(prepared).success is True
    blocked(prepared)  # The new true return still requires actual host terminal acceptance.
    finalize(prepared, request)
    commit._RECONCILIATION_ACCEPTED.clear()
    with commit.PendingStore(root).boundary() as entries:
        assert entries[0]["accepted"] is True and entries[0]["failed"] is True
    assert all((root / name).read_bytes() == raw for name, raw in old.items())
    assert (root / "reconcile.failed").exists()
    assert prepared.f.inputs == ["native_click"]


@pytest.mark.parametrize("change", ["missing_footer", "cancelled", "completed_with_failures", "late_ready", "wrong_run", "false_node", "two_nodes", "extra_terminal", "wrong_hash",
    "agent_terminal", "agent_task_result", "payload_conflict", "ready_as_info"])
def test_final_acceptance_rejects_unknown_late_or_cross_run_native_evidence(prepared, change):
    assert reconcile(prepared).success is True
    acceptance = finalize(prepared)
    rows = [json.loads(line) for line in acceptance["run_log"].splitlines()]
    if change == "missing_footer":
        rows.pop()
    elif change == "cancelled":
        rows[-1]["outcome"] = "CANCELLED"
    elif change == "completed_with_failures":
        rows[-1]["outcome"] = "COMPLETED_WITH_FAILURES"
    elif change == "late_ready":
        rows[1], rows[2] = rows[2], rows[1]
    elif change == "extra_terminal":
        rows.insert(3, deepcopy(rows[2]))
    elif change == "agent_terminal":
        rows[2]["kind"] = "Agent"
    elif change == "agent_task_result":
        rows[3]["kind"] = "AgentError"
    elif change == "ready_as_info":
        rows[1]["kind"] = "Info"
    elif change == "payload_conflict":
        prefix, payload = rows[3]["text"].split("; purchase_maintenance_native=", 1)
        native = json.loads(payload)
        native["node_count"] = 2
        rows[3]["text"] = prefix + "; purchase_maintenance_native=" + contract.canonical(native).decode()
    elif change in {"wrong_run", "false_node", "two_nodes"}:
        terminal = json.loads(rows[2]["text"].split(" ", 1)[1])
        if change == "wrong_run":
            terminal["maintenance_identity"]["run_nonce"] = "0" * 32
        elif change == "false_node":
            terminal["single_entry_completed"] = False
        else:
            terminal["node_count"] = 2
        rows[2]["text"] = "purchase_maintenance_terminal " + contract.canonical(terminal).decode()
    acceptance["run_log"] = "".join(contract.canonical(row).decode() + "\n" for row in rows)
    acceptance["run_log_sha256"] = hashlib.sha256(acceptance["run_log"].encode()).hexdigest()
    if change == "wrong_hash":
        acceptance["receipt_sha256"] = "0" * 64
    (prepared.f.paths.marker_root / "accepted.json").write_bytes(contract.canonical(acceptance))
    commit._RECONCILIATION_ACCEPTED.clear()
    blocked(prepared)


@pytest.fixture
def synthetic_terminal_evidence(prepared):
    assert reconcile(prepared).success is True
    receipt = commit.read_object(prepared.f.paths.marker_root / "reconciled.json", contract.MAX_RECONCILE_BYTES)
    rows = [json.loads(line) for line in synthetic_terminal_log(
        prepared.request, receipt, prepared.pending).splitlines()]
    return SimpleNamespace(request=prepared.request, receipt=receipt, pending=prepared.pending, rows=rows)


def synthetic_acceptance(evidence):
    raw = "".join(contract.canonical(row).decode() + "\n" for row in evidence.rows)
    return contract.acceptance_for(evidence.request, evidence.receipt, evidence.pending, contract.CLAIM_BYTES,
                                   "run_20261026_223000_1.jsonl", raw)


@pytest.mark.parametrize("framing", ["two_envelopes", "plain_prefix", "plain_between", "crlf"])
def test_synthetic_batched_agent_audit_lines_preserve_original_log_bytes(synthetic_terminal_evidence, framing):
    evidence = synthetic_terminal_evidence
    row = evidence.rows[1]
    ready = row["text"]
    anchor = json.loads(ready)
    anchor.update(sequence=99, message=contract.canonical(dict(audit_event="source_anchor")).decode())
    anchor = contract.canonical(anchor).decode()
    row["text"] = {
        "two_envelopes": anchor + "\n" + ready,
        "plain_prefix": "synthetic Agent startup\n" + ready,
        "plain_between": anchor + "\nsynthetic Agent startup\n" + ready + "\n",
        "crlf": anchor + "\r\n" + ready + "\r\n",
    }[framing]
    original_rows = deepcopy(evidence.rows)
    acceptance = synthetic_acceptance(evidence)
    assert acceptance["run_log"] == "".join(contract.canonical(item).decode() + "\n" for item in original_rows)
    assert acceptance["run_log_sha256"] == hashlib.sha256(acceptance["run_log"].encode()).hexdigest()
    assert evidence.rows == original_rows


@pytest.mark.parametrize("failure", ["duplicate_ready", "info_ready", "agent_error_ready", "extra_info_ready",
    "bad_envelope_json", "bad_event_json", "same_line_json", "split_line_json", "split_record_json",
    "duplicate_key", "nonfinite", "wrong_identity", "wrong_request", "wrong_receipt", "wrong_pending", "wrong_claim",
    "wrong_runtime", "wrong_session", "wrong_level",
    "ready_after_terminal", "agent_native_terminal", "agent_native_result", "combined_native_info"])
def test_synthetic_batched_audit_framing_keeps_strict_evidence_gates(synthetic_terminal_evidence, failure):
    evidence = synthetic_terminal_evidence
    row = evidence.rows[1]
    ready = row["text"]
    if failure == "duplicate_ready":
        row["text"] = ready + "\n" + ready
    elif failure in {"info_ready", "agent_error_ready"}:
        row.update(kind="Info" if failure == "info_ready" else "AgentError",
                   text="synthetic plain prefix\n" + ready)
    elif failure == "extra_info_ready":
        evidence.rows.insert(1, dict(row, kind="Info", text="synthetic plain prefix\n" + ready))
    elif failure == "bad_envelope_json":
        row["text"] = ready + '\n{"event":'
    elif failure == "same_line_json":
        row["text"] = ready + ready
    elif failure in {"split_line_json", "split_record_json"}:
        midpoint = len(ready) // 2
        row["text"] = ready[:midpoint]
        if failure == "split_line_json":
            row["text"] += "\n" + ready[midpoint:]
        else:
            evidence.rows.insert(2, dict(row, text=ready[midpoint:]))
    elif failure == "duplicate_key":
        row["text"] = ready + '\n{"event":"maa_punish_audit","event":"maa_punish_audit"}'
    elif failure == "nonfinite":
        row["text"] = ready + '\n{"synthetic":NaN}'
    elif failure in {"bad_event_json", "wrong_identity", "wrong_request", "wrong_receipt", "wrong_pending", "wrong_claim",
                     "wrong_runtime", "wrong_session", "wrong_level"}:
        envelope = json.loads(ready)
        event = json.loads(envelope["message"])
        if failure == "wrong_identity":
            event["maintenance_identity"]["run_nonce"] = "0" * 32
        elif failure in {"wrong_request", "wrong_receipt", "wrong_pending", "wrong_claim"}:
            key = {"wrong_request": "request_id", "wrong_receipt": "receipt_sha256",
                   "wrong_pending": "pending_sha256", "wrong_claim": "claim_sha256"}[failure]
            event[key] = "0" * len(event[key])
        elif failure == "wrong_runtime":
            event["runtime"]["agent_generation"] += 1
        elif failure == "wrong_session":
            envelope["session_id"] = "0" * 32
        elif failure == "wrong_level":
            envelope["level"] = "WARNING"
        envelope["message"] = '{"audit_event":' if failure == "bad_event_json" else contract.canonical(event).decode()
        row["text"] = "synthetic plain prefix\n" + contract.canonical(envelope).decode()
    elif failure == "ready_after_terminal":
        evidence.rows[1], evidence.rows[2] = evidence.rows[2], evidence.rows[1]
        row["text"] = "synthetic plain prefix\n" + ready
    elif failure in {"agent_native_terminal", "agent_native_result"}:
        native_index = 2 if failure == "agent_native_terminal" else 3
        row["text"] += "\n" + evidence.rows.pop(native_index)["text"]
    else:
        evidence.rows[2]["text"] += "\n" + evidence.rows.pop(3)["text"]
    with pytest.raises(ValueError):
        synthetic_acceptance(evidence)


@pytest.mark.parametrize("failure", ["unknown", "price", "same_image", "balance", "inventory", "no_goods",
    "text", "box", "pending_hash", "claim_hash", "nonce", "case", "run", "runtime", "expired", "extra", "unrelated_time"])
def test_unproven_effect_or_mismatched_identity_never_archives(prepared, failure):
    request = deepcopy(prepared.request)
    effect = request["effect"]
    if failure == "unknown":
        effect["state"] = "UNKNOWN"
    elif failure == "price":
        effect["unit_price"] += 1
    elif failure == "same_image":
        effect["after"]["png_sha256"] = effect["before"]["png_sha256"]
    elif failure == "balance":
        effect["after"]["balance"] = number(99)
    elif failure == "inventory":
        effect["after"]["inventory"] = number(99)
    elif failure == "no_goods":
        for frame in (effect["before"], effect["after"]):
            frame["inventory"] = frame["remaining_limit"] = None
        effect["inventory_units_per_purchase"] = None
    elif failure == "text":
        effect["after"]["balance"]["text"] = "another number"
    elif failure == "box":
        effect["after"]["balance"]["box"] = [29, 19, 5, 5]
    elif failure == "pending_hash":
        request["pending_sha256"] = "0" * 64
    elif failure == "claim_hash":
        request["claim_sha256"] = "0" * 64
    elif failure == "nonce":
        request["reservation"]["source_nonce"] = "0" * 32
    elif failure == "case":
        source = json.loads(request["reservation"]["source_envelope"]["message"])
        source["case_id"] = "D13"
        request["reservation"]["source_envelope"]["message"] = contract.canonical(source).decode()
    elif failure == "run":
        request["maintenance_identity"]["run_nonce"] = "0" * 32
    elif failure == "runtime":
        request["runtime"]["native_generation"] += 1
    elif failure == "expired":
        request["expires_boottime_ms"] = prepared.f.clock()
    elif failure == "unrelated_time":
        request["reservation"]["host_received_at"] = "2026-10-26T21:00:00+08:00"
    else:
        request["forged_confirmation"] = True
    prepared.f.write("purchase-reconcile.json", request)
    assert reconcile(prepared).success is False
    assert not (prepared.f.paths.marker_root / "reconciled.done").exists()
    blocked(prepared)


@pytest.mark.parametrize("filename", ["reconcile.claim", "reconciled.json", "reconciled.done"])
def test_partial_archive_write_stays_blocked(prepared, monkeypatch, filename):
    real = commit.PendingStore.write_durable

    def fail(path, raw):
        if path.name == filename:
            path.write_bytes(raw[:3])
            raise OSError("injected interrupted write")
        return real(path, raw)

    monkeypatch.setattr(commit.PendingStore, "write_durable", staticmethod(fail))
    assert reconcile(prepared).success is False
    blocked(prepared)
    assert commit.read_object(prepared.f.paths.marker_root / "pending.json") == prepared.pending


def test_stop_after_receipt_before_witness_stays_blocked(prepared, monkeypatch):
    real = commit.PendingStore.write_durable

    def stop(path, raw):
        real(path, raw)
        if path.name == "reconciled.json":
            prepared.f.context.tasker.stopping = True

    monkeypatch.setattr(commit.PendingStore, "write_durable", staticmethod(stop))
    assert reconcile(prepared).success is False
    assert not (prepared.f.paths.marker_root / "reconciled.done").exists()
    blocked(prepared)


def test_concurrent_boundary_denies_reconciliation_without_mutations(prepared):
    with commit.PendingStore(prepared.f.paths.marker_root).boundary(allow_pending=True):
        assert reconcile(prepared).success is False
    assert not (prepared.f.paths.marker_root / "reconcile.claim").exists()
    blocked(prepared)


def test_normal_mode_cannot_reconcile(prepared, monkeypatch):
    monkeypatch.setattr(action, "AUDIT_CONFIG", AuditConfig())
    assert reconcile(prepared).success is False
    blocked(prepared)


def test_native_short_task_reconciliation_calls_no_game_input(prepared, tmp_path):
    import jsonc
    from pathlib import Path
    from maa.resource import Resource
    from maa.tasker import Tasker
    from test_audit_purchase_native import MemoryController

    class CountingController(MemoryController):
        def __init__(self):
            super().__init__()
            self.inputs = []
            for name in ("click", "swipe", "touch_down", "touch_move", "touch_up", "click_key", "input_text",
                         "key_down", "key_up", "start_app", "stop_app"):
                setattr(self, name, lambda *args, name=name: self.inputs.append((name, args)) or False)

    assert Tasker.set_log_dir(tmp_path / "native-reconcile-logs")
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    Tasker.set_debug_mode(False)
    source = Path(__file__).resolve().parents[2] / "resource/base/pipeline/Audit_Purchase.jsonc"
    definitions = jsonc.loads(source.read_text(encoding="utf-8"))
    definitions[action.NODE]["attach"] = {"audit_run": prepared.f.binding}
    pipeline = tmp_path / "maintenance-bundle/pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "purchase.json").write_text(json.dumps(definitions, ensure_ascii=False), encoding="utf-8")
    resource = Resource()
    assert resource.post_bundle(pipeline.parent).wait().succeeded
    callback = action.AuditPurchaseReconcile()
    assert resource.register_custom_action("AuditPurchaseReconcile", callback)
    controller = CountingController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        assert tasker.post_task(action.NODE).wait().succeeded
        assert controller.inputs == []
        assert (prepared.f.paths.marker_root / "reconciled.done").exists()
    finally:
        stop_and_wait_idle(tasker)


def test_native_lifecycle_waits_for_later_callback_stop_after_outer_job(monkeypatch):
    from test_support import native_lifecycle

    progress = SimpleNamespace(phase=0, joined=False)

    class Tasker:
        def post_stop(self):
            return SimpleNamespace(wait=lambda: setattr(progress, "joined", True))

        @property
        def running(self):
            return progress.phase == 0

        @property
        def stopping(self):
            return progress.phase == 1

    monkeypatch.setattr(native_lifecycle, "time", SimpleNamespace(
        monotonic=lambda: progress.phase / 100,
        sleep=lambda _: setattr(progress, "phase", progress.phase + 1)))
    native_lifecycle.stop_and_wait_idle(Tasker())
    assert progress.joined is True and progress.phase == 2
