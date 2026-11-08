"""Fresh warning boundaries for each ordinary input, using synthetic frame pixels."""

import importlib

import numpy as np
import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.general_fight import GeneralFight
from test_support.fakes import FakeClock, FakeContext, FakeJob, make_hit, make_miss


class PixelFight:
    """Run real consumers; only SDK responses, clock and software protection vary.

    Pixel zero identifies the NEXT ordinary input, independently of how many
    screenshots or model calls occurred. Pixel one identifies each fresh image.
    """

    def __init__(self, monkeypatch, *, role_cls=GeneralFight, warning_inputs=(),
                 stop_at=None, protect_at=None, fail_at=None, native_result=True,
                 warning_seconds=0.0):
        self.context = FakeContext()
        self.clock = FakeClock(now=100.0)
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot.solo(role_cls.__name__)
        self.role = role_cls(self.combat, "R", role_cls.__name__)
        self.combat.roles = {"R": self.role}
        self.action = self.role.action
        self.role.phase = "farm" if role_cls is GeneralFight else "p1_farm"
        self.role._farm_ticks = 7
        self.role._clear_deadline = 1000.0
        self.warning_inputs = set(warning_inputs)
        self.stop_at, self.protect_at, self.fail_at = stop_at, protect_at, fail_at
        self.native_result, self.warning_seconds = native_result, warning_seconds
        self.protected = False
        self.events, self.warning_frames, self.counts = [], [], {}
        self.inputs = 0
        self.previous_frame = np.zeros((2, 2, 3), np.uint8)
        self.combat.frame = self.previous_frame
        module = importlib.import_module("action.combat.actions.CombatActions")
        monkeypatch.setattr(module.time, "monotonic", self.clock.monotonic)
        monkeypatch.setattr(module.time, "sleep", self.sleep)
        monkeypatch.setattr(self.role, "is_input_protected", lambda: self.protected)
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)

    def boundary(self, kind):
        number = self.counts.get(kind, 0) + 1
        self.counts[kind] = number
        token = kind, number
        self.events.append((kind, number, self.clock.now))
        if token == self.stop_at:
            self.context.tasker.stopping = True
            self.events.append(("stopped", number, self.clock.now))
        if token == self.protect_at:
            self.protected = True
        if token == self.fail_at:
            raise RuntimeError(f"synthetic {kind} failure")
        return number

    def capture(self):
        sequence = self.boundary("capture")
        image = np.full((2, 2, 3), self.inputs + 1, np.uint8)
        image[0, 1, 0] = sequence
        return FakeJob(image=image)

    def recognize(self, name, image, pipeline_override=None):
        if name == "自动闪避":
            self.warning_frames.append(image)
            self.clock.now += self.warning_seconds
            self.boundary("warning")
            return make_hit() if int(image[0, 0, 0]) in self.warning_inputs else make_miss()
        self.boundary(name)
        return make_hit() if name in {"战斗中", "切换蓝色QTE"} else make_miss()

    def act(self, name, pipeline_override=None):
        self.context.actions.append(name)
        if name == "攻击" or name.startswith("消球"):
            self.inputs += 1
        self.boundary(name)
        return self.native_result

    def sleep(self, seconds):
        self.clock.sleep(seconds)
        self.boundary("sleep")

    def burst(self, kind, *, count=5, interval=25, target=2):
        if kind == "attack":
            return self.action.attack_burst(count, interval)
        return self.action.ball_elimination_burst(target, count, interval)

    def assert_no_request_after_stop(self):
        index = next(i for i, event in enumerate(self.events) if event[0] == "stopped")
        assert self.events[index + 1:] == []


@pytest.mark.parametrize("kind,node", [("attack", "攻击"), ("ball", "消球2")])
@pytest.mark.parametrize("number", [2, 5])
def test_late_pixel_warning_is_handled_before_that_burst_input(monkeypatch, kind, node, number):
    fight = PixelFight(monkeypatch, warning_inputs=(number,))
    assert fight.burst(kind) is True
    expected = [node] * 5
    expected.insert(number - 1, "闪避")
    assert fight.context.actions == expected
    assert [int(frame[0, 0, 0]) for frame in fight.warning_frames] == [1, 2, 3, 4, 5]
    assert len({id(frame) for frame in fight.warning_frames}) == 5
    assert all(frame is not fight.previous_frame for frame in fight.warning_frames)
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind,node", [("attack", "攻击"), ("ball", "消球2")])
@pytest.mark.parametrize("boundary", [("capture", 2), ("warning", 2), ("闪避", 1)])
def test_inner_warning_stop_cancels_unrequested_burst_input(monkeypatch, kind, node, boundary):
    fight = PixelFight(monkeypatch, warning_inputs=(2,), stop_at=boundary)
    assert fight.burst(kind) is False
    assert fight.context.actions == [node] + (["闪避"] if boundary[0] == "闪避" else [])
    fight.assert_no_request_after_stop()
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind,node", [("attack", "攻击"), ("ball", "消球2")])
@pytest.mark.parametrize("boundary", ["warning", "ordinary"])
def test_new_software_protection_skips_later_warning_but_keeps_role_input(monkeypatch, kind, node, boundary):
    token = ("warning", 2) if boundary == "warning" else (node, 1)
    fight = PixelFight(monkeypatch, warning_inputs=(2, 5), protect_at=token)
    assert fight.burst(kind) is True
    assert fight.context.actions == [node] * 5
    assert fight.counts["warning"] == (2 if boundary == "warning" else 1)
    assert fight.combat.is_observation_protected()


@pytest.mark.parametrize("kind,node", [("attack", "攻击"), ("ball", "消球2")])
@pytest.mark.parametrize("native_result", [False, None])
def test_miss_and_native_false_keep_configured_burst_and_intervals(monkeypatch, kind, node, native_result):
    fight = PixelFight(monkeypatch, native_result=native_result)
    assert fight.burst(kind) is True
    assert fight.context.actions == [node] * 5
    assert fight.counts["warning"] == 5
    assert fight.clock.now == pytest.approx(100.125)


@pytest.mark.parametrize("kind,node", [("attack", "攻击"), ("ball", "消球2")])
@pytest.mark.parametrize("boundary", [("capture", 2), ("warning", 2)])
def test_inner_observation_error_propagates_and_discards_cached_frame(monkeypatch, kind, node, boundary):
    fight = PixelFight(monkeypatch, fail_at=boundary)
    with pytest.raises(RuntimeError, match=f"synthetic {boundary[0]} failure"):
        fight.burst(kind)
    assert fight.context.actions == [node]
    assert fight.combat.frame is None


@pytest.mark.parametrize("number", [2, 5, 7, 10])
def test_general_farm_consumes_both_real_bursts_and_late_pixel_warning(monkeypatch, number):
    fight = PixelFight(monkeypatch, warning_inputs=(number,))
    assert fight.role.do_perform() is None
    expected = ["攻击"] * 5 + ["消球2"] * 5
    expected.insert(number - 1, "闪避")
    assert fight.context.actions == expected
    assert fight.role.phase == "farm" and fight.role._farm_ticks == 8


@pytest.mark.parametrize("number", [2, 5, 7, 10])
def test_general_farm_inner_warning_stop_preserves_phase_and_counter(monkeypatch, number):
    fight = PixelFight(monkeypatch, stop_at=("warning", number))
    assert fight.role.do_perform() is None
    expected = ["攻击"] * 5 + ["消球2"] * 5
    assert fight.context.actions == expected[:number - 1]
    assert fight.role.phase == "farm" and fight.role._farm_ticks == 7
    fight.assert_no_request_after_stop()
