"""Requested hold durations remain independent across shared-context callers."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

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


@pytest.mark.parametrize("result", [True, False, None, SimpleNamespace(success=True), SimpleNamespace(success=False)])
def test_skill_hold_invalidates_frame_and_preserves_action_result(result):
    context = FakeContext()
    context.run_action = Mock(return_value=result)
    scope = SimpleNamespace(invalidate_frame=Mock())
    combat = SimpleNamespace(frame=object(), input_scope=scope)
    action = CombatActions(context)
    action._role = SimpleNamespace(combat=combat)

    assert action.long_press_skill(2000) is result

    assert combat.frame is None
    scope.invalidate_frame.assert_called_once_with()
    context.run_action.assert_called_once_with("长按技能")


def test_skill_hold_invalidates_frame_when_native_action_raises():
    context = FakeContext()
    context.run_action = Mock(side_effect=RuntimeError("native failure"))
    combat = SimpleNamespace(frame=object(), input_scope=None)
    action = CombatActions(context)
    action._role = SimpleNamespace(combat=combat)

    with pytest.raises(RuntimeError, match="native failure"):
        action.long_press_skill(2000)

    assert combat.frame is None
    context.run_action.assert_called_once_with("长按技能")


@pytest.mark.parametrize("native_action", [
    {"type": "Swipe", "param": {
        "begin": [915, 690], "end": [915, 690], "duration": 1000,
        "begin_offset": [89, 23, 0, 0], "end_offset": [89, 23, 0, 0],
    }},
    {"type": "LongPressKey", "param": {"key": 76, "duration": 1000}},
])
def test_skill_hold_only_overrides_duration_for_touch_and_keyboard(native_action):
    class PreservingContext(FakeContext):
        # FakeContext uses shallow merges; emulate the Context's recursive
        # duration override without changing the production action contract.
        def override_pipeline(self, patch):
            self.pipeline_overrides.append(patch)
            self.node_data["长按技能"]["action"]["param"].update(
                patch["长按技能"]["action"]["param"],
            )

    context = PreservingContext(node_data={"长按技能": {"action": deepcopy(native_action)}})
    action = CombatActions(context)

    action.long_press_skill(2000)

    expected = deepcopy(native_action)
    expected["param"]["duration"] = 2000
    assert context.node_data["长按技能"]["action"] == expected
    assert context.pipeline_overrides == [
        {"长按技能": {"action": {"param": {"duration": 2000}}}},
    ]
    assert context.actions == ["长按技能"]
