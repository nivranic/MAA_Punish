"""Regression checks from the 2026-10-21 Android navigation screenshots."""

from pathlib import Path
import re

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


def test_join_team_roi_contains_complete_observed_label():
    roles = jsonc.loads((PIPELINE / "Role_Selection.jsonc").read_text(encoding="utf-8"))
    x, y, width, height = roles["编入队伍"]["recognition"]["param"]["roi"]
    # 15:35 roster screenshot: the old left edge at 983 cropped off 编.
    bx, by, bw, bh = (952, 472, 102, 29)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_layout_tutorial_is_handled_before_capture_and_collapse():
    layout = jsonc.loads(
        (PIPELINE / "Common/Read_Interface/Cache_Roi_Zone.json").read_text(encoding="utf-8")
    )
    for parent, later in (("打开界面设置", "收起布局菜单"), ("收起布局菜单", "识别区域")):
        branches = layout[parent]["next"]
        assert branches.index("[JumpBack]关闭布局引导") < branches.index(later)
    # A tutorial can consume the first fold click; check the expanded menu again.
    assert "收起布局菜单" in layout["收起布局菜单"]["next"]
    # Do not calibrate while the menu is still expanded behind a tutorial.
    assert "展开布局菜单" in layout["识别区域"]["recognition"]["param"]["all_of"]


@pytest.mark.parametrize(
    "observed_box",
    [(141, 81, 86, 90), (134, 186, 96, 98), (136, 302, 92, 91),
     (136, 412, 92, 91), (136, 523, 91, 91)],
)
def test_role_portrait_roi_contains_observed_complete_portraits(observed_box):
    roles = jsonc.loads((PIPELINE / "Role_Selection.jsonc").read_text(encoding="utf-8"))
    x, y, width, height = roles["识别角色"]["recognition"]["param"]["roi"]
    bx, by, bw, bh = observed_box
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_trial_role_switch_roi_contains_complete_power_label():
    siege = jsonc.loads((PIPELINE / "Simulated_Siege.jsonc").read_text(encoding="utf-8"))
    x, y, width, height = siege["选择其他人物"]["recognition"]["param"]["roi"]
    bx, by, bw, bh = (259, 131, 52, 17)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


@pytest.mark.parametrize(
    ("text", "locked"),
    [("碎片", True), ("10/50", True), ("/50", True),
     ("3/3", False), ("80", False), ("8159", False)],
)
def test_fragment_counter_fallback_does_not_match_owned_role_metadata(text, locked):
    roles = jsonc.loads((PIPELINE / "Role_Selection.jsonc").read_text(encoding="utf-8"))
    expected = roles["检查人物是否拥有"]["recognition"]["param"]["expected"]
    patterns = [expected] if isinstance(expected, str) else expected
    assert any(re.search(pattern, text) for pattern in patterns) is locked
