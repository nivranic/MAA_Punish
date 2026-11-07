"""Pianissimo ordinary windows use shared warning and propagate cancellation."""

import importlib

import numpy as np
import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.pianissimo import Pianissimo
from test_support.fakes import FakeClock, FakeContext, FakeJob, make_hit, make_miss


role_module = importlib.import_module("action.combat.roles.pianissimo")


class WindowFight:
    """Record framework boundaries while real role and shared actions execute."""

    def __init__(self, monkeypatch, *, warning=False, protected=False,
                 stop_at=None, fail_at=None, warning_seconds=0.0, phase="idle"):
        self.context = FakeContext()
        self.clock = FakeClock(now=100.0)
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot.solo("Pianissimo")
        self.role = Pianissimo(self.combat, "R", "Pianissimo")
        self.combat.roles = {"R": self.role}
        self.role.phase = phase
        self.role._farm_ticks = 7
        self.role._clear_deadline = 1000.0
        self.warning = warning
        self.warning_seconds = warning_seconds
        self.stop_at = stop_at
        self.fail_at = fail_at
        self.events = []
        self.counts = {}
        self.observation_frames = []
        self.stop_state = None
        self.observation_hits = set()
        monkeypatch.setattr(role_module.time, "monotonic", self.clock.monotonic)
        monkeypatch.setattr(role_module.time, "sleep", self.sleep)
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)
        self.override_pipeline = self.context.override_pipeline
        monkeypatch.setattr(self.context, "override_pipeline", self.override)
        monkeypatch.setattr(self.role.action, "count_signal_balls", self.count_balls)
        monkeypatch.setattr(self.role.action, "Arrange_Signal_Balls", self.arrange)
        if protected:
            monkeypatch.setattr(self.combat, "is_observation_protected", lambda: True)

    def state(self):
        return (self.role.phase, self.role._farm_ticks, self.role._burst_ticks,
                self.role._clear_deadline, self.role._p2_farm_next)

    def boundary(self, kind, key=None):
        pair = (kind, key)
        self.counts[pair] = self.counts.get(pair, 0) + 1
        number = self.counts[pair]
        self.events.append((kind, key, number, self.clock.now))
        token = (kind, key, number)
        if token == self.stop_at:
            self.context.tasker.stopping = True
            self.stop_state = self.state()
            self.events.append(("stop", token, self.clock.now))
        if token == self.fail_at:
            raise RuntimeError("synthetic boundary failure")
        return number

    def capture(self):
        number = self.boundary("capture")
        return FakeJob(image=np.full((2, 2, 3), number, dtype=np.uint8))

    def recognize(self, name, image, pipeline_override=None):
        self.observation_frames.append((name, image))
        if name == "自动闪避":
            self.clock.now += self.warning_seconds
        self.boundary("recognition", name)
        hit = self.warning if name == "自动闪避" else name in self.observation_hits
        return make_hit() if hit else make_miss()

    def act(self, name, pipeline_override=None):
        self.boundary("input_request", name)
        self.context.actions.append(name)
        self.boundary("input_return", name)
        return True

    def override(self, patch):
        self.boundary("override", next(iter(patch)))
        self.override_pipeline(patch)

    def sleep(self, seconds):
        self.clock.sleep(seconds)
        self.boundary("sleep")

    def count_balls(self):
        self.boundary("count_balls")
        return 3

    def arrange(self):
        self.boundary("arrange")
        return 2

    def window(self, kind, **kwargs):
        if kind == "p1":
            return self.role._continuous_tap_attack(**kwargs)
        return self.role._p2_farm_burst()

    def assert_cancelled(self, *, phase=False, window_frame=True):
        stop = next(i for i, event in enumerate(self.events) if event[0] == "stop")
        assert not any(event[0] in ("capture", "recognition", "input_request", "count_balls", "arrange")
                       for event in self.events[stop + 1:])
        if phase:
            assert self.state() == self.stop_state
        if window_frame:
            assert self.combat.frame is None


