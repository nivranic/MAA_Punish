"""Reject a stale daily admission at the final purchase action, not just mark."""

import datetime as dt
import json

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

import audit_commit
from audit_config import AuditConfig
from action.basics import AuditPurchase as purchase, daily_progress as dp
from recognition.exclusives import PurchaseObservation as observation
from test_audit_purchase_native import MemoryController
from test_daily_activity_native import ROOT, native_resource
from test_support.daily_progress import register_daily_progress
from test_support.native_lifecycle import stop_and_wait_idle


FAST = {"pre_delay": 0, "post_delay": 0, "timeout": 400, "rate_limit": 10}
ENTRY, SELECT = "购买物品", "购买_宿舍"
BUTTON = {"D12": "识别购买界面购买", "D13": "识别宿舍购买按钮", "D14": "识别宿舍购买按钮"}
SHOP = {"D13": "在宿舍币商店中", "D14": "遣测商店内部"}
GUARD = {"D12": "日常断点_购买物品_逆元碎片", "D13": "日常断点_购买物品_宿舍", "D14": "日常断点_购买物品_宿舍"}
BEFORE, AFTER = dt.datetime(2026, 11, 25, 4, 59, 59), dt.datetime(2026, 11, 25, 5, 0, 1)
CONFIRM_POINT, MAX_POINT = (761, 668), (643, 671)


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("case_index, case_id, rollover", [
    (0, "D12", None), (1, "D13", None), (2, "D14", None),
    (0, "D12", "submit"), (1, "D13", "submit"), (2, "D14", "submit"),
    (2, "D14", "max"),
], ids=["D12-same-day", "D13-same-day", "D14-same-day", "D12-before-submit", "D13-before-submit",
        "D14-before-submit", "D14-before-max"])
def test_daily_purchase_rechecks_05_before_native_click(tmp_path, monkeypatch, language, case_index, case_id, rollover):
    ledger = tmp_path / "ledger.json"
    clock = [BEFORE]
    reached = []
    monkeypatch.setattr(dp, "_now", lambda: clock[0])
    monkeypatch.setattr(observation, "AUDIT_CONFIG", AuditConfig())
    real_gate = purchase.PurchaseCommit
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda log: real_gate(
        log, config=AuditConfig(), paths=audit_commit.RuntimePaths(tmp_path / "runtime")))
    case = audit_commit.PURCHASE_CASES[case_id]
    button = BUTTON[case_id]

    class Offer(CustomRecognition):
        def analyze(self, context, argv):
            reached.append(argv.node_name)
            if (argv.node_name == button and rollover == "submit") or (
                argv.node_name == "购买_宿舍_武器涂装设计图" and rollover == "max"
            ):
                # The actual work guard has already admitted the old day. At
                # the final D14 button, MAX has already been acknowledged.
                clock[0] = AFTER
            return (*CONFIRM_POINT, 1, 1)

    resource = native_resource(tmp_path, language)
    register_daily_progress(resource, monkeypatch, ledger)
    assert resource.register_custom_action("AuditPurchase", purchase.AuditPurchase())
    assert resource.register_custom_recognition(case.observation_action, getattr(observation, case.observation_action)())
    assert resource.register_custom_recognition("SyntheticPurchaseOffer", Offer())
    controller, tasker = MemoryController(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    pi = json.loads((ROOT / "tasks/购买物品.json").read_text(encoding="utf-8"))
    option = pi["option"]["选择物品"]["cases"][case_index]["pipeline_override"]
    # The first candidate is a controlled available offer. Keep real admission,
    # option routing, D14 MAX, production And/box and guarded final action.
    base = {name: dict(FAST) for name in (ENTRY, dp.ADMIT_NODE, GUARD[case_id], SELECT, case.action_node)}
    base[case.action_node].update(next=[], on_error=[])
    base[button] = {"recognition": {"type": "Custom", "param": {"custom_recognition": "SyntheticPurchaseOffer"}}}
    # Synthetic page leaves and no purchase outcome: cut the result tail
    # intentionally so this test cannot be read as approval to mark done on ACK.
    if case_id == "D12":
        base["识别购买界面逆元碎片"] = {"recognition": "DirectHit"}
    else:
        base[SHOP[case_id]] = {**FAST, "recognition": "DirectHit", "next": [SELECT]}
        base["购买_宿舍_涂装设计草稿"] = {"recognition": "DirectHit"}
        base["购买_宿舍_武器涂装设计图"] = base[button] if rollover == "max" else {"recognition": "DirectHit"}
        base["错误购买"] = {"enabled": False}
    # App's normal mode attaches an explicit disabled audit binding.
    audit_off = {name: {"attach": {"audit_run": {"enabled": False}}} for name in (case.action_node, SELECT)}
    try:
        job = tasker.post_task(ENTRY, [base, option, audit_off]).wait()
        nodes = [tasker.get_node_detail(node_id) for node_id in job.get().node_id_list]
        assert nodes[1].name == dp.ADMIT_NODE and nodes[1].completed
        assert nodes[2].name == GUARD[case_id] and nodes[2].completed
        assert reached == (["购买_宿舍_武器涂装设计图"] if rollover == "max" else [button])
        expected = [MAX_POINT] if case_id == "D14" and rollover != "max" else []
        if rollover is None:
            expected.append(CONFIRM_POINT)
        assert controller.clicks == expected
        if rollover == "max":
            # Native recovery can end the Job as successful even though MAX
            # was refused. Check the action detail, not Job.succeeded.
            assert [node.completed for node in nodes if node.name == SELECT] == [False]
            assert not any(node.name == case.action_node for node in nodes)
        else:
            assert job.succeeded is (rollover is None)
        assert not ledger.exists()
        # A scoped refusal must not poison later native tasks.
        assert tasker.post_task("购买日切回归_无输入后续", {
            "购买日切回归_无输入后续": {**FAST, "action": "DoNothing", "next": [], "on_error": []},
        }).wait().succeeded
        assert controller.clicks == expected
    finally:
        stop_and_wait_idle(tasker)
