"""Ordinary CrimsonWeave probes must share warning and stop boundaries."""

import numpy as np
import pytest

from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles import crimson_weave as module
from action.combat.roles.crimson_weave import CrimsonWeave
from test_support.fakes import FakeClock, FakeContext, FakeJob, make_hit, make_miss


ORDINARY_PHASES = (
    "small_dodge", "small_attack", "great_ball",
    "great_build_dodge", "great_build_attack",
)
WAIT_PHASES = ("small_ult_wait", "great_ult_wait")


class ScriptedFight:
    """Real role/actions with synthetic frames and recorded framework calls."""

    def __init__(self, monkeypatch, phase, *, warning_at=(), stop_at=None,
                 mode_at=None, great_light=300, hit_at=None):
        self.clock = FakeClock(now=100.0)
        monkeypatch.setattr(module.time, "monotonic", self.clock.monotonic)
        self.context = FakeContext()
        self.combat = CombatTask(self.context, CombatCheck())
        self.combat.team = TeamSnapshot(R="CrimsonWeave", B="", Y="", current="R")
        self.role = CrimsonWeave(self.combat, "R", "CrimsonWeave")
        self.combat.roles = {"R": self.role}
        self.role.phase = phase
        self.events = []
        self.captures = 0
        self.warnings = 0
        self.attacks = 0
        self.node_calls = {}
        self.stop_phase = None
        self.warning_at = warning_at
        self.stop_at = stop_at
        self.mode_at = mode_at or {}
        self.great_light = great_light
        self.hit_at = hit_at or {}
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)

    def stop_if_requested(self, boundary, count):
        if self.stop_at == (boundary, count):
            self.context.tasker.stopping = True
            self.stop_phase = self.role.phase
            self.events.append(("stop", boundary, count))

    def capture(self):
        self.captures += 1
        self.clock.now += 0.01
        self.events.append(("capture", self.captures))
        self.stop_if_requested("capture", self.captures)
        return FakeJob(image=np.full((2, 2, 3), self.captures, dtype=np.uint8))

    def recognize(self, name, image, pipeline_override=None):
        frame = int(image[0, 0, 0])
        self.events.append(("recognition", name, frame))
        self.clock.now += 0.01
        self.node_calls[name] = self.node_calls.get(name, 0) + 1
        occurrence = self.node_calls[name]
        self.stop_if_requested("recognition", (name, occurrence))
        if name == "自动闪避":
            self.warnings += 1
            self.stop_if_requested("warning", self.warnings)
            return make_hit() if self.warnings in self.warning_at else make_miss()
        mode = self.mode_at.get(frame, "great")
        if name == module._GREAT_LIGHT_NODE:
            return make_hit() if mode in ("great", "both") else make_miss()
        if name == module._SMALL_LIGHT_NODE:
            return make_hit() if mode in ("small", "both") else make_miss()
        if name in (module._GREAT_LIGHT_TEXT_NODE, module._SMALL_LIGHT_TEXT_NODE):
            result = make_hit()
            result.best_result.text = str(
                self.great_light if name == module._GREAT_LIGHT_TEXT_NODE else 25
            )
            return result
        return make_hit() if occurrence in self.hit_at.get(name, ()) else make_miss()

    def act(self, name, pipeline_override=None):
        self.events.append(("action", name))
        self.context.actions.append(name)
        # Synthetic progress only prevents the real ordinary loop from waiting.
        # It is not a model of native inference cost or gameplay throughput.
        self.clock.now += 0.2
        if name == "攻击":
            self.attacks += 1
            self.stop_if_requested("attack", self.attacks)
        return True

    def assert_no_work_after_stop(self):
        stopped = next(i for i, event in enumerate(self.events) if event[0] == "stop")
        assert not any(event[0] in ("action", "recognition")
                       for event in self.events[stopped + 1:])


@pytest.mark.parametrize("phase", ORDINARY_PHASES)
def test_ordinary_phase_detects_warning_between_two_probe_attacks(monkeypatch, phase):
    fight = ScriptedFight(monkeypatch, phase, warning_at=(2,))

    fight.role.do_perform()

    # The first warning misses; the new warning must be handled before the
    # second ordinary supplement, not deferred until a later combo action.
    assert fight.context.actions[:3] == ["攻击", "闪避", "攻击"]
    first_light = next(i for i, event in enumerate(fight.events)
                       if event[:2] == ("recognition", module._GREAT_LIGHT_NODE))
    assert [event[1] for event in fight.events[:first_light]
            if event[0] == "recognition"] == ["自动闪避", "自动闪避"]


@pytest.mark.parametrize("phase", ORDINARY_PHASES)
@pytest.mark.parametrize("boundary", ("capture", "warning", "attack"))
@pytest.mark.parametrize("supplement", (1, 2))
def test_stop_in_ordinary_supplement_ends_real_phase_dispatch(
    monkeypatch, phase, boundary, supplement,
):
    fight = ScriptedFight(monkeypatch, phase, warning_at=(1, 2),
                          stop_at=(boundary, supplement))

    fight.role.do_perform()

    assert fight.context.tasker.stopping
    fight.assert_no_work_after_stop()
    assert not set(fight.context.actions) & {"长按闪避", "长按攻击", "技能", "辅助机", "qte1", "qte2"}


