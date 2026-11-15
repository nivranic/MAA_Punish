"""A mid-battle R03 dialogue must yield to its existing skip route."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.entry.runner import CombatRunner
from test_support.fakes import FakeContext, make_hit

ROOT = Path(__file__).resolve().parents[2]
DIALOGUE = "跳过战斗对话"


def battle_node():
    task = json.loads((ROOT / "tasks/矩阵循生.json").read_bytes())["task"][0]
    return task["pipeline_override"]["战斗中"]


@pytest.mark.parametrize("hud_visible", [True, False])
def test_r03_dialogue_yields_before_identity_checks_or_combat_input(hud_visible):
    recognition = {DIALOGUE: make_hit()}
    if hud_visible:
        recognition["战斗中"] = make_hit()
    context = FakeContext(recognition_map=recognition)
    checker = CombatCheck(exit_nodes=battle_node()["attach"].get("combat_exit_nodes", ()))
    combat = CombatTask(context, checker, roguelike3_identity_required=True)
    combat.frame = b"same-dialogue-frame"
    assert combat._check_combat_presence() == "outer_interface"
    assert context.actions == [] and context.tasker.controller.clicks == []


def test_r03_fight_without_dialogue_continues():
    context = FakeContext(recognition_map={"战斗中": make_hit()})
    checker = CombatCheck(exit_nodes=battle_node()["attach"].get("combat_exit_nodes", ()))
    combat = CombatTask(context, checker, roguelike3_identity_required=True)
    combat.frame = b"same-fight-frame"
    assert combat._check_combat_presence() == ""
    assert context.actions == [] and context.tasker.controller.clicks == []


def test_runner_limits_dialogue_exit_to_the_current_r03_invocation(monkeypatch):
    observed = []
    monkeypatch.setattr(CombatTask, "combat_once", lambda self: observed.append(
        (self.roguelike3_identity_required, self.combat_check.exit_nodes)
    ))
    context = FakeContext(node_data={"战斗中": battle_node()})
    runner = CombatRunner()
    args = SimpleNamespace(node_name="战斗中", custom_action_param="")
    assert runner.run(context, args).success
    context.node_data["战斗中"] = {}
    assert runner.run(context, args).success
    assert observed == [(True, (DIALOGUE,)), (False, ())]


def test_only_r03_requests_the_battle_dialogue_exit():
    requested = []
    for path in (ROOT / "tasks").glob("*.json"):
        for task in json.loads(path.read_bytes()).get("task", []):
            attach = task.get("pipeline_override", {}).get("战斗中", {}).get("attach", {})
            if DIALOGUE in attach.get("combat_exit_nodes", []):
                requested.append(task["name"])
    assert requested == ["矩阵循生"]
