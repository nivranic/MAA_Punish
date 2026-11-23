"""R02 invite-candidate whitelist tokens stay synced across the three slot nodes.

The whitelist is the OCR ``expected`` list of 选择人物1/2/3_厄愿潮声. A live
火之邀约LV2 pool (2026-11-23, candidates 丽芙·极昼/罗兰·戏炎/里·异火) missed the
old list and burned the refresh budget into 未找到指定角色 re-entry, so the
expansion tokens are pinned here against silent reverts and slot drift.
"""
from pathlib import Path

import jsonc
import pytest

ROOT = Path(__file__).resolve().parents[2]

SLOTS = ("选择人物1_厄愿潮声", "选择人物2_厄愿潮声", "选择人物3_厄愿潮声")

# Evidence-backed expansion tokens (short glyph forms for the narrow name-row ROI).
EXPANSION = ("丽芙", "极昼", "罗兰", "戏炎", "异火")
# Traditional + variant glyph forms shipped by the locale overrides.
EXPANSION_ZH_TW = EXPANSION + ("麗芙", "極晝", "羅蘭", "戲炎", "異火")
EXPANSION_JA_JP = EXPANSION_ZH_TW + ("極昼", "戯炎")
# Original tokens that must never drop out of the base list.
BASE_CORE = ("红网", "深红内", "深红囚影", "囚影", "誓焰", "超刻", "深谣", "终焉", "深痕", "启明")


def _slot_lists(locale: str) -> list[tuple[str, ...]]:
    path = ROOT / f"resource/{locale}/pipeline/Roguelike_2/Roguelike_2.jsonc"
    data = jsonc.loads(path.read_text(encoding="utf-8"))
    lists = []
    for name in SLOTS:
        node = data[name]
        expected = node.get("expected") or node["recognition"]["param"]["expected"]
        lists.append(tuple(expected))
    return lists


@pytest.mark.parametrize(("locale", "expansion"), [
    ("base", EXPANSION),
    ("zh_TW", EXPANSION_ZH_TW),
    ("ja_JP", EXPANSION_JA_JP),
])
def test_three_slots_share_one_whitelist(locale, expansion):
    lists = _slot_lists(locale)
    assert lists[0] == lists[1] == lists[2]
    assert len(set(lists[0])) == len(lists[0]), "duplicate tokens"
    for token in expansion:
        assert token in lists[0], f"{locale} whitelist lost expansion token {token}"


def test_base_whitelist_keeps_original_tokens():
    (base,) = {_slot_lists("base")[0]}
    for token in BASE_CORE:
        assert token in base
