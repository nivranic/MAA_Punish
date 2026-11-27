"""Exercise the real Runner, entry checks, role scan and blind assist without a device."""

from types import SimpleNamespace

import numpy as np
import pytest

from action.combat.core import session
from action.combat.core.session import CombatTask
from action.combat.core.team import TEAM_ROSTER_NODE
from action.combat.entry.runner import CombatRunner
from test_support.fakes import FakeClock, FakeContext, FakeJob, make_hit, make_miss


HUD = "战斗中"
OVERLAY = "失败2_宣叙妄响"
DEFEAT = "失败_宣叙妄响"
WARNING = "自动闪避"
ROLE = "检查角色"
ENTRY = SimpleNamespace(node_name=HUD)


class EntryFight:
    """Frame-tagged Context/Controller double; no stubs to the safety logic."""

    def __init__(self, monkeypatch, frames, *, published=False, stop_on=None, dodge_success=True):
        self.context = FakeContext(node_data={
            HUD: {"attach": {"combat_exit_nodes": [OVERLAY, DEFEAT]}},
            ROLE: {"recognition": {"type": "TemplateMatch", "param": {}}},
        })
        if published:
            self.context.node_data[TEAM_ROSTER_NODE] = {"attach": {"R": "InverseCrown"}}
        self.frames = frames
        self.captures = []
        self.recognitions = []
        self.actions = []
        self.performed = []
        self.stop_on = stop_on
        self.dodge_success = dodge_success
        self.clock = FakeClock()
        monkeypatch.setattr(session, "time", self.clock)
        monkeypatch.setattr(CombatTask, "WAIT_COMBAT_TIME", 0.6)
        monkeypatch.setattr(CombatTask, "COMBAT_UI_LOST_TIMEOUT", 0.4)
        monkeypatch.setattr(CombatTask, "perform", lambda combat: self.record_dispatch(combat))
        monkeypatch.setattr(self.context.tasker.controller, "post_screencap", self.capture)
        monkeypatch.setattr(self.context, "run_recognition", self.recognize)
        monkeypatch.setattr(self.context, "run_action", self.act)

    def capture(self):
        step = len(self.captures) + 1
        self.clock.sleep(0.05)
        assert step < 30, "fixture did not reach a terminal frame or timeout"
        image = np.full((2, 2, 3), step, dtype=np.uint8)
        self.captures.append(image)
        if self.stop_on == (step, "capture"):
            self.context.tasker.stopping = True
        return FakeJob(image=image)

    def recognize(self, entry, image, pipeline_override=None):
        step = int(image[0, 0, 0])
        assert image is self.captures[step - 1]
        self.recognitions.append((step, entry))
        if self.stop_on == (step, entry):
            self.context.tasker.stopping = True
        if entry == ROLE:
            candidates = pipeline_override[ROLE]["recognition"]["param"]["any_of"]
            return SimpleNamespace(algorithm="Or", hit=False, raw_detail=[
                {"algorithm": "TemplateMatch", "box": None} for _ in candidates
            ])
        return make_hit() if entry in self.tags(step) else make_miss()

    def tags(self, step):
        return self.frames[min(step, len(self.frames)) - 1]

    def act(self, entry, pipeline_override=None):
        self.actions.append((len(self.captures), entry))
        return SimpleNamespace(success=entry != "闪避" or self.dodge_success)

    def record_dispatch(self, combat):
        if combat.combat_ui_visible:
            self.performed.append(combat.team.current_cls())

    def run(self):
        runner = CombatRunner()
        assert runner.run(self.context, ENTRY).success is True
        result = runner.last_outcome
        assert result.callback_status == "COMPLETED" and result.game_outcome == "UNKNOWN"
        assert self.context.tasker.controller.clicks == []
        assert self.context.tasker.controller.touches == []
        return result


@pytest.mark.parametrize("first,overlay", [({HUD, OVERLAY}, OVERLAY), ({DEFEAT}, DEFEAT)])
def test_entry_checks_known_overlay_before_hud_on_the_same_frame(monkeypatch, first, overlay):
    fight = EntryFight(monkeypatch, [first, {HUD}, {DEFEAT}])
    outcome = fight.run()
    assert fight.actions == []
    assert outcome.exit_reason == "outer_interface" and not outcome.role_bindings
    assert len(fight.captures) == 1 and (1, overlay) in fight.recognitions
    assert (1, HUD) not in fight.recognitions


@pytest.mark.parametrize("stop_on,first", [
    ("capture", {HUD, OVERLAY}), (OVERLAY, {HUD, OVERLAY}), (HUD, {HUD}),
])
def test_entry_stop_has_priority_over_overlay_and_hud(monkeypatch, stop_on, first):
    fight = EntryFight(monkeypatch, [first, {DEFEAT}], stop_on=(1, stop_on))
    outcome = fight.run()
    assert outcome.exit_reason == "stopped" and not outcome.role_bindings
    assert len(fight.captures) == 1 and fight.actions == []
    if stop_on != HUD:
        assert (1, HUD) not in fight.recognitions


def test_stop_after_entry_invocation_beats_a_false_team_result(monkeypatch):
    fight = EntryFight(monkeypatch, [{HUD}])

    def stop_after_load(self):
        assert self.frame is fight.captures[0]
        self.context.tasker.stopping = True
        return False

    monkeypatch.setattr(CombatTask, "load_team", stop_after_load)
    outcome = fight.run()
    assert outcome.exit_reason == "stopped" and not outcome.role_bindings
    assert len(fight.captures) == 1 and fight.actions == []


