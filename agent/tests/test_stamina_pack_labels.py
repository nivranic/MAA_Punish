"""The three serum pack slots also take the 1周 label (user ruling 2026-11-28: use those packs without asking)."""

import jsonc
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pack_slots_take_the_week_label():
    path = ROOT / "resource/base/pipeline/daily_battle/Stamina.jsonc"
    nodes = jsonc.loads(path.read_text(encoding="utf-8"))
    for slot in "123":
        expected = nodes[f"使用{slot}号补给包"]["recognition"]["param"]["expected"]
        assert expected == ["小时", "^1天$", "^2天$", "^3天$", "^1周$"]
