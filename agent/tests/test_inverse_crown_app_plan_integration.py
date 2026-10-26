"""Opt-in actual App -> native integration, with no fake option compiler.

A clean checkout skips this test. To run it, explicitly provide the pinned
App's exported RuntimeTask arrays, source receipt and actual App input binding
via the environment. The App input binding is a path-to-SHA256 map from the
same compile, supplied as MAA_PROFILE_APP_INPUT_BINDING with its digest in
MAA_PROFILE_APP_INPUT_SHA256.
All controller endpoints are memory-only; geometry preflight is bypassed solely
to exercise the unchanged strategy gate, never to accept a device layout.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from maa.custom_action import CustomAction
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

import test_inverse_crown_task_option as native


EXPORT_ENV = "MAA_PROFILE_APP_EXPORT"
SOURCE_ENV = "MAA_PROFILE_SOURCE_RECEIPT"
HASH_ENV = "MAA_PROFILE_APP_EXPORT_SHA256"
INPUT_ENV = "MAA_PROFILE_APP_INPUT_BINDING"
INPUT_HASH_ENV = "MAA_PROFILE_APP_INPUT_SHA256"
OUT_ENV = "MAA_PROFILE_APP_NATIVE_OUT"
READ_NODE = "ProfileOptionsReadOnly"
PREPARATION_NODE = "逆冕准备页开始"
APP_INPUTS = {
    "interface.json", "tasks/战斗辅助.json", "options/inverse_crown_profile.json",
    "i18n/zh_cn.json", "i18n/zh_tw.json", "i18n/en_us.json", "i18n/ja_jp.json",
}
VALID_VARIANTS = (
    "yes_explicit", "no_stale_child", "unset_stale_child", "yes_default_child",
    "yes_explicit_preparation", "no_stale_preparation", "unset_stale_preparation",
    "yes_default_preparation", "yes_explicit_takeover",
)
DANGERS = ("自动闪避后攻击", "战斗重启1")
SIBLINGS = ("跳过剧情", "未选人", "点击作战开始_速通", "使用体力", "A_使用体力")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _names(node):
    return [item if isinstance(item, str) else item["name"] for item in node["next"]]


def _terminal(tasker, job, seconds=3):
    # Wait/Running alone are not the original task's runtime-cache barrier.
    # Wait for the fixed SDK TaskDetail terminal status before another post.
    job.wait()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        detail = tasker.get_task_detail(job.job_id)
        if detail is not None and detail.status.done and not tasker.running:
            return detail
        time.sleep(.005)
    pytest.fail("original native TaskDetail/Running did not settle")


class _ReadContext(CustomAction):
    def __init__(self):
        super().__init__()
        self.rows = []

    def run(self, context, argv):
        self.rows.append({
            "called_node": argv.node_name,
            "entry": context.get_node_data("战斗辅助"),
            "battle": context.get_node_data("战斗中"),
            "preparation": context.get_node_data(PREPARATION_NODE),
            "siblings": {name: context.get_node_data(name) for name in SIBLINGS},
        })
        return CustomAction.RunResult(success=True)


def _quiet_modules(monkeypatch):
    quiet = lambda _name: SimpleNamespace(logger=native._QuietLog(), emit_audit=lambda *_: None)
    for module in (native.session, native.runner, native.recovery):
        monkeypatch.setattr(module, "LoggerComponent", quiet)
    monkeypatch.setattr(native.runner, "require_inverse_crown_layout", lambda _context: None)


def _resource():
    resource = Resource()
    assert resource.post_bundle(native.ROOT / "resource/base").wait().succeeded
    return resource


def _tasker(resource):
    controller = native._MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    return tasker, controller


@pytest.mark.skipif(not os.environ.get(EXPORT_ENV), reason="requires actual pinned App RunPlanBuilder export")
def test_actual_app_arrays_reach_native_consumer_and_remain_task_local(tmp_path, monkeypatch):
    export_path = Path(os.environ[EXPORT_ENV])
    source_path = Path(os.environ[SOURCE_ENV])
    export_raw, source_raw = export_path.read_bytes(), source_path.read_bytes()
    assert _sha(export_raw) == os.environ[HASH_ENV]
    export, source = json.loads(export_raw), json.loads(source_raw)
    assert export["source_implementation_receipt_sha256"] == _sha(source_raw)
    input_raw = Path(os.environ[INPUT_ENV]).read_bytes()
    assert _sha(input_raw) == os.environ[INPUT_HASH_ENV]
    app_inputs = json.loads(input_raw)
    assert set(app_inputs) == APP_INPUTS
    for name, digest in app_inputs.items():
        assert _sha((native.ROOT / name).read_bytes()) == digest, name
    documentation_records = []
    for name, record in source["source_files"].items():
        # The implementation receipt also records prose, which is neither an
        # App compiler input nor a native runtime consumer. Keep the original
        # receipt/export hash chain, but do not require documentation to freeze
        # forever after a valid compile. All other recorded files remain pinned.
        if name.startswith("docs/"):
            documentation_records.append(name)
            continue
        assert _sha((native.ROOT / name).read_bytes()) == record["after"], name
    assert Library.version().lstrip("v") == "5.14.2"
    out = Path(os.environ.get(OUT_ENV, str(tmp_path / "native-result")))
    out.mkdir(parents=True, exist_ok=True)
    Tasker.set_log_dir(tmp_path / "native-logs")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    _quiet_modules(monkeypatch)

    valid = {case["case"]: case for case in export["cases"] if case["builder_result"] == "Success"}
    invalid = [case for case in export["cases"] if case["builder_result"] == "Invalid"]
    order = [f"{mode}_{variant}" for mode in ("auto", "continuous") for variant in VALID_VARIANTS]
    assert set(valid) == set(order) and len(invalid) == 10
    assert len(export["cases"]) == len(valid) + len(invalid)
    for case in invalid:
        # Invalid is no RuntimeTask, not an empty patch standing for a valid plan.
        assert case["pipeline_overrides"] is None and case["entry"] is None
        assert case["typed_profile"] is None
        assert any(item["severity"] == "Error" for item in case["diagnostics"])

    # Inspect actual effective node data in one real Tasker, without executing
    # ordinary-mode combat. Only a test-local read-only Custom entry is added.
    resource = _resource()
    reader = _ReadContext()
    assert resource.register_custom_action("ProfileOptionsInspect", reader)
    assert resource.override_pipeline({READ_NODE: {
        "recognition": "DirectHit",
        "action": {"type": "Custom", "param": {"custom_action": "ProfileOptionsInspect"}},
        "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0,
    }})
    tasker, controller = _tasker(resource)
    _terminal(tasker, tasker.post_task(READ_NODE, {}))
    pristine = deepcopy(reader.rows[-1])
    contexts = []
    for key in order:
        case = valid[key]
        # No fold/reorder/template substitution: pass the original App array.
        patches = deepcopy(case["pipeline_overrides"])
        detail = _terminal(tasker, tasker.post_task(READ_NODE, patches))
        observed = deepcopy(reader.rows[-1])
        profile = observed["battle"]["attach"]["inverse_crown_profile"]
        assert profile == case["typed_profile"]
        assert observed["preparation"]["attach"]["inverse_crown_profile"] == profile
        assert type(profile["enabled"]) is bool
        assert type(profile["signature_weapon_confirmed"]) is bool
        assert all(observed["siblings"][name]["enabled"] is False for name in SIBLINGS)
        if case["selection"] == "Yes":
            expected_consumer = PREPARATION_NODE if key.endswith("preparation") else "战斗中"
            assert _names(observed["entry"]) == [expected_consumer]
            assert _names(observed["battle"]) == []
            assert _names({"next": observed["entry"]["on_error"]}) == ["返回主菜单_err"]
        else:
            assert profile == {"enabled": False, "scene": "unknown", "source": "unknown",
                               "rank": "unknown", "signature_weapon_confirmed": False}
            if case["mode"] == "自动检测":
                expected = next(item for item in native.TASK["option"]["战斗模式"]["cases"]
                                if item["name"] == "自动检测")["pipeline_override"]["战斗辅助"]["next"]
                assert _names(observed["entry"]) == [item.removeprefix("[JumpBack]") for item in expected]
                assert all(item.get("jump_back") is True for item in observed["entry"]["next"])
            else:
                assert observed["entry"]["next"] == pristine["entry"]["next"]
        contexts.append({"case": key, "task_id": detail.task_id, "observed": observed,
                         "memory_inputs": list(controller.inputs)})
    _terminal(tasker, tasker.post_task(READ_NODE, {}))
    other = deepcopy(reader.rows[-1])
    assert other == pristine
    assert controller.inputs == []

    # Execute the ten actual Yes plans through the production CombatRunner
    # and ScopedRecovery. Each fresh runtime keeps recovery retention honest.
    routes = []
    for key in [name for name in order if valid[name]["selection"] == "Yes"]:
        case = valid[key]
        route_resource = _resource()
        recognizer = native._MatchBattleAndDanger()
        assert route_resource.register_custom_recognition("ProfileMatchFixture", recognizer)
        combat = native._ObserveRunner()
        fallback = native.recovery.ScopedRecovery()
        assert route_resource.register_custom_action("CombatRunner", combat)
        assert route_resource.register_custom_action("ScopedRecovery", fallback)
        route_tasker, route_controller = _tasker(route_resource)
        fixture = {name: {"recognition": {"type": "Custom", "param": {
            "custom_recognition": "ProfileMatchFixture"}}, "pre_delay": 0, "post_delay": 0,
            "pre_wait_freezes": {"time": 0}, "post_wait_freezes": {"time": 0}, "rate_limit": 0}
            for name in ("战斗中", PREPARATION_NODE, *DANGERS, "检查主界面2")}
        fixture["战斗辅助"] = {"pre_delay": 0, "post_delay": 0}
        # The fixture supplies recognition endpoints only. It precedes the
        # untouched App patch array so actual option precedence still wins.
        patches = [fixture, *deepcopy(case["pipeline_overrides"])]
        detail = _terminal(route_tasker, route_tasker.post_task(case["entry"], patches))
        names = [route_tasker.get_node_detail(node_id).name for node_id in detail.node_id_list]
        assert combat.profile == case["typed_profile"]
        assert combat.dangers_match == [True, True]
        assert combat.actual.last_outcome.exit_reason == "strategy_not_ready"
        assert combat.actual.last_outcome.callback_status == "REJECTED"
        expected_consumer = PREPARATION_NODE if key.endswith("preparation") else "战斗中"
        assert names[:2] == ["战斗辅助", expected_consumer]
        assert "返回主菜单_err" in names
        assert not {*DANGERS, "攻击"} & set(names)
        assert fallback.last_result["business_success"] is False
        assert route_controller.inputs == [] and route_controller.captures >= 1
        routes.append({"case": key, "task_id": detail.task_id, "node_names": names,
                       "profile": combat.profile, "danger_recognition_hit": combat.dangers_match,
                       "combat_outcome": combat.actual.last_outcome.to_event(),
                       "recovery": fallback.last_result, "captures": route_controller.captures,
                       "memory_inputs": list(route_controller.inputs)})

    result = {
        "status": "ACTUAL_APP_EXPORT_TO_REAL_NATIVE_VERIFIED_MEMORY_ONLY",
        "app_export_sha256": _sha(export_raw), "source_receipt_sha256": _sha(source_raw),
        "app_input_binding_sha256": _sha(input_raw), "app_input_files": app_inputs,
        "non_runtime_documentation_records": documentation_records,
        "maafw": Library.version(), "success_plans_context_inspected": len(contexts),
        "yes_plans_executed": len(routes), "invalid_plans_not_dispatched": len(invalid),
        "native_post_task_count": 2 + len(contexts) + len(routes),
        "invalid_plan_post_task_count": 0,
        "same_tasker_order": order, "same_tasker_contexts": contexts,
        "unrelated_no_override_context_equals_pristine": other == pristine,
        "production_yes_routes": routes,
        "page_recognition": "memory endpoint; actual production outer actions remain unchanged",
        "layout_preflight": "bypassed solely to reach unchanged strategy_not_ready gate",
        "ordinary_mode_combat_execution": "NOT_RUN; No/unset effective nodes inspected without combat",
        "device_operations": 0, "ui_game_acceptance": "NOT_RUN", "apk_changed": False,
    }
    (out / "actual-app-native-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
