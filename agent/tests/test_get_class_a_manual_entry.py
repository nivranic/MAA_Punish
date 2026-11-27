"""A-rank shard card opens a page whose manual entry reads 战斗准备, not 战斗/自动作战."""

from pathlib import Path
import re

import jsonc
import pytest


ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads((ROOT / "resource/base/pipeline/Get_Class_A.jsonc").read_text(encoding="utf-8"))
ZH_TW = jsonc.loads((ROOT / "resource/zh_TW/pipeline/Get_Class_A.jsonc").read_text(encoding="utf-8"))
LOCALES = ("zh_cn", "zh_tw", "en_us", "ja_jp")

# Measured on the 2026-11-26 device frame (1280x720): the button body spans
# x 958..1182 and y 633..681. The older 战斗-only roi covered the left third of
# that box, which cuts a four-character label down to 战斗准 and matches nothing.
BUTTON = (958, 633, 224, 48)


def expected_labels(node):
    expected = node["recognition"]["param"]["expected"]
    return [expected] if isinstance(expected, str) else list(expected)


def test_card_click_falls_back_to_the_manual_entry_after_the_auto_branch():
    assert NODES["A级角色"]["next"] == [
        "使用体力刷碎片",
        "未解锁自动作战",
        "未解锁自动作战_战斗准备",
    ]


def test_manual_entry_roi_covers_the_measured_button():
    roi = NODES["未解锁自动作战_战斗准备"]["recognition"]["param"]["roi"]
    left, top, width, height = BUTTON
    assert roi[0] <= left and roi[0] + roi[2] >= left + width
    assert roi[1] <= top and roi[1] + roi[3] >= top + height


def test_manual_entry_matches_the_observed_label_only():
    labels = expected_labels(NODES["未解锁自动作战_战斗准备"])
    assert any(re.search(label, "战斗准备") for label in labels)
    assert not any(re.search(label, "自动作战") for label in labels)


def test_manual_entry_closes_the_page_without_spending_serum():
    node = NODES["未解锁自动作战_战斗准备"]
    assert "action" not in node
    assert node["next"] == ["返回主菜单"]
    assert node["focus"] == {"Node.Recognition.Succeeded": "$focus.未解锁自动作战_战斗准备"}


@pytest.mark.parametrize("language", LOCALES)
def test_focus_key_is_translated_in_every_locale(language):
    translations = jsonc.loads((ROOT / f"i18n/{language}.json").read_text(encoding="utf-8"))
    assert translations["focus.未解锁自动作战_战斗准备"].strip()


def test_traditional_chinese_covers_both_labels():
    labels = ZH_TW["未解锁自动作战_战斗准备"]["expected"]
    assert "战斗准备" in labels and "戰鬥準備" in labels
