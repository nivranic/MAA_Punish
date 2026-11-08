"""Pianissimo ordinary timed windows check fresh pixels between native inputs."""

import pytest

from action.combat.roles.pianissimo import Pianissimo
from test_burst_warning_windows import PixelFight


def window(fight, kind, **kwargs):
    return (fight.role._continuous_tap_attack(**kwargs) if kind == "p1"
            else fight.role._p2_farm_burst())


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("number", [2, 5])
def test_warning_pixel_appearing_inside_window_is_handled_before_input(monkeypatch, kind, number):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, warning_inputs=(number,))
    assert window(fight, kind) is True
    expected = ["攻击"] * 20 if kind == "p1" else ["攻击", "消球2"] * 40
    expected.insert(number - 1, "闪避")
    assert fight.context.actions == expected
    assert [int(frame[0, 0, 0]) for frame in fight.warning_frames] == list(range(1, fight.inputs + 1))
    assert fight.clock.now == pytest.approx(100.5 if kind == "p1" else 101.0)
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("boundary", [("capture", 2), ("warning", 2), ("闪避", 1), ("攻击", 1)])
def test_inner_stop_does_not_dispatch_remaining_input(monkeypatch, kind, boundary):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, warning_inputs=(2,), stop_at=boundary)
    assert window(fight, kind) is False
    assert fight.context.actions == ["攻击"] + (["闪避"] if boundary[0] == "闪避" else [])
    fight.assert_no_request_after_stop()
    assert fight.combat.frame is None


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("boundary", [("capture", 2), ("warning", 2), ("攻击", 1)])
def test_new_software_protection_keeps_native_window_and_suppresses_warning(monkeypatch, kind, boundary):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, warning_inputs=(2, 5), protect_at=boundary)
    assert window(fight, kind) is True
    expected = ["攻击"] * 20 if kind == "p1" else ["攻击", "消球2"] * 40
    assert fight.context.actions == expected
    assert fight.counts["warning"] == (2 if boundary[0] == "warning" else 1)
    assert fight.combat.is_observation_protected()


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("native_result", [False, None])
def test_native_false_does_not_cancel_ordinary_window(monkeypatch, kind, native_result):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, native_result=native_result)
    assert window(fight, kind) is True
    assert fight.inputs == (20 if kind == "p1" else 80)


@pytest.mark.parametrize("kind", ["p1", "p2"])
@pytest.mark.parametrize("boundary", [("capture", 2), ("warning", 2), ("攻击", 2)])
def test_inner_failure_propagates_without_followup_input(monkeypatch, kind, boundary):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, fail_at=boundary)
    with pytest.raises(RuntimeError, match=f"synthetic {boundary[0]} failure"):
        window(fight, kind)
    assert fight.combat.frame is None
    assert fight.inputs == (2 if boundary[0] == "攻击" and kind == "p1" else
                            3 if boundary[0] == "攻击" else 1)


@pytest.mark.parametrize("phase", ["p1_farm", "p2_farm"])
def test_real_piano_phase_keeps_state_after_inner_warning_stop(monkeypatch, phase):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, stop_at=("warning", 5))
    fight.role.phase = phase
    assert fight.role.do_perform() is None
    assert fight.role.phase == phase and fight.role._farm_ticks == 7
    assert fight.inputs == 4
    fight.assert_no_request_after_stop()


def test_should_stop_observation_precedes_fresh_warning(monkeypatch):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, warning_inputs=(2,))
    callback_frames = []

    def observe():
        callback_frames.append(fight.action._get_image(fresh=True))
        fight.clock.now += 0.01
        fight.boundary("condition")
        return False

    assert window(fight, "p1", duration_s=0.06, should_stop=observe) is True
    kinds = [event[0] for event in fight.events]
    for index, kind in enumerate(kinds):
        if kind == "攻击":
            assert kinds[index - 1] in {"warning", "闪避"}
    assert fight.context.actions == ["攻击", "闪避", "攻击"]
    assert len(callback_frames) == len(fight.warning_frames) == 2
    assert all(warning is not prior and warning[0, 1, 0] > prior[0, 1, 0]
               for prior, warning in zip(callback_frames, fight.warning_frames))


@pytest.mark.parametrize("duration", [0.0, -0.1])
def test_nonpositive_window_keeps_single_warning_without_ordinary_input(monkeypatch, duration):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo)
    assert window(fight, "p1", duration_s=duration) is True
    assert fight.context.actions == []
    assert fight.counts["warning"] == 1


@pytest.mark.parametrize("kind", ["p1", "p2"])
def test_inner_warning_latency_consumes_original_deadline_without_reset(monkeypatch, kind):
    fight = PixelFight(monkeypatch, role_cls=Pianissimo, warning_seconds=0.1)
    assert window(fight, kind) is True
    warning_events = [event for event in fight.events if event[0] == "warning"]
    # First warning finishes before the one and only input-window deadline.
    deadline = warning_events[0][2] + (0.5 if kind == "p1" else 1.0)
    inputs = [event for event in fight.events if event[0] in {"攻击", "消球2"}]
    assert all(event[2] < deadline for event in inputs)
    assert 1 < fight.counts["warning"] < (20 if kind == "p1" else 80)
