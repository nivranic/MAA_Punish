"""The native switch click consumes its own fresh shared warning boundary."""

import pytest

from action.combat.core import switch
from action.combat.core.team import TeamSnapshot
from test_burst_warning_windows import PixelFight
from test_support.fakes import FakeJob, make_miss


class SwitchFight(PixelFight):
    def __init__(self, monkeypatch, *, arrive=True, **kwargs):
        super().__init__(monkeypatch, **kwargs)
        self.combat.team = TeamSnapshot("GeneralFight", "GeneralFight", "", "R")
        self.combat.roguelike3_identity_required = True
        self.arrival_calls = []
        self.stop_requested = False
        monkeypatch.setattr(self.context.tasker.controller, "post_click", self.click)

        def arrived(context, image, target_cls, **options):
            self.arrival_calls.append((target_cls, options, image))
            self.boundary("arrival")
            return arrive and bool(self.context.tasker.controller.clicks)

        monkeypatch.setattr(switch, "is_switch_arrived", arrived)
        monkeypatch.setattr(self.combat, "_correct_role_from_field",
                            lambda *args: ("合成辅助", "GeneralFight"))

    def click(self, x, y):
        self.context.tasker.controller.clicks.append((x, y))
        self.inputs += 1
        self.boundary("qte")
        return FakeJob()

    def call(self, **kwargs):
        return switch.attempt_switch_to_color(
            self.context, "B", "GeneralFight", combat=self.combat,
            attacker_callback=self.action.attack,
            should_stop=lambda: self.stop_requested, **kwargs,
        )


def test_pixel_warning_after_real_attacker_is_handled_before_native_qte(monkeypatch):
    fight = SwitchFight(monkeypatch, warning_inputs=(2,))
    assert fight.call() is True
    assert fight.context.actions == ["攻击", "闪避"]
    assert fight.context.tasker.controller.clicks == [(120, 220)]
    assert [int(frame[0, 0, 0]) for frame in fight.warning_frames] == [1, 2]
    assert all(target == "GeneralFight" and options == {"strict": True}
               for target, options, _ in fight.arrival_calls)
    assert [event[0] for event in fight.events].index("闪避") < [event[0] for event in fight.events].index("qte")


@pytest.mark.parametrize("boundary", [("攻击", 1), ("capture", 3), ("warning", 2), ("闪避", 1)])
def test_stop_returning_from_attack_or_new_warning_cancels_qte(monkeypatch, boundary):
    fight = SwitchFight(monkeypatch, warning_inputs=(2,), stop_at=boundary)
    assert fight.call() is False
    assert fight.context.tasker.controller.clicks == []
    fight.assert_no_request_after_stop()


@pytest.mark.parametrize("boundary", [("攻击", 1), ("capture", 3), ("warning", 2), ("闪避", 1)])
def test_new_software_protection_cancels_qte_and_further_warning(monkeypatch, boundary):
    fight = SwitchFight(monkeypatch, warning_inputs=(2,), protect_at=boundary)
    assert fight.call() is False
    assert fight.context.tasker.controller.clicks == []
    assert fight.counts["warning"] == (1 if boundary[0] in {"攻击", "capture"} else 2)
    assert fight.combat.is_observation_protected()


@pytest.mark.parametrize("native_result", [False, None])
def test_native_attacker_false_and_warning_miss_preserve_original_switch(monkeypatch, native_result):
    fight = SwitchFight(monkeypatch, native_result=native_result)
    assert fight.call() is True
    assert fight.context.actions == ["攻击"]
    assert fight.context.tasker.controller.clicks == [(120, 220)]


@pytest.mark.parametrize("boundary", [("capture", 3), ("warning", 2), ("攻击", 1)])
def test_boundary_exception_preserves_error_without_native_qte(monkeypatch, boundary):
    fight = SwitchFight(monkeypatch, fail_at=boundary)
    with pytest.raises(RuntimeError, match=f"synthetic {boundary[0]} failure"):
        fight.call()
    assert fight.context.tasker.controller.clicks == []


def test_should_stop_after_new_warning_is_consumed_before_qte(monkeypatch):
    fight = SwitchFight(monkeypatch)
    recognize = fight.recognize

    def stop_after_warning(name, image, pipeline_override=None):
        result = recognize(name, image, pipeline_override)
        if name == "自动闪避" and int(image[0, 0, 0]) == 2:
            fight.stop_requested = True
        return result

    monkeypatch.setattr(fight.context, "run_recognition", stop_after_warning)
    assert fight.call() is False
    assert fight.context.tasker.controller.clicks == []


def test_production_switch_to_color_consumes_boundary_stop_and_preserves_team(monkeypatch):
    fight = SwitchFight(monkeypatch, stop_at=("warning", 2))
    assert fight.combat.switch_to_color("B", attacker=fight.role) is False
    assert fight.combat.team.current == "R"
    assert fight.context.tasker.controller.clicks == []
    fight.assert_no_request_after_stop()


def test_production_switch_to_color_keeps_native_false_success(monkeypatch):
    fight = SwitchFight(monkeypatch, native_result=False, warning_inputs=(2,))
    assert fight.combat.switch_to_color("B", attacker=fight.role) is True
    assert fight.combat.team.current == "B"
    assert fight.context.actions == ["攻击", "闪避"]
    assert len(fight.context.tasker.controller.clicks) == 1


@pytest.mark.parametrize("mode", ["unknown_color", "missing_qte", "already_arrived"])
def test_original_color_and_identity_gates_run_before_new_warning(monkeypatch, mode):
    fight = SwitchFight(monkeypatch)
    color = "?" if mode == "unknown_color" else "B"
    if mode == "missing_qte":
        original = fight.recognize
        monkeypatch.setattr(fight.context, "run_recognition",
                            lambda name, image: make_miss() if name == "切换蓝色QTE" else original(name, image))
    if mode == "already_arrived":
        monkeypatch.setattr(switch, "is_switch_arrived", lambda *args, **kwargs: True)
    result = switch.attempt_switch_to_color(fight.context, color, "GeneralFight", combat=fight.combat)
    assert result is (mode == "already_arrived")
    assert "warning" not in fight.counts
    assert fight.context.tasker.controller.clicks == []


def test_warning_latency_does_not_reset_switch_timeout(monkeypatch):
    fight = SwitchFight(monkeypatch, arrive=False, warning_seconds=0.2)
    assert fight.call(verify_timeout=0.3) is False
    assert fight.context.tasker.controller.clicks == []
    assert fight.clock.now == pytest.approx(100.4)
