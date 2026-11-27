"""Actual Maa OCR on synthetic Win32 startup/stop button glyphs."""

from pathlib import Path

import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import LoggingLevelEnum, Tasker
from PIL import Image, ImageDraw, ImageFont

from test_audit_purchase_native import MemoryController
from test_daily_activity_native import ROOT
from test_support.native_lifecycle import stop_and_wait_idle


FONTS = {
    "zh_CN": Path("C:/Windows/Fonts/msyh.ttc"),
    "zh_TW": Path("C:/Windows/Fonts/msjh.ttc"),
}
CASES = {
    "点击登录": [
        ("simplified", "登录", "zh_CN", True),
        ("traditional-lu", "登錄", "zh_TW", True),
        ("traditional-ru", "登入", "zh_TW", True),
        ("unrelated", "返回", "zh_TW", False),
    ],
    "确认退出": [
        ("simplified", "确定", "zh_CN", True),
        ("traditional", "確定", "zh_TW", True),
        ("generic-confirm", "確認", "zh_TW", False),
        ("cancel", "取消", "zh_TW", False),
    ],
}


def _synthetic(literal: str, roi, font: Path) -> np.ndarray:
    x, y, width, height = roi
    image = Image.new("RGB", (1280, 720), "black")
    draw = ImageDraw.Draw(image)
    draw.text(
        (x + width // 2, y + height // 2),
        literal,
        font=ImageFont.truetype(str(font), 30),
        fill="white",
        anchor="mm",
    )
    return np.asarray(image).copy()


def test_current_win32_nodes_accept_exact_simplified_and_traditional_buttons(tmp_path):
    assert (ROOT / "resource/base/model/ocr/rec.onnx").is_file(), "actual OCR model required"
    for font in FONTS.values():
        if not font.is_file():
            pytest.skip(f"font unavailable: {font}")
    assert Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)

    resource = Resource()
    for bundle in ("base", "zh_TW", "pgr_win32"):
        assert resource.post_bundle(ROOT / "resource" / bundle).wait().succeeded

    hits = []

    class _Probe(CustomAction):
        def run(self, context, _argv):
            for node, cases in CASES.items():
                roi = context.get_node_data(node)["recognition"]["param"]["roi"]
                for label, literal, locale, expected in cases:
                    frame = _synthetic(literal, roi, FONTS[locale])
                    result = context.run_recognition(node, frame)
                    actual = bool(result and result.hit)
                    hits.append((node, label, literal, expected, actual))
            return self.RunResult(success=True)

    controller, tasker = MemoryController(), Tasker()
    assert resource.register_custom_action("Win32OCRProbe", _Probe())
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task(
            "Win32OCRProbe",
            {
                "Win32OCRProbe": {
                    "recognition": "DirectHit",
                    "action": {
                        "type": "Custom",
                        "param": {"custom_action": "Win32OCRProbe"},
                    },
                    "pre_delay": 0,
                    "post_delay": 0,
                    "on_error": [],
                    "next": [],
                }
            },
        ).wait()
        assert job.succeeded
        assert controller.clicks == []
    finally:
        stop_and_wait_idle(tasker)

    assert hits and all(expected == actual for _, _, _, expected, actual in hits), hits
