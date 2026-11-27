"""D12 submits only from an actual Chinese purchase button, not limit text."""

import os
from pathlib import Path
import re
from types import SimpleNamespace

import jsonc
import numpy as np
import pytest
from maa.resource import Resource
from maa.tasker import Tasker

import audit_commit
from audit_config import AuditConfig
from action.basics import AuditPurchase as purchase
from recognition.exclusives import PurchaseObservation as observation
from test_audit_purchase_native import MemoryController
from test_daily_activity_native import ROOT, native_resource
from test_support.native_lifecycle import stop_and_wait_idle


BUY = "购买"
BUTTON = "识别购买界面购买"
SCAN = "仅检查逆元碎片购买按钮"


@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_resolved_d12_ocr_matches_whole_chinese_button_only(tmp_path, language):
    assert Tasker.set_log_dir(tmp_path / "native")
    resource = Resource()
    for pack in ("base",) if language == "base" else ("base", language):
        path = ROOT / "resource" / pack / "pipeline/Buy_Item.jsonc"
        assert resource.override_pipeline(jsonc.loads(path.read_text(encoding="utf-8")))
    outer = resource.get_node_data(BUY)["recognition"]["param"]
    assert outer["all_of"][outer["box_index"]] == BUTTON
    ocr = resource.get_node_data(BUTTON)["recognition"]["param"]
    patterns = ocr["expected"]
    if isinstance(patterns, str):
        patterns = [patterns]

    def matches(text):
        return any(re.search(pattern, text) for pattern in patterns)

    for caption in ("还可购买1", "還可購買1", "无法购买", "購買成功", "售罄"):
        assert not matches(caption), caption
    assert matches("购买") and matches("購買")


def synthetic_capture(language, has_button):
    image_module = pytest.importorskip("PIL.Image")
    image_draw = pytest.importorskip("PIL.ImageDraw")
    image_font = pytest.importorskip("PIL.ImageFont")
    bundled = os.environ.get("MAA_TEST_CJK_FONT")
    fonts = Path(os.environ.get("WINDIR", "")) / "Fonts"
    candidates = ([Path(bundled)] if bundled else []) + [
        fonts / ("msjh.ttc" if language == "zh_TW" else "msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    face = next((path for path in candidates if path.is_file()), None)
    if face is None:
        pytest.skip("Chinese font needed to draw synthetic OCR frames")
    image = image_module.new("RGB", (1280, 720), (43, 43, 43))
    draw = image_draw.Draw(image)
    font = image_font.truetype(str(face), 28)
    caption = "還可購買1" if language == "zh_TW" else "还可购买1"
    draw.text((790, 606), caption, font=font, fill="white")
    if has_button:
        draw.text((744, 645), "購買" if language == "zh_TW" else "购买", font=font, fill="white")
    return np.asarray(image).copy()


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("has_button", [False, True], ids=["limit-only", "button-and-limit"])
def test_native_d12_button_does_not_dispatch_a_click_to_limit_text(tmp_path, monkeypatch, language, has_button):
    image = synthetic_capture(language, has_button)
    resource = native_resource(tmp_path, language)
    assert (ROOT / "resource/base/model/ocr/rec.onnx").is_file(), "actual OCR model required"
    controller, tasker = MemoryController(), Tasker()
    controller.screencap = lambda: image.copy()
    gate = purchase.PurchaseCommit
    monkeypatch.setattr(purchase, "LoggerComponent", lambda _: SimpleNamespace(logger=SimpleNamespace(error=lambda *a: None)))
    monkeypatch.setattr(purchase, "PurchaseCommit", lambda logger: gate(
        logger, config=AuditConfig(), paths=audit_commit.RuntimePaths(tmp_path / "runtime")))
    monkeypatch.setattr(observation, "AUDIT_CONFIG", AuditConfig())
    assert resource.register_custom_action("AuditPurchase", purchase.AuditPurchase())
    assert resource.register_custom_recognition("D12PurchaseObservation", observation.D12PurchaseObservation())
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    overrides = {
        SCAN: {"next": [BUY], "on_error": [], "timeout": 500, "rate_limit": 10, "post_delay": 0},
        "识别购买界面逆元碎片": {"recognition": "DirectHit"},
        # Probe the production And/box/guard but do not advance to the ledger.
        BUY: {"next": [], "on_error": [], "pre_delay": 0, "post_delay": 0},
    }
    try:
        job = tasker.post_task(SCAN, overrides).wait()
        assert len(controller.clicks) == int(has_button), controller.clicks
        assert job.succeeded is has_button
        if has_button:
            x, y = controller.clicks[0]
            assert 735 <= x < 812 and 644 <= y < 687, (x, y)
    finally:
        stop_and_wait_idle(tasker)
