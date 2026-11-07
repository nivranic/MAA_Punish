"""Requested hold durations remain independent across shared-context callers."""

import pytest

from action.combat.actions.CombatActions import CombatActions
from test_support.fakes import FakeContext


class DurationContext(FakeContext):
    def __init__(self, node_name):
        super().__init__(node_data={
            node_name: {"action": {"param": {"duration": 1000}}},
        })
        self.executed_durations = []

    def run_action(self, entry, pipeline_override=None):
        node = self.get_node_data(entry)
        self.executed_durations.append(node["action"]["param"]["duration"])
        return super().run_action(entry, pipeline_override)


@pytest.mark.parametrize("method_name,node_name", [
    ("long_press_attack", "长按攻击"),
    ("long_press_dodge", "长按闪避"),
    ("long_press_skill", "长按技能"),
])
def test_default_hold_duration_does_not_inherit_another_callers_override(
    method_name, node_name,
):
    context = DurationContext(node_name)
    # Role actions share a persistent Context, including pipeline overrides.
    custom = CombatActions(context)
    default = CombatActions(context)

    assert getattr(custom, method_name)(2000) is True
    assert getattr(default, method_name)() is True
    assert getattr(custom, method_name)(350) is True
    assert getattr(default, method_name)() is True

    assert context.executed_durations == [2000, 1000, 350, 1000]
    assert context.actions == [node_name] * 4
