"""QTE dispatch keeps shared warning/Stop boundaries and legal protected input."""

from types import SimpleNamespace

import numpy as np
import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.aegis import Aegis
from action.combat.roles.inverse_crown import InverseCrown
from test_support.fakes import FakeContext, FakeJob, make_hit, make_miss


class QteFight:
    """Record SDK boundaries while the real role and shared helpers execute."""

    def __init__(self, monkeypatch, *, role_cls=InverseCrown, warning_at=(),
                 stop_at=None, protect_at=None, unprotect_at=None, fail_at=None,
                 qte1_result=True, qte2_result=True, dodge_result=True):
        self.context = FakeContext()
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot.solo(role_cls.__name__)
        self.role = role_cls(self.combat, "R", role_cls.__name__)
        self.combat.roles = {"R": self.role}
        self.role.phase = "farm" if role_cls is InverseCrown else "combo"
        self.action = self.role.action
        self.warning_at = warning_at
        self.stop_at = stop_at
        self.protect_at = protect_at
        self.unprotect_at = unprotect_at
        self.fail_at = fail_at
        self.results = {"qte1": qte1_result, "qte2": qte2_result, "闪避": dodge_result}
        self.counts = {}
        self.events = []
        self.warning_frames = []
        self.previous_frame = np.zeros((2, 2, 3), dtype=np.uint8)
        self.combat.frame = self.previous_frame
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)

    def boundary(self, kind):
        number = self.counts.get(kind, 0) + 1
        self.counts[kind] = number
        token = (kind, number)
        self.events.append(token)
        if token == self.stop_at:
            self.context.tasker.stopping = True
            self.events.append(("stopped", number))
        if token == self.protect_at:
            self.role.phase = "special_press"
            self.events.append(("protected", number))
        if token == self.unprotect_at:
            self.role.phase = "farm"
            self.events.append(("unprotected", number))
        if token == self.fail_at:
            raise RuntimeError(f"synthetic {kind} failure")
        return number

    def capture(self):
        number = self.boundary("capture")
        return FakeJob(image=np.full((2, 2, 3), number, dtype=np.uint8))

    def recognize(self, name, image, pipeline_override=None):
        assert name == "自动闪避"
        self.warning_frames.append(image)
        number = self.boundary("warning")
        return make_hit() if number in self.warning_at else make_miss()

    def act(self, name, pipeline_override=None):
        self.context.actions.append(name)
        self.boundary(name)
        return self.results.get(name, True)

    def assert_no_request_after_stop(self):
        index = next(i for i, event in enumerate(self.events) if event[0] == "stopped")
        assert self.events[index + 1:] == []


def test_warning_appearing_after_qte1_is_handled_before_qte2(monkeypatch):
    fight = QteFight(monkeypatch, warning_at=(2,))

    assert fight.action.use_qte() is True

    assert fight.context.actions == ["qte1", "闪避", "qte2"]
    assert fight.events.index(("qte1", 1)) < fight.events.index(("warning", 2))
    assert fight.events.index(("warning", 2)) < fight.events.index(("闪避", 1))
    assert fight.events.index(("闪避", 1)) < fight.events.index(("qte2", 1))
    assert len(fight.warning_frames) == 2
    assert all(image is not fight.previous_frame for image in fight.warning_frames)
    assert fight.warning_frames[0] is not fight.warning_frames[1]
    assert fight.combat.frame is None


@pytest.mark.parametrize("boundary", [("capture", 1), ("warning", 1), ("闪避", 1), ("qte1", 1)])
@pytest.mark.parametrize("warning_hit", [False, True])
def test_stop_during_blocking_return_cancels_unrequested_qte(monkeypatch, boundary, warning_hit):
    # A dodge return can only be reached when a warning hits.
    fight = QteFight(monkeypatch, warning_at=(1,) if warning_hit or boundary[0] == "闪避" else (),
                     stop_at=boundary)

    assert fight.action.use_qte() is False

    expected = []
    if boundary[0] == "闪避" or (boundary[0] == "qte1" and warning_hit):
        expected.append("闪避")
    if boundary[0] == "qte1":
        expected.append("qte1")
    assert fight.context.actions == expected
    fight.assert_no_request_after_stop()
    assert fight.combat.frame is None


def test_next_helper_entry_after_stop_does_not_observe_or_dispatch(monkeypatch):
    fight = QteFight(monkeypatch, stop_at=("qte1", 1))

    assert fight.action.use_qte() is False
    previous_events = list(fight.events)
    assert fight.action.use_qte() is False

    assert fight.events == previous_events
    assert fight.context.actions == ["qte1"]
    assert fight.combat.frame is None


@pytest.mark.parametrize("phase,skill_sub", [
    ("special_press", ""), ("special_attack", ""),
    ("skill", "cast"), ("skill", "cage"), ("skill", "finish"),
])
def test_inverse_protection_skips_warning_but_keeps_legal_qte(monkeypatch, phase, skill_sub):
    fight = QteFight(monkeypatch, warning_at=(1, 2))
    fight.role.phase = phase
    fight.role._skill_sub = skill_sub
    assert fight.combat.is_observation_protected()

    assert fight.action.use_qte() is True

    assert fight.events == [("qte1", 1), ("qte2", 1)]
    assert fight.context.actions == ["qte1", "qte2"]
    assert fight.combat.frame is None


