"""Real screenshot/ROI replay through SDK 5.14.2 and production And/guard.

No ADB, OS input or game is involved. Only an in-memory controller can click.
Public fixtures exclude the account/header. Optional raw pages remain ignored.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import jsonc
import numpy as np
import pytest

from maa.resource import Resource
from maa.tasker import Tasker

import audit_commit as commit
from audit_config import AuditConfig
from action.basics import AuditPurchase as purchase
from recognition.exclusives import PurchaseObservation as observation
from test_audit_purchase import Fixture
from test_audit_purchase_native import MemoryController
from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures/purchase_observation"
PROVENANCE = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
POLICIES = {
    "D12": dict(cycle_id="goal-20261026-purchase-acceptance", target="inverse_fragment_mingjue",
                currency="cogs", unit_price=20000, quantity=1, budget_amount=20000, budget_quantity=1),
    "D13": dict(cycle_id="goal-20261026-purchase-acceptance", target="coating_design_draft",
                currency="dorm_coins", unit_price=3, quantity=1, budget_amount=3, budget_quantity=1),
    "D14": dict(cycle_id="goal-20261026-purchase-acceptance", target="weapon_coating_blueprint",
                currency="dispatch_voucher", unit_price=4500, quantity=1, budget_amount=4500, budget_quantity=1),
}


def page(case, kind="P1", *, raw=False):
    record = PROVENANCE["images"][case + "-" + kind]
    if raw:
        source = os.environ.get("MAA_PURCHASE_RAW_PAGES")
        if not source:
            pytest.skip("Set MAA_PURCHASE_RAW_PAGES for ignored original full-page replay")
        raw_bytes = (Path(source) / record["original_filename"]).read_bytes()
        assert hashlib.sha256(raw_bytes).hexdigest() == record["original_png_sha256"]
        return cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    image = np.zeros(record["shape"], dtype=np.uint8)
    for key, (x, y, w, h) in PROVENANCE["rects"].items():
        crop_bytes = (FIXTURES / record["crops"][key]["filename"]).read_bytes()
        assert hashlib.sha256(crop_bytes).hexdigest() == record["crops"][key]["sha256"]
        crop = cv2.imdecode(np.frombuffer(crop_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert crop.shape == (h, w, 3)
        image[y:y+h, x:x+w] = crop
    return image


def replay(tmp_path, monkeypatch, case, image, change=None):
    bundle = Path(os.environ.get("MAA_PURCHASE_OCR_RESOURCE", ROOT / "resource/base"))
    if not (bundle / "model/ocr/rec.onnx").is_file():
        if "MAA_PURCHASE_OCR_RESOURCE" in os.environ:
            pytest.fail("MAA_PURCHASE_OCR_RESOURCE lacks OCR models")
        pytest.skip("Initialize OCR submodule or set MAA_PURCHASE_OCR_RESOURCE")
    assert Tasker.set_log_dir(tmp_path / "native-logs")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    f = Fixture(tmp_path)
    f.emitter.callback = f.ack
    entry = commit.PURCHASE_CASES[case].action_node
    intent = deepcopy(POLICIES[case])
    candidate = os.environ.get("MAA_PURCHASE_ONCE_ROOT")
    full_flow = change in {"audit_full_flow", "normal_full_flow"}
    candidate_override = None
    if candidate and not full_flow:
        adapter = json.loads((Path(candidate) / case / "source-overlay/tasks/Screencap.json").read_text(encoding="utf-8"))
        assert adapter["task"][0]["name"] == "Screencap" and adapter["task"][0]["entry"] == entry
        candidate_override = adapter["option"]["ScreenCaplimit"]["pipeline_override"]
        assert candidate_override[entry]["attach"] == {"audit_intent": {"case_id": case, "intent": POLICIES[case]}}
        assert candidate_override[entry]["max_hit"] == 1
        assert candidate_override[entry]["next"] == candidate_override[entry]["on_error"] == []
    attach = dict(audit_run=f.binding, audit_intent=dict(case_id=case, intent=intent))
    if change == "plan_price":
        intent["unit_price"] -= 1
    elif change == "old_d12_target":
        assert case == "D12"
        intent["target"] = "inverse_fragment_qingshang"
    elif change == "plan_quantity":
        intent.update(quantity=2, budget_quantity=2, budget_amount=2 * intent["unit_price"])
    elif change == "plan_currency":
        intent["currency"] = "unobserved_coin"
    elif change == "inactive_binding":
        attach["audit_run"] = {"enabled": False}
    elif change == "closed_state":
        state = commit.read_object(f.mailbox / "audit-state.json")
        f.write("audit-state.json", dict(state, phase="idle"))
    elif change == "old_session":
        attach["audit_run"] = dict(f.binding, session_id="0" * 32)
    elif change == "expired_permit":
        f.challenge["expires_boottime_ms"] = f.challenge["permit_expires_boottime_ms"] = f.clock()
        f.write("audit-permit.json", f.challenge)
    if change in {"normal_no", "normal_full_flow"}:
        f.config = AuditConfig()
        attach["audit_run"] = {"enabled": False}
        if change == "normal_full_flow":
            attach.pop("audit_intent")
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if case == "D14":
        original_task = json.loads((ROOT / "tasks/购买物品.json").read_text(encoding="utf-8"))
        original_override = original_task["option"]["选择物品"]["cases"][2]["pipeline_override"]
        assert resource.override_pipeline(original_override)
    # Candidate changes only the existing button OCR's ROI, not ordinary source.
    button = "识别购买界面购买" if case == "D12" else "识别宿舍购买按钮"
    if candidate_override is not None:
        assert resource.override_pipeline(candidate_override)
        assert candidate_override[button]["recognition"]["param"]["roi"] == [727, 647, 97, 40]
    elif not full_flow:
        assert resource.override_pipeline({button: {"recognition": {"type": "OCR", "param": {
            "roi": [727, 647, 97, 40], "expected": "^购买$", "only_rec": True, "threshold": 0.98}}}})
    # This fresh binding models App's final runtime-only audit_run override.
    assert resource.override_pipeline({entry: dict(attach=attach, next=[], on_error=[], pre_delay=0,
                                                   post_delay=0, timeout=100)})
    if full_flow:
        assert resource.override_pipeline({"购买_宿舍": {"attach": {"audit_run": attach["audit_run"]},
            "pre_delay": 0, "post_delay": 0, "on_error": [], "timeout": 100}})
    monkeypatch.setattr(observation, "AUDIT_CONFIG", f.config)
    monkeypatch.setattr(observation, "runtime_paths", lambda: f.paths)
    monkeypatch.setattr(observation, "boottime_ms", f.clock)
    real_guard = commit.PurchaseCommit
    monkeypatch.setattr(observation, "PurchaseCommit", lambda *_, **__: real_guard(None,
        config=f.config, paths=f.paths, clock=f.clock))
    monkeypatch.setattr(purchase, "LoggerComponent", lambda _: f.emitter)
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda _: f.gate())
    callback_results = []
    details = []

    class CapturingPurchase(purchase.AuditPurchase):
        def run(self, context, argv):
            details.append(argv.reco_detail)
            result = super().run(context, argv)
            callback_results.append(result.success)
            return result

    controller = MemoryController()
    controller.screencap = lambda: image.copy()
    assert controller.post_connection().wait().succeeded
    for key in ("D12", "D13", "D14"):
        cls = getattr(observation, key + "PurchaseObservation")
        assert resource.register_custom_recognition(key + "PurchaseObservation", cls())
    assert resource.register_custom_action("AuditPurchase", CapturingPurchase())
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        result = tasker.post_task("购买_宿舍" if full_flow else entry).wait()
        stop_and_wait_idle(tasker)
        return SimpleNamespace(clicks=list(controller.clicks), callback_results=callback_results,
                               details=details, f=f, succeeded=result.succeeded)
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("raw", [False, True], ids=["public-roi", "ignored-original"])
@pytest.mark.parametrize("case", ["D12", "D13", "D14"])
def test_real_page_production_and_observation_and_guard_can_commit_once_in_memory(tmp_path, monkeypatch, case, raw):
    result = replay(tmp_path, monkeypatch, case, page(case, raw=raw))
    assert result.callback_results == [True]
    assert len(result.clicks) == 1
    x, y = result.clicks[0]
    assert 727 <= x < 824 and 647 <= y < 687
    pending = commit.read_object(result.f.paths.marker_root / "pending.json")
    assert pending["intent"] == POLICIES[case]
    assert pending["observation"]["quantity"] == 1
    assert pending["observation"]["unit_price"] == POLICIES[case]["unit_price"]


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("case", ["D12", "D13", "D14"])
def test_real_other_product_never_produces_a_purchase(tmp_path, monkeypatch, case, raw):
    result = replay(tmp_path, monkeypatch, case, page(case, "N1", raw=raw))
    assert result.clicks == [] and not any(result.callback_results)


@pytest.mark.parametrize("raw", [False, True], ids=["public-roi", "ignored-original"])
def test_rotated_out_d12_qingshang_rejects_current_mingjue_intent(tmp_path, monkeypatch, raw):
    result = replay(tmp_path, monkeypatch, "D12", page("D12", "QINGSHANG-P1", raw=raw))
    assert result.clicks == [] and not any(result.callback_results)
    assert not (result.f.paths.marker_root / "pending.claim").exists()
    assert not (result.f.paths.marker_root / "pending.json").exists()


def test_current_d12_mingjue_rejects_old_qingshang_intent(tmp_path, monkeypatch):
    result = replay(tmp_path, monkeypatch, "D12", page("D12"), "old_d12_target")
    assert result.clicks == [] and not any(result.callback_results)
    assert not (result.f.paths.marker_root / "pending.claim").exists()
    assert not (result.f.paths.marker_root / "pending.json").exists()


@pytest.mark.parametrize("case", ["D12", "D13", "D14"])
@pytest.mark.parametrize("change", ["plan_price", "plan_quantity", "plan_currency", "normal_no",
    "inactive_binding", "closed_state", "old_session", "expired_permit", "wrong_icon", "covered_title", "covered_quantity"])
def test_same_real_page_mismatch_or_closed_audit_is_zero_click(tmp_path, monkeypatch, case, change):
    image = page(case)
    if change == "wrong_icon":
        other = page("D13" if case == "D12" else "D12")
        image[539:570, 653:684] = other[539:570, 653:684]
    elif change == "covered_title":
        image[210:244, 535:750] = 255
    elif change == "covered_quantity":
        image[643:690, 439:472] = 255
    result = replay(tmp_path, monkeypatch, case, image, change)
    assert result.clicks == [] and not any(result.callback_results)
    assert not (result.f.paths.marker_root / "pending.claim").exists()


@pytest.mark.parametrize("mode,click_count", [("audit_full_flow", 1), ("normal_full_flow", 2)])
def test_actual_d14_option_skips_only_non_consuming_max_in_audit_and_keeps_normal_no(tmp_path, monkeypatch, mode, click_count):
    result = replay(tmp_path, monkeypatch, "D14", page("D14", raw=True), mode)
    assert len(result.clicks) == click_count
    if mode == "normal_full_flow":
        assert result.clicks[0] == (643, 671)
    else:
        assert (643, 671) not in result.clicks
        pending = commit.read_object(result.f.paths.marker_root / "pending.json")
        assert pending["case_id"] == "D14" and pending["intent"] == POLICIES["D14"]


@pytest.mark.parametrize("raw", [False, True])
def test_actual_d14_nearstart_month_selector_box_is_inside_weapon_card(tmp_path, raw):
    from maa.pipeline import JAnd

    bundle = Path(os.environ.get("MAA_PURCHASE_OCR_RESOURCE", ROOT / "resource/base"))
    if not (bundle / "model/ocr/rec.onnx").is_file():
        pytest.skip("OCR models unavailable")
    record = PROVENANCE["D14-S0-month"]
    if raw:
        source = os.environ.get("MAA_PURCHASE_RAW_PAGES")
        if not source:
            pytest.skip("raw pages unavailable")
        image = cv2.imdecode(np.fromfile(Path(source) / record["original_filename"], dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, w, h = record["crop"]
        image[y:y+h, x:x+w] = cv2.imdecode(np.fromfile(FIXTURES / record["filename"], dtype=np.uint8), cv2.IMREAD_COLOR)
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    task = json.loads((ROOT / "tasks/购买物品.json").read_text(encoding="utf-8"))
    override = task["option"]["选择物品"]["cases"][2]["pipeline_override"]
    assert resource.override_pipeline(override)
    controller = MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        params = resource.get_node_data("推荐物品")["recognition"]["param"]
        detail = tasker.post_recognition("And", JAnd(all_of=params["all_of"], box_index=0), image).wait().get().nodes[0].recognition
        assert detail.hit is True
        child = detail.best_result.sub_results[0]
        assert len(child.filtered_results) == 1 and "本月" in child.best_result.text
        x, y, w, h = detail.box
        assert 884 <= x < x + w <= 1126 and 355 <= y < y + h <= 499
        assert controller.clicks == []
        (tmp_path / "d14-native-selector.json").write_text(json.dumps({"box": list(detail.box),
            "text": child.best_result.text, "roi": [218, 355, 1062, 144], "raw_original": raw}, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        stop_and_wait_idle(tasker)
