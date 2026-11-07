"""Single shared helpers must not request ordinary input after warning Stop."""

from types import SimpleNamespace

import numpy as np
import pytest

from action.combat.actions.CombatActions import CombatActions
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from test_support.fakes import FakeContext, FakeJob, make_hit, make_miss


HELPERS = (
    ("ball_elimination_target", "消球2"),
    ("lens_lock", "锁定视角"),
    ("auxiliary_machine", "辅助机"),
)


class ScriptedHelper:
    """Control framework returns while running real capture and warning helpers."""

    def __init__(self, monkeypatch, *, stop_at=None, warning_hit=False,
                 protected=False, protect_on_warning=False, dodge_result=True,
                 action_result=True, fail_at=None):
        self.context = FakeContext()
        self.combat = CombatTask(self.context, CombatCheck())
        self.action = CombatActions(self.context)
        self.action._role = SimpleNamespace(combat=self.combat)
        self.previous_frame = np.zeros((2, 2, 3), dtype=np.uint8)
        self.current_frame = np.ones((2, 2, 3), dtype=np.uint8)
        self.combat.frame = self.previous_frame
        self.events = []
        self.stop_at = stop_at
        self.warning_hit = warning_hit
        self.protected = protected
        self.protect_on_warning = protect_on_warning
        self.dodge_result = dodge_result
        self.action_result = action_result
        self.fail_at = fail_at
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)
        monkeypatch.setattr(self.combat, "is_observation_protected", lambda: self.protected)

    def returned(self, boundary):
        self.events.append(("return", boundary))
        if self.stop_at == boundary:
            self.context.tasker.stopping = True
            self.events.append(("stop", boundary))

    def fail(self, boundary):
        if self.fail_at == boundary:
            raise RuntimeError(f"synthetic {boundary} failure")

    def capture(self):
        self.events.append(("capture_request",))
        job = FakeJob(image=self.current_frame)

        def read_image(wait=False):
            self.fail("capture")
            self.returned("capture")
            return self.current_frame

        job.get = read_image
        return job

    def recognize(self, name, image, pipeline_override=None):
        assert name == "自动闪避"
        assert image is self.current_frame
        self.events.append(("warning_request",))
        self.fail("warning")
        if self.protect_on_warning:
            self.protected = True
        self.returned("warning")
        return make_hit() if self.warning_hit else make_miss()

    def act(self, name, pipeline_override=None):
        self.events.append(("action_request", name, pipeline_override))
        self.context.actions.append(name)
        boundary = "dodge" if name == "闪避" else "ordinary"
        self.fail(boundary)
        self.returned(boundary)
        return self.dodge_result if boundary == "dodge" else self.action_result

    def call(self, helper, *, target=2):
        method = getattr(self.action, helper)
        return method(target=target) if helper == "ball_elimination_target" else method()

    def assert_no_request_after_stop(self):
        stopped = next(i for i, event in enumerate(self.events) if event[0] == "stop")
        assert not any(event[0].endswith("_request") for event in self.events[stopped + 1:])


@pytest.mark.parametrize("helper,node", HELPERS)
def test_entry_stop_prevents_observation_and_input_and_discards_frame(monkeypatch, helper, node):
    fight = ScriptedHelper(monkeypatch)
    fight.context.tasker.stopping = True

    assert fight.call(helper) is False

    assert fight.events == []
    assert fight.combat.frame is None


@pytest.mark.parametrize("helper,node", HELPERS)
@pytest.mark.parametrize("boundary,warning_hit", (
    ("capture", False), ("warning", False), ("warning", True), ("dodge", True),
))
def test_stop_during_shared_warning_prevents_new_ordinary_input(monkeypatch, helper, node,
                                                              boundary, warning_hit):
    fight = ScriptedHelper(monkeypatch, stop_at=boundary, warning_hit=warning_hit)

    assert fight.call(helper) is False

    fight.assert_no_request_after_stop()
    assert fight.context.actions == (["闪避"] if boundary == "dodge" else [])
    assert fight.combat.frame is None


@pytest.mark.parametrize("helper,node", HELPERS)
@pytest.mark.parametrize("mode", ("miss", "hit", "protected", "late_protection", "failed_dodge"))
def test_normal_helper_keeps_fresh_warning_then_original_input_order(monkeypatch, helper, node, mode):
    fight = ScriptedHelper(
        monkeypatch, warning_hit=mode in ("hit", "late_protection", "failed_dodge"),
        protected=mode == "protected", protect_on_warning=mode == "late_protection",
        dodge_result=mode != "failed_dodge",
    )

    assert fight.call(helper) is True

    expected = (["闪避"] if mode in ("hit", "failed_dodge") else []) + [node]
    assert fight.context.actions == expected
    requests = [event for event in fight.events if event[0].endswith("_request")]
    observations = [] if mode == "protected" else [("capture_request",), ("warning_request",)]
    assert requests == observations + [("action_request", name, None) for name in expected]
    assert fight.context.pipeline_overrides == []
    assert fight.combat.frame is None


@pytest.mark.parametrize("helper,node", HELPERS)
@pytest.mark.parametrize("stop_at", (None, "ordinary"))
@pytest.mark.parametrize("result", (True, False, SimpleNamespace(success=False)))
def test_already_requested_ordinary_action_preserves_native_return(monkeypatch, helper, node,
                                                                 stop_at, result):
    fight = ScriptedHelper(monkeypatch, stop_at=stop_at, action_result=result)

    assert fight.call(helper) is result

    assert fight.context.actions == [node]
    if stop_at:
        fight.assert_no_request_after_stop()
    assert fight.combat.frame is None


@pytest.mark.parametrize("helper,node", HELPERS)
@pytest.mark.parametrize("boundary", ("capture", "warning", "dodge", "ordinary"))
def test_failure_propagates_without_followup_input_and_discards_frame(monkeypatch, helper, node,
                                                                   boundary):
    fight = ScriptedHelper(monkeypatch, fail_at=boundary, warning_hit=boundary == "dodge")

    with pytest.raises(RuntimeError, match=f"synthetic {boundary} failure"):
        fight.call(helper)

    expected = {"capture": [], "warning": [], "dodge": ["闪避"], "ordinary": [node]}
    assert fight.context.actions == expected[boundary]
    assert fight.combat.frame is None


@pytest.mark.parametrize("target,node", ((0, "消球2"), (-3, "消球3"), (1, "消球1"), (8, "消球8")))
def test_ball_target_normalization_is_unchanged(monkeypatch, target, node):
    fight = ScriptedHelper(monkeypatch, warning_hit=True)

    assert fight.call("ball_elimination_target", target=target) is True

    assert fight.context.actions == ["闪避", node]


@pytest.mark.parametrize("target", (9, -9))
def test_invalid_ball_target_still_rejects_before_observation(monkeypatch, target):
    fight = ScriptedHelper(monkeypatch)

    assert fight.call("ball_elimination_target", target=target) is False

    assert fight.events == []
    assert fight.combat.frame is fight.previous_frame