@pytest.mark.parametrize("boundary", [("capture", 1), ("warning", 1), ("闪避", 1), ("qte1", 1)])
def test_new_inverse_protection_keeps_qte_and_stops_further_warning(monkeypatch, boundary):
    fight = QteFight(monkeypatch, warning_at=(1, 2), protect_at=boundary)

    assert fight.action.use_qte() is True

    expected_dodge = boundary[0] in {"闪避", "qte1"}
    assert fight.context.actions == (["闪避"] if expected_dodge else []) + ["qte1", "qte2"]
    assert fight.counts["capture"] == 1
    assert fight.counts.get("warning", 0) == (0 if boundary[0] == "capture" else 1)
    assert fight.combat.is_observation_protected()
    assert fight.combat.frame is None


def test_inverse_protection_exit_between_qtes_resumes_shared_warning(monkeypatch):
    fight = QteFight(monkeypatch, warning_at=(1,), unprotect_at=("qte1", 1))
    fight.role.phase = "special_attack"

    assert fight.action.use_qte() is True

    assert fight.context.actions == ["qte1", "闪避", "qte2"]
    assert fight.counts["capture"] == fight.counts["warning"] == 1
    assert not fight.combat.is_observation_protected()
    assert fight.combat.frame is None


@pytest.mark.parametrize("qte1_result", [True, False, None])
@pytest.mark.parametrize("qte2_result", [True, False, None, SimpleNamespace(success=False)])
def test_miss_preserves_qte_order_and_exact_last_native_result(monkeypatch, qte1_result, qte2_result):
    fight = QteFight(monkeypatch, qte1_result=qte1_result, qte2_result=qte2_result)

    assert fight.action.use_qte() is qte2_result

    assert fight.context.actions == ["qte1", "qte2"]
    assert fight.counts["warning"] == 2
    assert fight.context.pipeline_overrides == []
    assert fight.combat.frame is None


@pytest.mark.parametrize("qte2_result", [True, False, None, SimpleNamespace(success=False)])
def test_stop_during_already_requested_last_qte_preserves_its_result(monkeypatch, qte2_result):
    fight = QteFight(monkeypatch, stop_at=("qte2", 1), qte2_result=qte2_result)

    assert fight.action.use_qte() is qte2_result

    assert fight.context.actions == ["qte1", "qte2"]
    fight.assert_no_request_after_stop()
    assert fight.combat.frame is None


def test_failed_dodge_is_not_treated_as_stop(monkeypatch):
    fight = QteFight(monkeypatch, warning_at=(1, 2), dodge_result=False)

    assert fight.action.use_qte() is True

    assert fight.context.actions == ["闪避", "qte1", "闪避", "qte2"]
    assert fight.combat.frame is None


@pytest.mark.parametrize("boundary,expected", [
    (("capture", 1), []), (("warning", 1), []), (("闪避", 1), ["闪避"]),
    (("qte1", 1), ["qte1"]), (("capture", 2), ["qte1"]),
    (("warning", 2), ["qte1"]), (("qte2", 1), ["qte1", "qte2"]),
])
def test_failure_propagates_and_invalidates_frame_without_followup(monkeypatch, boundary, expected):
    fight = QteFight(monkeypatch, fail_at=boundary,
                     warning_at=(1,) if boundary[0] == "闪避" else ())

    with pytest.raises(RuntimeError, match=f"synthetic {boundary[0]} failure"):
        fight.action.use_qte()

    assert fight.context.actions == expected
    assert fight.combat.frame is None


@pytest.mark.parametrize("warning_number", [2, 3, 4])
def test_aegis_actual_double_helper_checks_each_qte_without_role_change(monkeypatch, warning_number):
    fight = QteFight(monkeypatch, role_cls=Aegis, warning_at=(warning_number,))
    fight.role._combo_clear_ticks = 3
    fight.role._combo_ball_count = 4
    # The ordinary branch sees ball-count progress and calls use_qte twice.
    monkeypatch.setattr(fight.action, "count_signal_balls", lambda: 3)

    assert fight.role.do_perform() is None

    expected = ["qte1", "qte2", "qte1", "qte2"]
    expected.insert(warning_number - 1, "闪避")
    assert fight.context.actions == expected + ["辅助机"]
    assert fight.counts["warning"] == 5  # Four QTE boundaries plus the existing auxiliary helper.
    assert fight.role.phase == "loop" and fight.role._loop_ticks == 1
    assert fight.combat.frame is None


@pytest.mark.parametrize("boundary", [("warning", 1), ("qte1", 1), ("qte2", 1)])
def test_aegis_second_helper_cannot_dispatch_after_stop(monkeypatch, boundary):
    fight = QteFight(monkeypatch, role_cls=Aegis, stop_at=boundary)
    fight.role._combo_clear_ticks = 3
    fight.role._combo_ball_count = 4
    monkeypatch.setattr(fight.action, "count_signal_balls", lambda: 3)

    assert fight.role.do_perform() is None

    assert fight.context.actions == {"warning": [], "qte1": ["qte1"],
                                     "qte2": ["qte1", "qte2"]}[boundary[0]]
    fight.assert_no_request_after_stop()
    assert fight.combat.frame is None