@pytest.mark.parametrize("published", [False, True], ids=["provider-fallback", "roster-correction"])
@pytest.mark.parametrize("hazard_at", [2, 3], ids=["first-assist-frame", "after-first-attack"])
def test_fresh_overlay_during_real_blind_assist_is_sticky(monkeypatch, published, hazard_at):
    fight = EntryFight(monkeypatch, [
        *[{HUD}] * (hazard_at - 1), {HUD, OVERLAY, WARNING}, *[{HUD}] * 7, {DEFEAT},
    ], published=published)
    outcome = fight.run()
    assert fight.actions == ([] if hazard_at == 2 else [(2, "攻击")])
    assert (hazard_at, OVERLAY) in fight.recognitions
    assert (hazard_at, HUD) not in fight.recognitions
    assert (hazard_at, WARNING) not in fight.recognitions
    assert outcome.exit_reason == "outer_interface" and outcome.loop_count == 0
    assert len(outcome.role_bindings) == int(published)


@pytest.mark.parametrize("published,hazard_at,attacks", [
    (False, 2, []), (True, 4, [(2, "攻击"), (3, "攻击")]),
], ids=["provider-on-tick", "roster-correction-on-tick"])
def test_assist_handoff_skips_the_stale_role_scan(monkeypatch, published, hazard_at, attacks):
    fight = EntryFight(monkeypatch, [
        *[{HUD}] * (hazard_at - 1), {HUD, OVERLAY}, {DEFEAT},
    ], published=published)
    recognize = fight.recognize

    def fail_stale_role(entry, image, pipeline_override=None):
        if entry == ROLE:
            raise RuntimeError("stale-frame role scan ran after the handoff")
        return recognize(entry, image, pipeline_override)

    monkeypatch.setattr(fight.context, "run_recognition", fail_stale_role)
    outcome = fight.run()  # Successful ordinary callback; not a reported game win.
    assert fight.actions == attacks and len(fight.captures) == hazard_at
    assert outcome.exit_reason == "outer_interface" and outcome.exception_type is None
    assert len(outcome.role_bindings) == int(published)


def test_overlay_on_qte_frame_preempts_qte_and_first_role_binding(monkeypatch):
    fight = EntryFight(monkeypatch, [{HUD}, {HUD}, {HUD}, {DEFEAT}, *[{HUD}] * 5, {DEFEAT}])
    recognize = fight.recognize

    def fail_exit_qte(entry, image, pipeline_override=None):
        if int(image[0, 0, 0]) == 4 and entry in ("切换红色QTE", "切换黄色QTE", "切换蓝色QTE"):
            raise RuntimeError("QTE recognition ran on the newly captured exit")
        return recognize(entry, image, pipeline_override)

    monkeypatch.setattr(fight.context, "run_recognition", fail_exit_qte)
    outcome = fight.run()
    assert fight.actions == [(2, "攻击"), (3, "攻击")]
    assert outcome.exit_reason == "outer_interface" and not outcome.role_bindings
    assert outcome.loop_count == 0 and (4, DEFEAT) in fight.recognitions


@pytest.mark.parametrize("dodge_success", [True, False], ids=["succeeded", "failed"])
def test_even_failed_dodge_requires_a_new_safe_frame_before_attack(monkeypatch, dodge_success):
    fight = EntryFight(monkeypatch, [
        {HUD}, {HUD, WARNING}, {DEFEAT}, *[{HUD}] * 5, {DEFEAT},
    ], dodge_success=dodge_success)
    outcome = fight.run()
    assert fight.actions == [(2, "闪避")]
    assert (3, DEFEAT) in fight.recognitions
    assert outcome.exit_reason == "outer_interface" and not outcome.role_bindings


def test_entry_loss_of_hud_vetoes_assistance_without_faking_terminal(monkeypatch):
    fight = EntryFight(monkeypatch, [{HUD}, {WARNING}])
    outcome = fight.run()
    assert fight.actions == []
    assert outcome.exit_reason == "combat_ui_lost"
    assert outcome.role_bindings and len(fight.captures) > 3
    assert all(step == 1 or entry != WARNING for step, entry in fight.recognitions)


def test_unrelated_page_without_hud_uses_existing_entry_timeout(monkeypatch):
    fight = EntryFight(monkeypatch, [{WARNING, "返回主菜单"}])
    outcome = fight.run()
    assert fight.actions == [] and not outcome.role_bindings
    assert outcome.exit_reason == "enter_timeout" and len(fight.captures) > 1


def test_normal_hud_still_assists_role_scan_and_enters_combat(monkeypatch):
    fight = EntryFight(monkeypatch, [*[{HUD}] * 9, {DEFEAT}])
    outcome = fight.run()
    assert fight.actions == [(step, "攻击") for step in (2, 3, 5, 6, 7, 8)]
    assert [step for step, entry in fight.recognitions if entry == ROLE] == [1, 4]
    assert (4, "切换红色QTE") in fight.recognitions
    assert fight.performed == ["GeneralFight"] and len(outcome.role_bindings) == 1
    assert outcome.exit_reason == "outer_interface"


def test_dodge_followup_can_attack_after_a_new_positive_hud_frame(monkeypatch):
    fight = EntryFight(monkeypatch, [{HUD}, {HUD, WARNING}, *[{HUD}] * 7, {DEFEAT}])
    outcome = fight.run()
    assert fight.actions[:3] == [(2, "闪避"), (3, "攻击"), (4, "攻击")]
    assert (3, HUD) in fight.recognitions
    assert outcome.exit_reason == "outer_interface" and outcome.role_bindings