@pytest.mark.parametrize("kind,duration,attacks", [("p1", 0.5, 20), ("p2", 1.0, 40)])
@pytest.mark.parametrize("mode", ["hit", "miss", "protected"])
def test_window_warns_once_before_original_inputs(monkeypatch, kind, duration, attacks, mode):
    fight = WindowFight(monkeypatch, warning=mode == "hit", protected=mode == "protected")
    assert fight.window(kind) is True
    expected = (["闪避"] if mode == "hit" else [])
    expected += (["攻击"] if kind == "p1" else ["攻击", "消球2"]) * attacks
    assert fight.context.actions == expected
    assert fight.counts.get(("recognition", "自动闪避"), 0) == (0 if mode == "protected" else 1)
    assert fight.clock.now == pytest.approx(100.0 + duration)
    assert fight.combat.frame is None
    for patch in fight.context.pipeline_overrides:
        assert next(iter(patch.values())) == {"pre_delay": 0, "post_delay": 0, "rate_limit": 0}


@pytest.mark.parametrize("kind,duration,attacks", [("p1", 0.5, 20), ("p2", 1.0, 40)])
def test_slow_warning_does_not_consume_input_window(monkeypatch, kind, duration, attacks):
    fight = WindowFight(monkeypatch, warning_seconds=2.5)
    assert fight.window(kind) is True
    assert fight.clock.now == pytest.approx(102.5 + duration)
    inputs = [event for event in fight.events if event[0] == "input_request" and event[1] == "攻击"]
    assert len(inputs) == attacks
    assert inputs[0][3] == pytest.approx(102.5)
    assert inputs[-1][3] == pytest.approx(102.5 + duration - 0.025)
    assert fight.counts[("recognition", "自动闪避")] == 1


@pytest.mark.parametrize("kind", ["p1", "p2"])
def test_preexisting_stop_does_not_observe_or_input(monkeypatch, kind):
    fight = WindowFight(monkeypatch)
    fight.combat.frame = object()
    fight.context.tasker.stopping = True
    assert fight.window(kind) is False
    assert fight.events == []
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("warning", [False, True])
@pytest.mark.parametrize("boundary", [("capture", None, 1), ("recognition", "自动闪避", 1)])
def test_stop_during_warning_does_not_start_window(monkeypatch, kind, warning, boundary):
    fight = WindowFight(monkeypatch, warning=warning, stop_at=boundary)
    assert fight.window(kind) is False
    fight.assert_cancelled()
    assert not fight.context.actions


@pytest.mark.parametrize("kind", ["p1", "p2"])
def test_stop_after_warning_dodge_does_not_start_attack(monkeypatch, kind):
    fight = WindowFight(monkeypatch, warning=True, stop_at=("input_return", "闪避", 1))
    assert fight.window(kind) is False
    fight.assert_cancelled()
    assert fight.context.actions == ["闪避"]


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("number", [1, 3])
def test_attack_return_stop_cancels_remainder_including_ball(monkeypatch, kind, number):
    fight = WindowFight(monkeypatch, stop_at=("input_return", "攻击", number))
    assert fight.window(kind) is False
    fight.assert_cancelled()
    assert fight.context.actions[-1] == "攻击"


@pytest.mark.parametrize("number", [1, 3, 40])
def test_p2_ball_return_stop_cancels_remainder(monkeypatch, number):
    fight = WindowFight(monkeypatch, stop_at=("input_return", "消球2", number))
    assert fight.window("p2") is False
    fight.assert_cancelled()


@pytest.mark.parametrize("kind,last", [("p1", 20), ("p2", 40)])
@pytest.mark.parametrize("at_last", [False, True])
def test_wait_stop_is_cancelled_even_after_final_input(monkeypatch, kind, last, at_last):
    fight = WindowFight(monkeypatch, stop_at=("sleep", None, last if at_last else 1))
    assert fight.window(kind) is False
    fight.assert_cancelled()


@pytest.mark.parametrize("method,node", [("_tap_attack", "攻击"), ("_tap_ball", "消球2")])
@pytest.mark.parametrize("boundary", ["entry", "override", "input_return"])
def test_raw_input_helpers_cancel_and_invalidate_frame(monkeypatch, method, node, boundary):
    fight = WindowFight(monkeypatch, stop_at=(boundary, node, 1))
    fight.combat.frame = object()
    if boundary == "entry":
        fight.context.tasker.stopping = True
    args = (2,) if method == "_tap_ball" else ()
    assert getattr(fight.role, method)(*args) is False
    if boundary == "entry":
        assert fight.events == []
    else:
        fight.assert_cancelled()
    assert fight.combat.frame is None