@pytest.mark.parametrize("phase", WAIT_PHASES)
def test_ultimate_wait_keeps_raw_supplements_without_new_warning(monkeypatch, phase):
    fight = ScriptedFight(monkeypatch, phase, warning_at=(1, 2))

    fight.role.do_perform()

    assert fight.context.actions == ["攻击", "攻击"]
    assert fight.warnings == 0
    # Preserve the original raw supplement between the two bar recognitions.
    observed = [event[:2] for event in fight.events if event[0] != "capture"]
    assert observed[:4] == [
        ("action", "攻击"), ("recognition", module._GREAT_LIGHT_NODE),
        ("action", "攻击"), ("recognition", module._SMALL_LIGHT_NODE),
    ]


def test_probe_reads_both_bars_and_ocr_from_frame_after_guarded_supplement(monkeypatch):
    fight = ScriptedFight(monkeypatch, "great_ball", warning_at=(1,),
                          mode_at={1: "great", 2: "small"})

    assert fight.role._probe_sword_mode() == ("small", None, 25)

    observations = [event for event in fight.events
                    if event[0] == "recognition" and event[1] != "自动闪避"]
    assert [(event[1], event[2]) for event in observations] == [
        (module._GREAT_LIGHT_NODE, 2),
        (module._SMALL_LIGHT_NODE, 2),
        (module._SMALL_LIGHT_TEXT_NODE, 2),
    ]
    assert fight.context.actions == ["闪避", "攻击"]


def test_probe_cache_reuses_current_tick_then_real_dispatch_refreshes(monkeypatch):
    fight = ScriptedFight(monkeypatch, "great_ball")
    expected = fight.role._probe_sword_mode()
    snapshot = list(fight.events)

    assert fight.role._probe_sword_mode() == expected
    assert fight.role._is_great_sword()
    assert fight.role._read_great_light() == 300
    assert fight.events == snapshot

    fight.role.do_perform()

    assert fight.attacks == 3  # One earlier probe, then two unchanged supplements.
    assert fight.warnings == 3
    assert fight.context.actions == ["攻击", "攻击", "攻击", "长按闪避"]
    assert fight.captures > 2


def test_warning_miss_keeps_supplement_count_order_and_dragon_action(monkeypatch):
    fight = ScriptedFight(monkeypatch, "great_ball")

    fight.role.do_perform()

    assert fight.warnings == 2
    assert fight.context.actions == ["攻击", "攻击", "长按闪避"]
    assert fight.role.phase == "great_dragon_red"
    assert fight.context.pipeline_overrides[-1] == {
        "长按闪避": {"action": {"param": {"duration": 1500}}},
    }


@pytest.mark.parametrize("phase", ("small_dodge", "great_build_dodge"))
def test_stop_in_ordinary_combo_does_not_fall_through_phase(monkeypatch, phase):
    fight = ScriptedFight(monkeypatch, phase, great_light=0,
                          stop_at=("attack", 3))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.role.phase == phase


@pytest.mark.parametrize("phase", ORDINARY_PHASES)
def test_stop_during_probe_observation_capture_never_recognizes_or_publishes(monkeypatch, phase):
    fight = ScriptedFight(monkeypatch, phase, stop_at=("capture", 3))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == ["攻击", "攻击"]
    assert fight.role._sword_probe_cache is None
    assert fight.role.phase == fight.stop_phase == phase


@pytest.mark.parametrize("phase", ORDINARY_PHASES)
@pytest.mark.parametrize("node", (
    module._GREAT_LIGHT_NODE, module._SMALL_LIGHT_NODE,
    module._GREAT_LIGHT_TEXT_NODE, module._SMALL_LIGHT_TEXT_NODE,
))
def test_stop_during_probe_observation_result_never_uses_partial_mode(monkeypatch, phase, node):
    fight = ScriptedFight(monkeypatch, phase,
                          mode_at={3: "both"},
                          stop_at=("recognition", (node, 1)))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == ["攻击", "攻击"]
    assert fight.role._sword_probe_cache is None
    assert fight.role.phase == fight.stop_phase == phase


@pytest.mark.parametrize("phase", ORDINARY_PHASES[1:])
@pytest.mark.parametrize("hit", (False, True))
def test_stop_during_dragon_ready_observation_prevents_both_ready_branches(monkeypatch, phase, hit):
    node = module._DRAGON_RED_LIGHT_NODE
    fight = ScriptedFight(monkeypatch, phase,
                          hit_at={node: (1, 2) if hit else ()},
                          stop_at=("recognition", (node, 1)))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == ["攻击", "攻击"]
    assert fight.role.phase == fight.stop_phase == phase


