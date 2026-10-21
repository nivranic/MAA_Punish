"""Regression checks from the 2026-10-21 Android navigation screenshots."""

from pathlib import Path

import jsonc
import pytest


PIPELINE = Path(__file__).resolve().parents[2] / "resource/base/pipeline"


@pytest.mark.parametrize(
    ("node_name", "observed_box"),
    [
        ("返回_文字", (125, 16, 58, 55)),  # Profile screen.
        ("返回_文字", (130, 22, 48, 29)),  # Guild screen.
        ("返回主菜单_文字", (288, 23, 65, 29)),  # Profile screen.
    ],
)
def test_navigation_roi_contains_complete_observed_label(node_name, observed_box):
    shared = jsonc.loads(
        (PIPELINE / "Common/Shared_Recognition.jsonc").read_text(encoding="utf-8")
    )
    x, y, width, height = shared[node_name]["recognition"]["param"]["roi"]
    bx, by, bw, bh = observed_box
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_layout_entry_can_confirm_popup_and_resume_navigation():
    layout = jsonc.loads(
        (PIPELINE / "Common/Read_Interface/Cache_Roi_Zone.json").read_text(
            encoding="utf-8"
        )
    )
    general = jsonc.loads((PIPELINE / "General.jsonc").read_text(encoding="utf-8"))

    # A plain terminal confirmation would end layout collection at the popup.
    # Reuse the existing click action and jump back to the navigation choices.
    assert "[JumpBack]确定" in layout["打开界面设置"]["next"]
    confirmation = general["确定"]
    assert confirmation["action"]["type"] == "Click"
    assert "确定_文字" in confirmation["recognition"]["param"]["all_of"]
    assert not confirmation.get("next")