@pytest.mark.parametrize("phase", ["idle", "p1_farm", "p1_clear", "p2_farm"])
@pytest.mark.parametrize("boundary", ["input_return", "sleep"])
def test_four_phases_propagate_window_cancellation(monkeypatch, phase, boundary):
    stop_at = (boundary, "攻击" if boundary == "input_return" else None, 1)
    fight = WindowFight(monkeypatch, phase=phase, stop_at=stop_at)
    fight.role.do_perform()
    fight.assert_cancelled(phase=True)


@pytest.mark.parametrize("phase,node", [
    ("idle", "检查希声2阶段"), ("p1_farm", "检查希声2阶段"),
    ("p1_clear", "检查希声2阶段"), ("p2_farm", "检查希声红球"),
])
def test_four_phases_do_not_advance_after_observation_stop(monkeypatch, phase, node):
    fight = WindowFight(monkeypatch, phase=phase, stop_at=("recognition", node, 1))
    fight.observation_hits.add(node)
    fight.role.do_perform()
    # This is outside the new input window. The existing observer may retain
    # its valid frame; only window/helper cleanup is part of this candidate.
    fight.assert_cancelled(phase=True, window_frame=False)


@pytest.mark.parametrize("stage", ["capture", "recognition"])
@pytest.mark.parametrize("condition_hit", [False, True])
def test_should_stop_observation_cancellation_is_not_plain_condition(monkeypatch, stage, condition_hit):
    boundary = ("capture", None, 2) if stage == "capture" else ("recognition", "检查希声红球", 1)
    fight = WindowFight(monkeypatch, stop_at=boundary)
    if condition_hit:
        fight.observation_hits.add("检查希声红球")

    def observe():
        # Force an actual fresh frame for the existing optional observation.
        fight.role.action._invalidate_frame()
        result = fight.role._red_ball_ready()
        fight.events.append(("callback_return", result, fight.clock.now))
        return result

    assert fight.window("p1", should_stop=observe) is False
    stop = next(i for i, event in enumerate(fight.events) if event[0] == "stop")
    returned = next(i for i, event in enumerate(fight.events) if event[0] == "callback_return")
    # The opaque callback's old check_status may finish its own recognition
    # after a capture-side Stop. No new input or follow-up begins after return.
    assert not any(event[0] == "input_request" for event in fight.events[stop + 1:])
    assert not any(event[0] in ("capture", "recognition", "input_request")
                   for event in fight.events[returned + 1:])
    assert fight.combat.frame is None


def test_plain_should_stop_condition_ends_normally_without_attack(monkeypatch):
    fight = WindowFight(monkeypatch)
    assert fight.window("p1", should_stop=lambda: True) is True
    assert not fight.context.actions
    assert not fight.context.tasker.stopping
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind", ["p1", "p2"])
def test_next_observation_does_not_reuse_preinput_warning_frame(monkeypatch, kind):
    fight = WindowFight(monkeypatch)
    assert fight.window(kind) is True
    warning_frame = fight.observation_frames[0][1]
    fight.role.action.check_status("post_window_probe")
    assert fight.observation_frames[-1][0] == "post_window_probe"
    assert fight.observation_frames[-1][1] is not warning_frame
    assert fight.counts[("capture", None)] == 2


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("boundary", [("capture", None, 1), ("recognition", "自动闪避", 1),
                                      ("input_request", "攻击", 1), ("override", "攻击", 1)])
def test_window_failure_propagates_without_finally_inputs(monkeypatch, kind, boundary):
    fight = WindowFight(monkeypatch, fail_at=boundary)
    fight.combat.frame = object()
    with pytest.raises(RuntimeError, match="synthetic boundary failure"):
        fight.window(kind)
    assert fight.combat.frame is None
    assert not fight.context.actions


@pytest.mark.parametrize("phase,expected", [("idle", "p1_farm"), ("p1_farm", "p1_farm"),
                                            ("p1_clear", "p1_clear"), ("p2_farm", "p2_farm")])
def test_normal_four_phases_keep_existing_progress(monkeypatch, phase, expected):
    fight = WindowFight(monkeypatch, phase=phase)
    fight.role.do_perform()
    assert fight.role.phase == expected
    assert fight.role._farm_ticks == (0 if phase == "idle" else 8 if phase == "p1_farm" else 7)
    assert "攻击" in fight.context.actions
    if phase == "p1_clear":
        assert fight.context.actions[-1] == "消球2"
    if phase == "p2_farm":
        assert fight.context.actions.count("攻击") == fight.context.actions.count("消球2")
