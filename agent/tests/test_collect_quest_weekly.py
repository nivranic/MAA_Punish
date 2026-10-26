"""Weekly reward eligibility is a numerator, not the /10 denominator.

Real screenshots/OCR and modal negatives are replayed separately. These
contract checks do not prove an eligible reward was collected in the game.
"""

from pathlib import Path
import re

import jsonc
import pytest


ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads((ROOT / "resource/base/pipeline/Collect_Quest.jsonc").read_text(encoding="utf-8"))
REWARDS = ("领取补签卡1", "领取补签卡2")


@pytest.mark.parametrize("text,expected", [
    ("0", (False, False)), ("1", (False, False)), ("4", (False, False)),
    ("5", (True, False)), ("9", (True, False)), ("10", (True, True)),
    ("12", (True, True)), ("20", (True, True)),
    ("", (False, False)), ("05", (False, False)), ("+5", (False, False)),
    ("-5", (False, False)), ("5.0", (False, False)), ("5a", (False, False)),
    ("a10", (False, False)), (" 5", (False, False)), ("10 ", (False, False)),
    ("0 /10", (False, False)), ("4/10", (False, False)),
    ("5/10", (False, False)), ("10/10", (False, False)),
])
def test_only_complete_eligible_numerator_matches(text, expected):
    # Maa uses a regex search, so anchors must reject partial/denominator hits.
    counts = [NODES[name]["recognition"]["param"]["all_of"][2] for name in REWARDS]
    actual = tuple(bool(re.search(NODES[name]["recognition"]["param"]["expected"], text))
                   for name in counts)
    assert actual == expected


@pytest.mark.parametrize("name,visible_reward_box", [
    ("领取补签卡1", (798, 611, 54, 54)),
    ("领取补签卡2", (1129, 611, 53, 54)),
])
def test_weekly_reward_uses_selected_page_and_visible_reward_target(name, visible_reward_box):
    node = NODES[name]
    assert node["recognition"]["type"] == "And"
    parts = node["recognition"]["param"]["all_of"]
    # The existing Weekly OCR gets its language override; a tab label alone
    # is also visible on Daily and Duty, so it cannot establish selection.
    assert parts[:2] == ["每周任务", "每周任务选中背景"]
    assert all(isinstance(part, str) for part in parts)
    counter = NODES[parts[2]]["recognition"]
    assert counter["type"] == "OCR"
    assert counter["param"]["only_rec"] is True
    assert counter["param"]["roi"] == [348, 654, 59, 55]
    x, y = node["action"]["param"]["target"]
    bx, by, width, height = visible_reward_box
    assert bx <= x < bx + width and by <= y < by + height
    assert node["action"]["type"] == "Click"
    assert node["max_hit"] == 1
    assert node["next"] == ["关闭奖励"]


def test_reward_jumpbacks_still_return_before_duty_and_daily():
    branches = NODES["每周任务"]["next"]
    assert branches.index("[JumpBack]领取补签卡1") < branches.index("[JumpBack]领取补签卡2")
    assert branches.index("[JumpBack]领取补签卡2") < branches.index("勤务任务")
    assert NODES["每周任务选中背景"]["recognition"]["type"] == "ColorMatch"
    assert NODES["每周任务选中背景"].get("action", {}).get("type", "DoNothing") == "DoNothing"
