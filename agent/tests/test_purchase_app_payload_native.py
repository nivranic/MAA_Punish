"""Consume actual JVM App compiler exports as native override arrays.

Set MAA_PURCHASE_APP_EXPORTS to the ignored producer evidence directory. The PI
envelope, session and controller are synthetic; compiler arrays stay unchanged.
No ADB, Android process, OS input or real purchase is involved.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest
from maa.resource import Resource
from maa.tasker import Tasker

import audit_commit as commit
from audit_config import AuditConfig
from action.basics import AuditPurchase as purchase
from recognition.exclusives import PurchaseObservation as observation
from test_audit_purchase import Fixture
from test_audit_purchase_native import MemoryController
from test_purchase_calibration_native import ROOT, POLICIES, page
from test_support.native_lifecycle import stop_and_wait_idle


MODES = ["default", "unset", "no-option-declaration", "malformed-intent", "forged-identity", "normal-no"]


def exported_root():
    configured = os.environ.get("MAA_PURCHASE_APP_EXPORTS")
    if not configured:
        pytest.skip("Set MAA_PURCHASE_APP_EXPORTS to actual JVM compiler exports")
    return Path(configured)


def consume(tmp_path, monkeypatch, case, mode, *, no_variant=False, original_segment=False):
    base = exported_root()
    folder = base / ("original-D14" if original_segment else case + "/" + mode)
    name = "runtime-payload-no.json" if no_variant else "runtime-payload.json"
    payload_path = folder / name
    assert payload_path.is_file(), f"actual App payload is required: {payload_path}"
    payload_bytes = payload_path.read_bytes()
    payload = json.loads(payload_bytes)
    binding = json.loads((folder / "input-binding.json").read_text(encoding="utf-8"))
    task = payload["tasks"][0]
    assert len(payload["tasks"]) == 1
    if not original_segment:
        assert task["taskName"] == "Screencap" and task["entry"] == commit.PURCHASE_CASES[case].action_node
        candidate = Path(binding["candidate_path"])
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == binding["candidate_sha256"]
    patches = deepcopy(task["pipelineOverrides"])
    authoritative = patches[-1][commit.PURCHASE_CASES[case].action_node]["attach"]["audit_run"]
    enabled = payload["piEnv"].get("MAA_PUNISH_AUDIT_LOG_MODE") == "stdout"
    f = Fixture(tmp_path)
    if enabled:
        assert authoritative == binding["audit_run"] and authoritative["enabled"] is True
        identity = {key: authoritative[key] for key in commit.IDENTITY_KEYS}
        assert payload["piEnv"]["MAA_PUNISH_AUDIT_SESSION_ID"] == identity["session_id"]
        mailbox = Path(authoritative["mailbox_root"])
        assert mailbox.is_relative_to(base.resolve()) and mailbox.name == ".dev-session"
        mailbox.mkdir(parents=True, exist_ok=True)
        assert not (mailbox.parent / ".audit-commit").exists(), "each real export requires an unused mock runtime"
        f.mailbox = mailbox
        f.paths = commit.RuntimePaths(mailbox.parent / ".audit-commit", mailbox.parent, identity["package"])
        f.config = AuditConfig(True, identity["session_id"], "d" * 32)
        f.challenge.update(identity)
        f.write("audit-permit.json", f.challenge)
        f.write("audit-state.json", dict(identity, version=1, phase="running",
            started_boottime_ms=500, observed_boottime_ms=1000, audit_persist_supported=True))
    else:
        assert authoritative == {"enabled": False} and payload["piEnv"] == {}
        f.config = AuditConfig()
    f.emitter.callback = f.ack
    bundle = Path(os.environ.get("MAA_PURCHASE_OCR_RESOURCE", ROOT / "resource/base"))
    assert (bundle / "model/ocr/rec.onnx").is_file(), "actual OCR bundle is required"
    assert Tasker.set_log_dir(tmp_path / "native-logs")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    monkeypatch.setattr(observation, "AUDIT_CONFIG", f.config)
    monkeypatch.setattr(observation, "runtime_paths", lambda: f.paths)
    monkeypatch.setattr(observation, "boottime_ms", f.clock)
    real_guard = commit.PurchaseCommit
    monkeypatch.setattr(observation, "PurchaseCommit", lambda *_, **__: real_guard(None,
        config=f.config, paths=f.paths, clock=f.clock))
    monkeypatch.setattr(purchase, "LoggerComponent", lambda _: f.emitter)
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda _: f.gate())
    callbacks = []
    nodes = []

    class CapturingPurchase(purchase.AuditPurchase):
        def run(self, context, argv):
            nodes.append((argv.node_name, context.get_node_data(argv.node_name)))
            result = super().run(context, argv)
            callbacks.append(result.success)
            return result

    for key in ("D12", "D13", "D14"):
        assert resource.register_custom_recognition(key + "PurchaseObservation", getattr(observation, key + "PurchaseObservation")())
    assert resource.register_custom_action("AuditPurchase", CapturingPurchase())
    image = page(case, raw=True)
    controller = MemoryController()
    controller.screencap = lambda: image.copy()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        # Exactly the exported array order, no merging or patching. The original
        # D14 probe explicitly scopes its existing P1 segment, not navigation.
        entry = "购买_宿舍" if original_segment else task["entry"]
        tasker.post_task(entry, pipeline_override=patches).wait()
        stop_and_wait_idle(tasker)
        assert patches == task["pipelineOverrides"]
        expected = int(enabled and mode != "malformed-intent" and not original_segment)
        assert len(controller.clicks) == expected
        if expected:
            assert callbacks == [True]
            pending = commit.read_object(f.paths.marker_root / "pending.json")
            assert pending["case_id"] == case and pending["intent"] == POLICIES[case]
        else:
            assert controller.clicks == []
            assert not (f.paths.marker_root / "pending.claim").exists()
        if not enabled:
            assert callbacks == [False], "No must reach and deny the actual guarded consumer"
            assert "audit_intent" in nodes[-1][1]["attach"]
        if original_segment:
            assert callbacks == [True, False]
            assert nodes[0][1]["attach"]["audit_quantity_role"] == {"case_id": "D14", "role": "skip_max_in_audit"}
            assert nodes[0][1]["attach"]["audit_run"] == binding["audit_run"]
            assert nodes[-1][1]["recognition"]["param"]["all_of"] == ["识别宿舍购买按钮", "识别D14购买意图"]
        result = dict(case=case, mode=mode, no_variant=no_variant, original_p1_segment=original_segment,
            payload_sha256=hashlib.sha256(payload_bytes).hexdigest(), expected_memory_clicks=expected,
            memory_clicks=controller.clicks, custom_results=callbacks, adb_calls=0, os_input=0, actual_purchases=0)
        (tmp_path / "actual-app-sdk.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("case", ["D12", "D13", "D14"])
@pytest.mark.parametrize("mode", MODES)
def test_actual_app_compiled_arrays_reach_real_guard_without_identity_rewrite(tmp_path, monkeypatch, case, mode):
    consume(tmp_path, monkeypatch, case, mode)


@pytest.mark.parametrize("case", ["D12", "D13", "D14"])
@pytest.mark.parametrize("mode", ["default", "unset", "no-option-declaration", "malformed-intent", "forged-identity"])
def test_actual_app_no_runtime_is_zero_click_for_every_option_branch(tmp_path, monkeypatch, case, mode):
    consume(tmp_path, monkeypatch, case, mode, no_variant=True)


def test_actual_original_d14_option_keeps_quantity_role_and_real_final_case(tmp_path, monkeypatch):
    consume(tmp_path, monkeypatch, "D14", "original", original_segment=True)


@pytest.mark.parametrize("case", ["D12", "D13", "D14"])
def test_actual_app_rejects_bad_input_without_emitting_any_dispatch_payload(case):
    directory = exported_root() / case / "bad-option-value"
    result = json.loads((directory / "compile-result.json").read_text(encoding="utf-8"))
    assert result["payload_emitted"] is False
    assert not (directory / "runtime-payload.json").exists()
