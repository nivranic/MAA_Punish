"""Battle-defeat settlement overlay must exit combat without the UI-lost budget.

台账 2.338 实证（run_20261124_152439_2 第 43 战，uiwatch/f0251.png）：战败后
游戏切「作战失败」结算页、战斗 HUD 消失，旧路径耗 8s UI-miss 预算落
combat_ui_lost 误分类。败北页节点挂入 combat_exit_nodes 后按既有退出覆盖层
机制立即 outer_interface 退战（检测即走，不执行节点动作）。
"""

import json
from pathlib import Path
import re

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from test_support.fakes import FakeContext, make_hit

ROOT = Path(__file__).resolve().parents[2]
DEFEAT_R01 = "失败_宣叙妄响"
DEFEAT_GENERIC = "手动战斗_作战失败"

EXPECTED_EXITS = {
    "宣叙妄响": ["失败2_宣叙妄响", DEFEAT_R01],
    "矩阵循生": ["跳过战斗对话", DEFEAT_GENERIC],
    "厄愿潮声": [DEFEAT_GENERIC],
    "神寂启示录": [DEFEAT_GENERIC],
    "寒境曙光": [DEFEAT_GENERIC],
    "雾夜镇魂曲": [DEFEAT_GENERIC],
}


def task_exits():
    found = {}
    for path in (ROOT / "tasks").glob("*.json"):
        for task in json.loads(path.read_bytes()).get("task", []):
            attach = task.get("pipeline_override", {}).get("战斗中", {}).get("attach", {})
            nodes = attach.get("combat_exit_nodes")
            if nodes:
                found[task["name"]] = list(nodes)
    return found


def test_defeat_page_exits_combat_even_when_hud_is_missing():
    context = FakeContext(recognition_map={DEFEAT_GENERIC: make_hit()})
    combat = CombatTask(context, CombatCheck(exit_nodes=(DEFEAT_GENERIC,)))
    combat.frame = b"same-defeat-frame"
    assert combat._check_combat_presence() == "outer_interface"
    assert context.actions == []
    assert context.tasker.controller.clicks == []


def test_defeat_page_yields_before_hud_checks_like_other_overlays(capsys):
    context = FakeContext(recognition_map={DEFEAT_R01: make_hit(), "战斗中": make_hit()})
    combat = CombatTask(context, CombatCheck(exit_nodes=(DEFEAT_R01,)))
    combat.frame = b"same-defeat-frame"
    assert combat._check_combat_presence() == "outer_interface"
    assert f"识别到战斗退出界面: {DEFEAT_R01}" in capsys.readouterr().out


def test_without_exit_policy_a_defeat_frame_does_not_exit_combat():
    context = FakeContext(recognition_map={DEFEAT_GENERIC: make_hit()})
    combat = CombatTask(context, CombatCheck())
    combat.frame = b"same-defeat-frame"
    assert combat._check_combat_presence() == ""


def test_expected_tasks_request_their_defeat_exits():
    assert task_exits() == EXPECTED_EXITS


def test_attached_exit_nodes_are_recognizable_base_pipeline_nodes():
    patterns = {
        name: re.compile(r'"%s"\s*:\s*\{\s*"recognition"' % re.escape(name))
        for names in EXPECTED_EXITS.values()
        for name in names
    }
    for path in (ROOT / "resource" / "base" / "pipeline").rglob("*.jsonc"):
        text = path.read_text(encoding="utf8")
        for name in list(patterns):
            if patterns[name].search(text):
                del patterns[name]
    assert not patterns, f"exit nodes without base recognition definition: {sorted(patterns)}"
