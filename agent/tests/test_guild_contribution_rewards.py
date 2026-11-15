"""Fixed guild milestone eligibility and reward-strip positioning."""

from pathlib import Path
import re

import jsonc
import pytest


ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads((ROOT / "resource/base/pipeline/Simulated_Siege.jsonc").read_text(encoding="utf-8"))
TIERS = (3000, 6000, 9000, 12000, 15000)


@pytest.mark.parametrize("value", [0, 2999, 3000, 5999, 6000, 8999, 9000, 11999, 12000,
                                  14999, 15000, 25300, 100000])
def test_milestone_matches_at_threshold_and_never_below(value):
    for tier in TIERS:
        pattern = NODES[f"识别本周贡献{tier}"]["recognition"]["param"]["expected"]
        assert bool(re.search(pattern, str(value))) is (value >= tier)
        assert re.search(pattern, f"{value}_") is None
        assert re.search(pattern, f"{value}/15000") is None


def test_milestones_position_the_strip_before_using_reward_coordinates():
    entry = NODES["领取本周贡献"]["next"]
    assert "本周贡献奖励归位" in entry
    assert not any(name.startswith("识别本周贡献") for name in entry)
    align = NODES["本周贡献奖励归位"]
    assert align["recognition"] == {"type": "And", "param": {
        "all_of": ["本周贡献奖励数值", "本周贡献奖励档位"],
    }}
    assert align["action"]["type"] == "Swipe"
    param = align["action"]["param"]
    assert 300 < param["end"][0] < param["begin"][0] < 1228
    assert 605 <= param["begin"][1] <= 665 and 605 <= param["end"][1] <= 665
    names = [f"识别本周贡献{tier}" for tier in reversed(TIERS)]
    assert align["next"][:len(TIERS)] == names


def test_counter_excludes_the_bottom_decoration_and_targets_match_the_aligned_strip():
    boxes = [(408, 608, 54, 57), (597, 608, 54, 57), (786, 608, 54, 57),
             (975, 608, 54, 57), (1163, 608, 54, 57)]
    for tier, box in zip(TIERS, boxes):
        node = NODES[f"识别本周贡献{tier}"]
        x, y, width, height = node["recognition"]["param"]["roi"]
        assert x <= 104 and x + width >= 225
        assert y <= 669 and 697 <= y + height < 701
        tx, ty = node["action"]["param"]["target"]
        bx, by, bw, bh = box
        assert bx <= tx < bx + bw and by <= ty < by + bh
