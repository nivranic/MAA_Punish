"""G05 寒境回流边界：重开循环有界，轮数用尽落正向终局而非再回流。

台账 G05：`寒境曙光主界面→重置次数_寒境曙光`，重开主界面回 `寒境曙光`；
不是到主界面即自然停止——故重开回流必须带轮数上限，用尽后走
完成所有任务→主界面→重置次数 的正向确认终局。
"""
import jsonc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads(
    (ROOT / "resource/base/pipeline/Roguelike_4/Roguelike_4.jsonc").read_text(encoding="utf-8")
)


def test_restart_loop_back_is_round_bounded():
    entry = NODES["寒境曙光主界面_重开"]
    assert entry.get("max_hit", 0) >= 1
    assert entry["next"] == ["寒境曙光"]


def test_exhausted_round_budget_falls_through_to_positive_terminal():
    exit_next = NODES["完成所有任务_重开"]["next"]
    assert exit_next[0] == "寒境曙光主界面_重开"
    # 轮数用尽后必须落正向终局（主界面确认→重置次数→任务收束），
    # 且排在 JumpBack 兜底之前，不得再回流。
    assert exit_next[1] == "寒境曙光主界面"
    assert exit_next.index("寒境曙光主界面") < exit_next.index("[JumpBack]完成所有任务")


def test_terminal_confirms_main_interface_then_finishes_cleanly():
    terminal = NODES["寒境曙光主界面"]
    assert terminal["next"] == ["重置次数_寒境曙光"]
    assert "next" not in NODES["重置次数_寒境曙光"]
