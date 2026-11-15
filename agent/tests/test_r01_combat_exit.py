"""A waypoint prompt that appears after combat entry must return to its pipeline."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.entry.runner import CombatRunner
from test_support.fakes import FakeContext, make_hit


ROOT = Path(__file__).resolve().parents[2]
WAYPOINT = "失败2_宣叙妄响"


def test_waypoint_returns_control_even_while_combat_hud_remains_visible():
    context = FakeContext(recognition_map={WAYPOINT: make_hit(), "战斗中": make_hit()})
    combat = CombatTask(context, CombatCheck(exit_nodes=(WAYPOINT,)))
    combat.frame = b"same-decision-frame"
    assert combat._check_combat_presence() == "outer_interface"
    assert context.actions == []
    assert context.tasker.controller.clicks == []


def test_ordinary_combat_does_not_inherit_roguelike_exit_conditions():
    context = FakeContext(recognition_map={WAYPOINT: make_hit(), "战斗中": make_hit()})
    combat = CombatTask(context, CombatCheck())
    combat.frame = b"same-decision-frame"
    assert combat._check_combat_presence() == ""


def test_r01_combat_without_waypoint_continues():
    context = FakeContext(recognition_map={"战斗中": make_hit()})
    combat = CombatTask(context, CombatCheck(exit_nodes=(WAYPOINT,)))
    combat.frame = b"same-decision-frame"
    assert combat._check_combat_presence() == ""


@pytest.mark.parametrize("value", ["one-node", [""], [1], None])
def test_invalid_exit_node_list_is_rejected(value):
    with pytest.raises(ValueError):
        CombatCheck(exit_nodes=value)


def test_only_r01_task_requests_the_waypoint_exit():
    requested = []
    for path in (ROOT / "tasks").glob("*.json"):
        for task in json.loads(path.read_text(encoding="utf-8")).get("task", []):
            attach = task.get("pipeline_override", {}).get("战斗中", {}).get("attach", {})
            if WAYPOINT in attach.get("combat_exit_nodes", []):
                requested.append(task["name"])
    assert requested == ["宣叙妄响"]


def test_runner_consumes_current_task_policy_without_leaking_to_next_run(monkeypatch):
    seen = []

    def capture(combat):
        seen.append(combat.combat_check.exit_nodes)

    monkeypatch.setattr(CombatTask, "combat_once", capture)
    action = CombatRunner()
    context = FakeContext(node_data={"战斗中": {"attach": {"combat_exit_nodes": [WAYPOINT]}}})
    argv = SimpleNamespace(node_name="战斗中", custom_action_param="")
    assert action.run(context, argv).success
    context.node_data["战斗中"] = {}
    assert action.run(context, argv).success
    assert seen == [(WAYPOINT,), ()]


def test_runner_rejects_malformed_exit_policy_before_input():
    context = FakeContext(node_data={"战斗中": {"attach": {"combat_exit_nodes": WAYPOINT}}})
    action = CombatRunner()
    result = action.run(context, SimpleNamespace(node_name="战斗中", custom_action_param=""))
    assert not result.success
    assert action.last_outcome.exit_reason == "combat_exit_policy_invalid"
    assert context.actions == []