@pytest.mark.parametrize("hit", (False, True))
def test_stop_during_dragon_ready_phase_observation_prevents_input_and_fallback(monkeypatch, hit):
    node = module._DRAGON_RED_LIGHT_NODE
    fight = ScriptedFight(monkeypatch, "great_dragon_red",
                          hit_at={node: (1,) if hit else ()},
                          stop_at=("recognition", (node, 1)))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == []
    assert fight.role.phase == fight.stop_phase == "great_dragon_red"


@pytest.mark.parametrize("phase", ORDINARY_PHASES)
@pytest.mark.parametrize("hit", (False, True))
def test_stop_during_small_ult_energy_observation_never_starts_skill(monkeypatch, phase, hit):
    node = "技能_能量条"
    fight = ScriptedFight(monkeypatch, phase,
                          mode_at={3: "small"},
                          hit_at={node: (1,) if hit else ()},
                          stop_at=("recognition", (node, 1)))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == ["攻击", "攻击"]
    assert fight.role.phase == fight.stop_phase == phase


def test_stop_during_combo_energy_observation_capture_does_not_start_recognition(monkeypatch):
    fight = ScriptedFight(monkeypatch, "small_dodge",
                          mode_at={3: "small", 4: "small"},
                          stop_at=("capture", 4))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.role.phase == fight.stop_phase == "small_dodge"


@pytest.mark.parametrize("phase", WAIT_PHASES)
@pytest.mark.parametrize("boundary", (
    "capture", module._GREAT_LIGHT_NODE, module._SMALL_LIGHT_NODE,
    module._GREAT_LIGHT_TEXT_NODE, module._SMALL_LIGHT_TEXT_NODE,
))
def test_wait_probe_cancellation_is_not_treated_as_a_mode_miss(monkeypatch, phase, boundary):
    stop = (("capture", 1) if boundary == "capture"
            else ("recognition", (boundary, 1)))
    fight = ScriptedFight(monkeypatch, phase, mode_at={1: "both"}, stop_at=stop)
    fight.role._ult_wait_deadline = 1000.0

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.role._sword_probe_cache is None
    assert fight.role.phase == fight.stop_phase == phase


@pytest.mark.parametrize("phase", ORDINARY_PHASES[1:])
@pytest.mark.parametrize("hit", (False, True))
def test_second_dragon_ready_result_respects_stop_after_phase_entry(monkeypatch, phase, hit):
    node = module._DRAGON_RED_LIGHT_NODE
    fight = ScriptedFight(monkeypatch, phase,
                          hit_at={node: (1, 2) if hit else (1,)},
                          stop_at=("recognition", (node, 2)))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == ["攻击", "攻击"]
    assert fight.role.phase == fight.stop_phase == "great_dragon_red"


@pytest.mark.parametrize("phase", ("small_ult", "great_ult"))
@pytest.mark.parametrize("boundary", ("capture", "hit", "miss"))
def test_ult_energy_cancellation_does_not_start_skill_or_completion_inputs(monkeypatch, phase, boundary):
    node = "技能_能量条"
    stop = (("capture", 1) if boundary == "capture"
            else ("recognition", (node, 1)))
    fight = ScriptedFight(monkeypatch, phase,
                          hit_at={node: (1,) if boundary == "hit" else ()},
                          stop_at=stop)

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.context.actions == []
    assert fight.role.phase == fight.stop_phase == phase


@pytest.mark.parametrize("phase", WAIT_PHASES)
@pytest.mark.parametrize("hit", (False, True))
def test_wait_energy_cancellation_does_not_resume_wait_inputs(monkeypatch, phase, hit):
    node = "技能_能量条"
    fight = ScriptedFight(monkeypatch, phase, mode_at={1: "none"},
                          hit_at={node: (1,) if hit else ()},
                          stop_at=("recognition", (node, 1)))
    fight.role._ult_wait_deadline = 1000.0

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    assert fight.role.phase == fight.stop_phase == phase


@pytest.mark.parametrize("energy_observation", (1, 2))
@pytest.mark.parametrize("hit", (False, True))
def test_dragon_energy_cancellation_does_not_start_followup_inputs_or_phase(monkeypatch, energy_observation, hit):
    node = "技能_能量条"
    fight = ScriptedFight(monkeypatch, "great_dragon_red",
                          hit_at={module._DRAGON_RED_LIGHT_NODE: (1,),
                                  node: (energy_observation,) if hit else ()},
                          stop_at=("recognition", (node, energy_observation)))

    fight.role.do_perform()

    fight.assert_no_work_after_stop()
    # Before the second observation, a failed switch legitimately sets idle.
    # Cancellation must preserve the phase at Stop, not undo earlier changes.
    assert fight.role.phase == fight.stop_phase


@pytest.mark.parametrize("phase", ("small_ult", "great_ult"))
def test_ready_ult_energy_still_executes_original_skill_without_extra_warning(monkeypatch, phase):
    fight = ScriptedFight(monkeypatch, phase, hit_at={"技能_能量条": (1,)})

    fight.role.do_perform()

    assert fight.context.actions == ["技能"]
    assert fight.warnings == 0
    assert fight.role.phase == phase
