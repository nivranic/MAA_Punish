"""Fixed daily/weekly reward counters and non-event routing.

Real screenshots/OCR and modal negatives are replayed separately. These
contract checks do not prove an eligible reward was collected in the game.
"""

from pathlib import Path
import re

import jsonc
import pytest

from action.basics.daily_progress import ADMIT_NODE, GUARD_POINTER, REJECT_NODE


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


@pytest.mark.parametrize("value,branch", [(13, "0-19"), (20, "20-39"), (39, "20-39"),
    (40, "40-59"), (60, "60-79"), (80, "80-99"), (99, "80-99"), (100, "100"), (130, "100"), (200, "100")])
def test_daily_activity_reads_complete_numerator_without_the_denominator(value, branch):
    candidates = {name: node for name, node in NODES.items() if name.startswith("领取活跃")}
    for node in candidates.values():
        param = node["recognition"]["param"]
        x, y, width, height = param["roi"]
        # The three-digit numerator occupies this whole region. Its rightmost
        # digit must not be cropped, and the adjacent /100 is not the score.
        assert x <= 340 and x + width >= 424 and x + width <= 429
        assert y <= 665 and y + height >= 698
        assert param["only_rec"] is True
        assert re.search(param["expected"], "130/100") is None
    matches = [name for name, node in candidates.items()
               if re.search(node["recognition"]["param"]["expected"], str(value))]
    assert matches == ["领取活跃" + branch]


@pytest.mark.parametrize("branch,box", [
    ("20-39", (594, 610, 54, 54)), ("40-59", (727, 610, 54, 54)),
    ("60-79", (860, 610, 54, 54)), ("80-99", (993, 610, 54, 54)),
    ("100", (1127, 610, 54, 54)),
])
def test_daily_activity_target_is_inside_its_reward_chest(branch, box):
    x, y = NODES["领取活跃" + branch]["action"]["param"]["target"]
    bx, by, width, height = box
    assert bx <= x < bx + width and by <= y < by + height


@pytest.mark.parametrize("bundle", ["base", "zh_TW", "ja_JP", "en_US", "pgr_win32", "playcover"])
def test_fixed_quest_route_keeps_daily_rewards_and_cannot_enter_limited_activity(bundle):
    gate_file = "Tool/Daily_Progress.jsonc"
    gate_nodes = jsonc.loads((ROOT / "resource/base/pipeline" / gate_file).read_text(encoding="utf-8"))
    nodes = {**NODES, **gate_nodes}
    if bundle != "base":
        for relpath in ("Collect_Quest.jsonc", gate_file):
            overlay = ROOT / "resource" / bundle / "pipeline" / relpath
            if overlay.exists():
                for name, override in jsonc.loads(overlay.read_text(encoding="utf-8")).items():
                    nodes[name] = {**nodes.get(name, {}), **override}

    entry = nodes["任务"]
    assert entry["next"] == [ADMIT_NODE] and entry["on_error"] == []
    guard = entry["attach"][GUARD_POINTER]
    assert guard == "日常断点_领取任务"
    assert nodes[guard]["recognition"]["param"]["custom_recognition"] == "DailyProgressCheck"
    for name in (ADMIT_NODE, REJECT_NODE):
        gate = nodes[name]
        assert gate["recognition"]["type"] == "DirectHit" and gate.get("enabled", True)
        assert gate["action"] == {"type": "Custom", "param": {"custom_action": "DailyProgressAdmit"}}
        assert gate["next"] == gate["on_error"] == []

    visited, pending = set(), ["任务"]
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        node = nodes.get(name, {})
        # Admit creates these task-local routes only after the ledger read.
        # Failure has no fallback; done reaches the empty task, not the rewards.
        edges = [guard, REJECT_NODE, "空任务"] if name == ADMIT_NODE else node.get("next", []) + node.get("on_error", [])
        for edge in edges:
            target = edge["name"] if isinstance(edge, dict) else re.sub(r"^(?:\[[^]]+\])+", "", edge)
            if target in nodes:
                pending.append(target)
    assert {"逐浪拾光", "前往任务", "领取_逐浪拾光"}.isdisjoint(visited)
    assert {"每周任务", "勤务任务", "每日任务", "一键领取_任务",
            "一键领取_定期", "领取补签卡1", "领取补签卡2", "领取活跃100"} <= visited


def test_missing_assistant_entry_falls_back_to_task_page():
    # 2026-11-26 device evidence: tapping the assistant opens a trust dialogue
    # overlay (random lines, no 交流 button), so 每日助理交流_进入 timed out and
    # the whole task exited through 任务失败优雅退出. The fallback keeps the
    # weekly claim alive by opening the task page instead.
    entry = NODES["每日助理交流_进入"]
    assert entry["on_error"] == ["助理交流入口缺失_转任务", "任务失败优雅退出"]
    # The assistant click itself also fails when the entry never appears, so
    # both nodes keep the same recovery order.
    assert NODES["每日助理交流"]["on_error"] == ["助理交流入口缺失_转任务", "任务失败优雅退出"]
    fallback = NODES["助理交流入口缺失_转任务"]
    assert fallback["recognition"] == {"type": "And", "param": {"all_of": ["主界面_战斗节点"]}}
    assert fallback["action"] == {"type": "DoNothing"}
    assert fallback["next"] == ["任务_领取"]
    assert fallback["focus"] == {"Node.Recognition.Succeeded": "$focus.助理交流入口缺失_转任务"}
    # 任务_领取 must still be able to open the task page from a main screen.
    assert "[JumpBack]打开任务" in NODES["任务_领取"]["next"]


@pytest.mark.parametrize("locale", ["zh_cn", "zh_tw", "en_us", "ja_jp"])
def test_assistant_fallback_focus_is_translated(locale):
    data = jsonc.loads((ROOT / f"i18n/{locale}.json").read_text(encoding="utf-8"))
    assert data["focus.助理交流入口缺失_转任务"].strip()
