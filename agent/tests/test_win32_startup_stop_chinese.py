"""Win32 startup/stop OCR contracts accept exact simplified and traditional buttons."""

from pathlib import Path
import re

import jsonc


ROOT = Path(__file__).resolve().parents[2]
WIN32 = ROOT / "resource" / "pgr_win32" / "pipeline"


def _expected(file_name: str, node: str):
    nodes = jsonc.loads((WIN32 / file_name).read_text(encoding="utf-8"))
    return nodes[node]["recognition"]["param"]["expected"]


def _matches(patterns, text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def test_win32_login_accepts_only_exact_simplified_and_traditional_forms():
    patterns = _expected("Start_up.jsonc", "点击登录")
    for text in ("登录", "登錄", "登入"):
        assert _matches(patterns, text), text
    for text in ("返回", "取消", "用戶帳號登錄失敗", "登入遊戲"):
        assert not _matches(patterns, text), text


def test_win32_exit_confirmation_accepts_simplified_and_traditional_but_not_generic_confirm():
    patterns = _expected("Stop_App.jsonc", "确认退出")
    for text in ("确定", "確定"):
        assert _matches(patterns, text), text
    # The exit confirmation ROI is narrow; do not accept the generic 確認 word
    # or any surrounding dialog text without an exact button match.
    for text in ("確認", "取消", "返回", "確定取消"):
        assert not _matches(patterns, text), text
