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


def test_combat_roi_contains_live_pause_button_and_original_search_area():
    combat = jsonc.loads(
        (PIPELINE / "Auto_Battle/Auto_Battle.jsonc").read_text(encoding="utf-8")
    )
    x, y, width, height = combat["战斗中"]["recognition"]["param"]["roi"]
    # Three live combat frames all matched this complete 58x49 pause template.
    bx, by, bw, bh = (1145, 26, 58, 49)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh
    # Keep the original top-right area for layouts with a farther-right button.
    assert x <= 1165 and y == 0
    assert x + width == 1280 and height == 100


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


@pytest.mark.parametrize(
    ("filename", "node_name", "observed_box"),
    [
        ("Phantom_Pain_Cage.jsonc", "检查A区", (573, 425, 85, 28)),
        ("Phantom_Pain_Cage.jsonc", "挑战次数不足", (709, 108, 41, 35)),
        ("War_Zone.jsonc", "纷争战区内部", (503, 121, 53, 30)),
        ("War_Zone.jsonc", "首通已完成", (1143, 511, 53, 35)),
        ("War_Zone.jsonc", "战斗准备", (992, 656, 97, 30)),
    ],
)
def test_daily_mode_roi_contains_observed_complete_label(filename, node_name, observed_box):
    pipeline = jsonc.loads((PIPELINE / filename).read_text(encoding="utf-8"))
    x, y, width, height = pipeline[node_name]["recognition"]["param"]["roi"]
    bx, by, bw, bh = observed_box
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_cage_marker_keeps_pixel_threshold_and_stays_in_first_card():
    cage = jsonc.loads((PIPELINE / "Phantom_Pain_Cage.jsonc").read_text(encoding="utf-8"))
    param = cage["检查A区"]["recognition"]["param"]
    # Preserve the upstream false-positive guard; the old ROI clipped the marker.
    assert param["count"] == 1000
    x, y, width, height = param["roi"]
    assert x <= 462 and y <= 384
    assert x + width >= 605 and y + height >= 679
    assert x + width < 725  # Do not borrow the next card's blue pixels.


def test_war_zone_header_does_not_merge_two_rows_into_one_ocr_box():
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    _, y, _, height = war["纷争战区内部"]["recognition"]["param"]["roi"]
    # Original PNG: the lower 排行 row starts at y=153. Both rows together
    # are detected as a square and misread, even after widening the old ROI.
    assert y + height <= 153


def test_mapping_settlement_retry_uses_button_result_and_is_bounded():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    retry = mapping["结算_点击失效"]
    assert retry["recognition"]["type"] == "And"
    assert retry["recognition"]["param"]["all_of"] == ["结算"]
    assert retry["recognition"]["param"].get("box_index", 0) == 0
    assert retry["action"]["type"] == "Click"
    assert retry["action"].get("param", {}).get("target", True) is True
    assert 0 < retry["max_hit"] <= 3
    assert "[JumpBack]结算_点击失效" in mapping["结算"]["next"]


@pytest.mark.parametrize(
    ("parent", "later"),
    [("纷争战区", "纷争战区内部"),
     ("纷争战区内部", "[JumpBack]首通未完成"),
     ("首通未完成", "战斗准备")],
)
def test_war_zone_closes_profile_modal_before_map_actions(parent, later):
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    branches = war[parent]["next"]
    assert branches.index("[JumpBack]关闭战区玩家详情") < branches.index(later)
    close = war["关闭战区玩家详情"]
    assert close["recognition"]["param"]["all_of"] == [
        "意外点击_玩家详情", "战区玩家详情关闭按钮"
    ]
    assert close["recognition"]["param"]["box_index"] == 1
    assert close["action"].get("param", {}).get("target", True) is True
    x, y, width, height = war["战区玩家详情关闭按钮"]["recognition"]["param"]["roi"]
    assert x <= 1180 and y <= 51
    assert x + width >= 1225 and y + height >= 98


def test_war_profile_close_symbol_bypasses_batch_text_detection():
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    param = war["战区玩家详情关闭按钮"]["recognition"]["param"]
    # On the same phone image, individual OCR finds X but the real next-list
    # batch detector misses it. Recognize only the cropped symbol instead.
    assert param["only_rec"] is True
    x, y, width, height = param["roi"]
    assert x >= 1170 and x + width <= 1230
    assert y >= 47 and y + height <= 100  # Exclude the + above and score below.


def test_mapping_settlement_precedes_zero_counter_start_fallback():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    branches = mapping["开始挑战"]["next"]
    # Settlement also shows 0 currency at the start-retry ROI. Checking that
    # fallback first clicks the currency and opens its details instead of settling.
    assert branches.index("结算") < branches.index("[JumpBack]开始挑战_点击丢失")


def test_mapping_item_modal_close_is_inside_live_x_and_bounded():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    close = mapping["意外点击_物品详情"]
    x, y = close["action"]["param"]["target"]
    assert 1180 <= x < 1225 and 51 <= y < 96
    assert 0 < close["max_hit"] <= 3
    assert "[JumpBack]意外点击_物品详情" in mapping["结算"]["next"]
