"""Recruitment priority must distinguish frames belonging to the same character."""

from pathlib import Path
import re

import jsonc
import pytest


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc"


@pytest.mark.parametrize("slot", ["一", "二", "三"])
@pytest.mark.parametrize("name,preferred", [
    ("露西亚·深红囚影", True),
    ("里·超刻", True),
    ("比安卡·深痕", True),
    ("拉弥亚·深谣", True),
    ("露西亚·黎明", False),
    ("露西亚·红莲", False),
    ("露西亚·深红之渊", False),
    ("露西亚", False),
    ("露西亚·深红", False),
])
def test_preferred_recruitment_requires_a_supported_frame_name(slot, name, preferred):
    nodes = jsonc.loads(PIPELINE.read_text(encoding="utf-8"))
    patterns = nodes[f"{slot}号位_名字_宣叙妄响"]["recognition"]["param"]["expected"]
    assert any(re.search(pattern, name) for pattern in patterns) is preferred
