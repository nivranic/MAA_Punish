"""Replay the button geometry observed in the Android failure log."""

from pathlib import Path

import jsonc


PIPELINE = (
    Path(__file__).resolve().parents[2]
    / "resource/base/pipeline/Simulated_Siege.jsonc"
)


def test_battle_entry_roi_contains_complete_observed_button():
    pipeline = jsonc.loads(PIPELINE.read_text(encoding="utf-8"))
    node = pipeline["作战界面"]
    x, y, width, height = node["recognition"]["param"]["roi"]
    # 2026-10-15 21:23:23: a wider OCR node read the complete label here.
    bx, by, bw, bh = (1001, 653, 94, 29)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh
    assert node["recognition"]["param"]["expected"] == "作战开始"
    assert "[JumpBack]作战开始" in node["next"]
    assert "[JumpBack]战斗中" in node["next"]
