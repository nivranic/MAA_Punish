"""Exercise installed MaaFramework APIs with an in-memory CustomController only.

No OS input, ADB, phone or game is involved. This verifies native rectangle and
result semantics, not delivery to a device or purchase effects.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonc
import numpy as np
import pytest
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker

import audit_commit
from audit_config import AuditConfig
from action.basics import AuditPurchase as purchase
from test_support.native_lifecycle import stop_and_wait_idle


class MemoryController(CustomController):
    def __init__(self):
        self.clicks = []
        self.accept = True
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return "offline-purchase-native-test"
    def get_features(self): return 0
    def start_app(self, intent): return False
    def stop_app(self, intent): return False
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def click(self, x, y):
        self.clicks.append((x, y))
        return self.accept
    def swipe(self, *args): return False
    def touch_down(self, *args): return False
    def touch_move(self, *args): return False
    def touch_up(self, *args): return False
    def click_key(self, *args): return False
    def input_text(self, *args): return False
    def key_down(self, *args): return False
    def key_up(self, *args): return False


@pytest.mark.parametrize("target,offset,expected", [
    (True, [13, 17, 5, 7], (213, 117, 45, 27)),
    ([500, 200, 60, 40], [5, 9, 1, 2], (505, 209, 61, 42)),
    ([650, 300], [0, 0, 0, 0], (650, 300, 1, 1)),
])
def test_native_click_rectangle_offset_randomization_and_result(tmp_path, monkeypatch, target, offset, expected):
    assert Tasker.set_log_dir(tmp_path / "native-logs")
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    Tasker.set_debug_mode(False)
    monkeypatch.setattr(purchase, "LoggerComponent", lambda _: SimpleNamespace(logger=SimpleNamespace(error=lambda *args: None)))
    real_gate = purchase.PurchaseCommit
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda logger: real_gate(logger, config=AuditConfig(),
                         paths=audit_commit.RuntimePaths(tmp_path / "runtime")))
    observed_boxes = []
    callback_results = []
    class CapturingPurchase(purchase.AuditPurchase):
        def run(self, context, argv):
            observed_boxes.append(tuple(argv.box))
            result = super().run(context, argv)
            callback_results.append(result.success)
            return result
    native = {"recognition": {"type": "DirectHit", "param": {"roi": [200, 100, 40, 20]}},
              "action": {"type": "Click", "param": {"target": target, "target_offset": offset}},
              "pre_delay": 0, "post_delay": 0, "next": []}
    guarded = dict(native, action={"type": "Custom", "param": {
        "custom_action": "AuditPurchase", "target": target, "target_offset": offset}})
    pipeline = tmp_path / "bundle" / "pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "purchase.json").write_text(json.dumps({"baseline": native, "购买": guarded}, ensure_ascii=False), encoding="utf-8")
    resource = Resource()
    assert resource.post_bundle(pipeline.parent).wait().succeeded
    action = CapturingPurchase()
    assert resource.register_custom_action("AuditPurchase", action)
    controller = MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        results = {}
        for entry in ("baseline", "购买"):
            start = len(controller.clicks)
            for _ in range(8):
                assert tasker.post_task(entry).wait().succeeded
            results[entry] = controller.clicks[start:]
        assert observed_boxes == [expected] * 8
        x, y, width, height = expected
        for samples in results.values():
            assert len(samples) == 8
            assert all(x <= px <= x + max(0, width - 1) and y <= py <= y + max(0, height - 1)
                       for px, py in samples)
            if width > 1 and height > 1:
                assert len(set(samples)) > 1  # Still native random points, never a fixed center.
        # A failed native Controller action must stay failed through the wrapper.
        controller.accept = False
        assert tasker.post_task("购买").wait().failed
        assert observed_boxes[-1] == expected
        controller.accept = True
        inputs_before = len(controller.clicks)
        monkeypatch.setattr(purchase, "PurchaseCommit", lambda logger: real_gate(logger,
                             config=AuditConfig(True, "a" * 32, "b" * 32),
                             paths=audit_commit.RuntimePaths(tmp_path / "runtime")))
        tasker.post_task("购买").wait()
        # The callback's stop may be queued after the outer stop: drain both.
        stop_and_wait_idle(tasker)
        assert callback_results[-1] is False
        assert len(controller.clicks) == inputs_before
    finally:
        stop_and_wait_idle(tasker)


def merged_purchase_nodes(language, case_index):
    """Read real resource/option order without dispatching any game task."""
    root = Path(__file__).resolve().parents[2]
    resource = Resource()
    for suffix in ("Buy_Item.jsonc", "Dorm/Dorm_Shop.jsonc"):
        path = root / "resource/base/pipeline" / suffix
        assert resource.override_pipeline(jsonc.loads(path.read_text(encoding="utf-8")))
    if language != "base":
        for suffix in ("Buy_Item.jsonc", "Dorm/Dorm_Shop.jsonc"):
            path = root / "resource" / language / "pipeline" / suffix
            if path.exists():
                assert resource.override_pipeline(jsonc.loads(path.read_text(encoding="utf-8")))
    definition = json.loads((root / "tasks/购买物品.json").read_text(encoding="utf-8"))
    case = definition["option"]["选择物品"]["cases"][case_index]
    assert resource.override_pipeline(case["pipeline_override"])
    assert resource.override_pipeline({name: {"attach": {"audit_run": {"enabled": True}}}
                                       for name in ("购买", "购买_宿舍", "点击购买_宿舍")})
    return {name: resource.get_node_data(name) for name in
            ("购买物品", "购买", "购买_宿舍", "点击购买_宿舍")}


@pytest.mark.parametrize("language", ["base", "zh_TW", "ja_JP", "en_US"])
@pytest.mark.parametrize("case_index", [0, 1, 2], ids=["D12", "D13", "D14"])
def test_actual_merged_purchase_options_cannot_restore_unguarded_click(tmp_path, language, case_index):
    assert Tasker.set_log_dir(tmp_path / "native-merge-logs")
    nodes = merged_purchase_nodes(language, case_index)
    for name in ("购买", "点击购买_宿舍"):
        assert nodes[name]["action"]["type"] == "Custom"
        assert nodes[name]["action"]["param"]["custom_action"] == "AuditPurchase"
    first = nodes["购买_宿舍"]["action"]
    if case_index == 2:
        assert first["type"] == "Custom"
        assert first["param"]["custom_action"] == "AuditPurchase"
        assert first["param"]["target"] == [643, 671, 1, 1]
        assert nodes["购买_宿舍"]["attach"]["audit_quantity_role"] == {"case_id": "D14", "role": "skip_max_in_audit"}
        assert nodes["购买_宿舍"]["recognition"]["param"]["all_of"] == ["购买_宿舍_武器涂装设计图"]
        assert nodes["点击购买_宿舍"]["recognition"]["param"]["all_of"] == ["识别宿舍购买按钮", "识别D14购买意图"]
    else:
        assert first["type"] == "DoNothing"
    assert [item["name"] for item in nodes["购买_宿舍"]["next"]] == ["错误购买", "点击购买_宿舍"]


@pytest.mark.parametrize("case_index,entry", [(1, "点击购买_宿舍"), (2, "购买_宿舍")], ids=["D13", "D14-first-click"])
def test_actual_dorm_targets_preserve_normal_input_and_block_audit_and_pending(tmp_path, monkeypatch, case_index, entry):
    assert Tasker.set_log_dir(tmp_path / "native-logs")
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    Tasker.set_debug_mode(False)
    action = merged_purchase_nodes("base", case_index)[entry]["action"]
    baseline = deepcopy(action)
    baseline["type"] = "Click"
    baseline["param"].pop("custom_action", None)
    baseline["param"].pop("custom_action_param", None)
    recognition = {"type": "DirectHit", "param": {"roi": [200, 100, 40, 20]}}
    node = {"recognition": recognition, "action": action, "pre_delay": 0, "post_delay": 0, "next": []}
    # Only recognition/terminal next are a fixture. Action parameters are the
    # actual merged resource values, including the D14 explicit target.
    pipeline = tmp_path / "bundle/pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "purchase.json").write_text(json.dumps({entry: node, "baseline": dict(node, action=baseline)}, ensure_ascii=False), encoding="utf-8")
    resource = Resource()
    assert resource.post_bundle(pipeline.parent).wait().succeeded
    errors = []
    monkeypatch.setattr(purchase, "LoggerComponent", lambda _: SimpleNamespace(logger=SimpleNamespace(error=lambda *args: errors.append(args))))
    config = AuditConfig()
    paths = audit_commit.RuntimePaths(tmp_path / "runtime")
    real_gate = purchase.PurchaseCommit
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda logger: real_gate(logger, config=config, paths=paths))
    callback_results = []

    class CapturingPurchase(purchase.AuditPurchase):
        def run(self, context, argv):
            result = super().run(context, argv)
            callback_results.append(result.success)
            return result

    callback = CapturingPurchase()
    assert resource.register_custom_action("AuditPurchase", callback)
    controller = MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        samples = {}
        for name in ("baseline", entry):
            first = len(controller.clicks)
            for _ in range(5):
                assert tasker.post_task(name).wait().succeeded
            samples[name] = controller.clicks[first:]
        expected = (643, 671, 1, 1) if case_index == 2 else (200, 100, 40, 20)
        x, y, width, height = expected
        for values in samples.values():
            assert len(values) == 5
            assert all(x <= px <= x + width - 1 and y <= py <= y + height - 1 for px, py in values)
            if width > 1:
                assert len(set(values)) > 1
        assert callback_results == [True] * 5
        controller.accept = False
        assert tasker.post_task(entry).wait().failed
        assert callback_results[-1] is False
        controller.accept = True
        before = len(controller.clicks)
        config = AuditConfig(True, "a" * 32, "b" * 32)
        tasker.post_task(entry).wait()
        stop_and_wait_idle(tasker)
        assert len(controller.clicks) == before and callback_results[-1] is False
        assert str(errors[-1][-1]) == "unexpected_fields"
        config = AuditConfig()
        paths.marker_root.mkdir(exist_ok=True)
        (paths.marker_root / "pending.claim").write_bytes(b"pending_unconfirmed\n")
        tasker.post_task(entry).wait()
        stop_and_wait_idle(tasker)
        assert len(controller.clicks) == before and callback_results[-1] is False
        assert str(errors[-1][-1]) == "pending_unconfirmed"
    finally:
        # The callback posts stop but cannot wait on itself. The test's outer
        # owner joins every terminal stop before native handles are destroyed.
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("case_index,entry,case_id", [
    (0, "购买", "D12"), (1, "点击购买_宿舍", "D13"), (2, "点击购买_宿舍", "D14"),
])
def test_native_and_neutral_observation_preserves_button_box_and_uncalibrated_audit_rejects(
        tmp_path, monkeypatch, case_index, entry, case_id):
    from recognition.exclusives import PurchaseObservation as observation

    assert Tasker.set_log_dir(tmp_path / "and-native-logs")
    node = deepcopy(merged_purchase_nodes("base", case_index)[entry])
    node.update(next=[], on_error=[], pre_delay=0, post_delay=0, timeout=100)
    node["attach"] = {"audit_run": {"enabled": False}}
    case = audit_commit.PURCHASE_CASES[case_id]
    definitions = {entry: node}
    for name in node["recognition"]["param"]["all_of"]:
        if name == case.observation_node:
            definitions[name] = {"recognition": {"type": "Custom", "param": {
                "custom_recognition": case.observation_action}}}
        else:
            # Only fields are synthetic. The And ordering, box_index and action
            # come from the real merged consumer. No OCR acceptance is claimed.
            definitions[name] = {"recognition": {"type": "DirectHit", "param": {
                "roi": [200, 100, 40, 20]}}}
    pipeline = tmp_path / "and-bundle/pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "purchase.json").write_text(json.dumps(definitions, ensure_ascii=False), encoding="utf-8")
    resource = Resource()
    assert resource.post_bundle(pipeline.parent).wait().succeeded
    monkeypatch.setattr(observation, "AUDIT_CONFIG", AuditConfig())
    monkeypatch.setattr(purchase, "LoggerComponent", lambda _: SimpleNamespace(logger=SimpleNamespace(error=lambda *args: None)))
    real_gate = purchase.PurchaseCommit
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda logger: real_gate(
        logger, config=AuditConfig(), paths=audit_commit.RuntimePaths(tmp_path / "and-runtime")))
    action = purchase.AuditPurchase()
    producer = getattr(observation, case.observation_action)()
    assert resource.register_custom_action("AuditPurchase", action)
    assert resource.register_custom_recognition(case.observation_action, producer)
    controller = MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        assert tasker.post_task(entry).wait().succeeded
        expected = (200, 100, 40, 20)
        x, y, width, height = expected
        assert len(controller.clicks) == 1
        px, py = controller.clicks[0]
        assert x <= px < x + width and y <= py < y + height
        monkeypatch.setattr(observation, "AUDIT_CONFIG", AuditConfig(True, "a" * 32, "b" * 32))
        before = len(controller.clicks)
        assert tasker.post_task(entry).wait().failed
        assert len(controller.clicks) == before
    finally:
        stop_and_wait_idle(tasker)