@pytest.mark.parametrize("skill_sub,expected", [
    ("cage", ["长按技能", "辅助机", "qte1", "qte2"]),
    ("finish", ["辅助机", "qte1", "qte2"]),
])
def test_inverse_actual_protected_skill_consumer_keeps_legal_qte(monkeypatch, skill_sub, expected):
    fight = QteFight(monkeypatch, warning_at=(1, 2))
    fight.role.phase = "skill"
    fight.role._skill_sub = skill_sub
    fight.role._skill_switch_after = True
    assert fight.combat.is_observation_protected()

    assert fight.role.do_perform() is None

    assert fight.context.actions == expected
    assert "capture" not in fight.counts and "warning" not in fight.counts
    assert fight.role.phase == "switch" and fight.role._post_ult_qte_used
    assert fight.combat.frame is None


def test_aegis_stalled_balls_do_not_dodge_after_qte_warning_stop(monkeypatch):
    """Permanent form of the independently failed stalled-ball consumer probe."""
    fight = QteFight(monkeypatch, role_cls=Aegis, warning_at=(1,), stop_at=("warning", 1))
    fight.role._combo_clear_ticks = 3
    fight.role._combo_ball_count = 4
    monkeypatch.setattr(fight.action, "count_signal_balls", lambda: 4)

    assert fight.role.do_perform() is None

    assert fight.context.actions == []
    fight.assert_no_request_after_stop()
    assert fight.role.phase == "combo" and fight.role._loop_ticks == 0


@pytest.mark.parametrize("ball_count", [3, 4])
@pytest.mark.parametrize("boundary", ["warning", "qte1", "qte2", "count", "second_warning", "second_qte1"])
def test_aegis_combo_propagates_stop_before_new_input_and_phase_advance(monkeypatch, ball_count, boundary):
    stalled = ball_count == 4
    stop_at = {
        "warning": ("warning", 1), "qte1": ("qte1", 1), "qte2": ("qte2", 1),
        "count": ("count", 1), "second_warning": ("warning", 4 if stalled else 3),
        "second_qte1": ("qte1", 2),
    }[boundary]
    fight = QteFight(monkeypatch, role_cls=Aegis, stop_at=stop_at)
    fight.role._combo_clear_ticks = 3
    fight.role._combo_ball_count = 4

    def count():
        fight.boundary("count")
        return ball_count

    monkeypatch.setattr(fight.action, "count_signal_balls", count)

    assert fight.role.do_perform() is None

    expected = [] if boundary == "warning" else ["qte1"]
    if boundary in {"qte2", "count", "second_warning", "second_qte1"}:
        expected.append("qte2")
    if stalled and boundary in {"second_warning", "second_qte1"}:
        expected += ["闪避", "消球2"]
    if boundary == "second_qte1":
        expected.append("qte1")
    assert fight.context.actions == expected
    fight.assert_no_request_after_stop()
    assert fight.role.phase == "combo" and fight.role._loop_ticks == 0
    assert fight.role._combo_clear_ticks == 3
    assert fight.combat.frame is None


@pytest.mark.parametrize("boundary", [("闪避", 1), ("warning", 3)])
def test_aegis_stalled_middle_input_or_ball_warning_stop_cannot_advance(monkeypatch, boundary):
    fight = QteFight(monkeypatch, role_cls=Aegis, stop_at=boundary)
    fight.role._combo_clear_ticks = 3
    fight.role._combo_ball_count = 4
    monkeypatch.setattr(fight.action, "count_signal_balls", lambda: 4)

    assert fight.role.do_perform() is None

    assert fight.context.actions == ["qte1", "qte2", "闪避"]
    fight.assert_no_request_after_stop()
    assert fight.role.phase == "combo" and fight.role._loop_ticks == 0
    assert fight.combat.frame is None


@pytest.mark.parametrize("ball_count", [3, 4])
@pytest.mark.parametrize("native_result", [True, False])
def test_aegis_normal_combo_keeps_original_branches_even_when_native_qte_is_false(
    monkeypatch, ball_count, native_result
):
    fight = QteFight(monkeypatch, role_cls=Aegis, qte1_result=native_result, qte2_result=native_result)
    fight.role._combo_clear_ticks = 3
    fight.role._combo_ball_count = 4
    monkeypatch.setattr(fight.action, "count_signal_balls", lambda: ball_count)

    assert fight.role.do_perform() is None

    middle = ["闪避", "消球2"] if ball_count == 4 else []
    assert fight.context.actions == ["qte1", "qte2"] + middle + ["qte1", "qte2", "辅助机"]
    assert fight.counts["warning"] == (6 if ball_count == 4 else 5)
    assert fight.role.phase == "loop" and fight.role._loop_ticks == 1
    assert fight.role._combo_clear_ticks == 3
    assert fight.combat.frame is None
