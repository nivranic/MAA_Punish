"""Shared warning reactions and protected role phases."""
from types import SimpleNamespace
from unittest.mock import Mock
import importlib

import pytest

from action.combat.actions.CombatActions import CombatActions
from action.combat.core import role_factory
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.switch import blind_attack_click
from action.combat.core.team import TeamSnapshot
from action.combat.reactive_dodge import DodgeAfterWarning, auto_dodge
from test_support.fakes import FakeContext, make_hit, make_miss


def inverse_combat(phase, skill_sub=""):
    context = FakeContext(recognition_map={"自动闪避": make_hit()})
    combat = CombatTask(context, CombatCheck())
    combat.team = TeamSnapshot(R="InverseCrown", B="", Y="", current="R")
    role = role_factory.create_role(combat, "R", "InverseCrown")
    combat.roles = {"R": role}
    role.phase = phase
    role._skill_sub = skill_sub
    return context, combat, role


def test_warning_reacts_without_sleep(monkeypatch):
    context = FakeContext(recognition_map={"自动闪避": make_hit()})
    action = CombatActions(context)
    module = importlib.import_module("action.combat.actions.CombatActions")
    monkeypatch.setattr(module.time, "sleep", Mock(side_effect=AssertionError("warning reaction must not wait")))
    assert action._auto_dodge(b"current-frame")
    assert context.actions == ["闪避"]


def test_miss_does_not_send_dodge():
    context = FakeContext(recognition_map={"自动闪避": make_miss()})
    assert not auto_dodge(context, b"frame")
    assert context.actions == []


def test_role_warning_uses_new_frame_after_session_prechecks(monkeypatch):
    context, combat, role = inverse_combat("farm")
    previous_frame, current_frame = object(), object()
    combat.frame = previous_frame
    acquisitions = []

    def acquire(*, fresh=False):
        assert fresh
        acquisitions.append(True)
        combat.frame = current_frame
        return current_frame

    def detect(name, image):
        assert name == "自动闪避"
        # A warning that appeared during the HP/identity checks is absent from
        # the old snapshot; only the newly acquired frame may trigger it.
        return make_hit() if image is current_frame else make_miss()

    monkeypatch.setattr(role.action, "_get_image", acquire)
    context.run_recognition = detect
    assert role.action._auto_dodge(previous_frame)
    assert acquisitions == [True]
    assert context.actions == ["闪避"]


def test_role_attack_refreshes_warning_once(monkeypatch):
    context, combat, role = inverse_combat("farm")
    combat.frame = object()
    current_frame = object()
    acquisitions = []

    def acquire(*, fresh=False):
        assert fresh
        acquisitions.append(True)
        return current_frame

    def detect(name, image):
        assert name == "自动闪避" and image is current_frame
        return make_hit()

    monkeypatch.setattr(role.action, "_get_image", acquire)
    context.run_recognition = detect
    role.action.attack()
    assert acquisitions == [True]
    assert context.actions == ["闪避", "攻击"]


@pytest.mark.parametrize("phase,skill_sub", [("farm", ""), ("skill", "charge"),
                                             ("special_attack", "")])
def test_no_warning_reaction_does_not_block_normal_attack(phase, skill_sub):
    context, _, role = inverse_combat(phase, skill_sub)
    context.recognition_map["自动闪避"] = make_miss()

    assert role.action.attack() is True
    assert context.actions == ["攻击"]


def test_shared_attack_returns_false_if_warning_check_observes_stop(monkeypatch):
    context, _, role = inverse_combat("farm")

    def stopped(_image):
        context.tasker.stopping = True
        return False

    monkeypatch.setattr(role.action, "_auto_dodge", stopped)
    assert role.action.attack() is False
    assert context.actions == []


@pytest.mark.parametrize("stopped,phase,skill_sub", [
    (True, "farm", ""), (True, "skill", "charge"),
    (False, "special_press", ""), (False, "special_attack", ""),
    (False, "skill", "cast"), (False, "skill", "cage"), (False, "skill", "finish"),
])
def test_stopped_or_protected_role_does_not_acquire_warning_frame(monkeypatch, stopped, phase, skill_sub):
    context, combat, role = inverse_combat(phase, skill_sub)
    context.tasker.stopping = stopped
    monkeypatch.setattr(role.action, "_get_image", Mock(side_effect=AssertionError("must not capture")))
    assert not role.action._auto_dodge(object())
    assert context.actions == []


@pytest.mark.parametrize("phase,skill_sub", [("farm", ""), ("skill", "charge")])
def test_stop_during_fresh_capture_prevents_detection_and_input(monkeypatch, phase, skill_sub):
    context, combat, role = inverse_combat(phase, skill_sub)
    def acquire(*, fresh=False):
        assert fresh
        context.tasker.stopping = True
        return object()
    capture = Mock(side_effect=acquire)
    monkeypatch.setattr(role.action, "_get_image", capture)
    context.run_recognition = Mock(side_effect=AssertionError("must not detect after stop"))
    assert not role.action._auto_dodge(object())
    capture.assert_called_once_with(fresh=True)
    assert context.actions == []


@pytest.mark.parametrize("result", [False, None, SimpleNamespace(success=False)])
def test_failed_native_action_is_not_reported_as_success(result):
    context = FakeContext(recognition_map={"自动闪避": make_hit()})
    context.run_action = Mock(return_value=result)
    logger = Mock()
    assert not auto_dodge(context, b"frame", logger=logger)
    logger.info.assert_not_called()
    logger.warning.assert_called_once()


def test_stop_during_detection_prevents_late_input():
    context = FakeContext()
    def detect(*args):
        context.tasker.stopping = True
        return make_hit()
    context.run_recognition = detect
    assert not auto_dodge(context, b"frame")
    assert context.actions == []


@pytest.mark.parametrize("phase,skill_sub,protected_phase,protected_sub", [
    ("farm", "", "special_attack", ""),
    ("skill", "charge", "skill", "cast"),
])
def test_protection_begun_during_detection_prevents_late_input(phase, skill_sub, protected_phase, protected_sub):
    context, combat, role = inverse_combat(phase, skill_sub)
    def detect(*args):
        role.phase = protected_phase
        role._skill_sub = protected_sub
        return make_hit()
    context.run_recognition = Mock(side_effect=detect)
    assert not auto_dodge(context, b"frame", combat=combat)
    context.run_recognition.assert_called_once_with("自动闪避", b"frame")
    assert context.actions == []


@pytest.mark.parametrize("phase,skill_sub", [
    ("special_press", ""), ("special_attack", ""),
    ("skill", "cast"), ("skill", "cage"), ("skill", "finish"),
])
def test_inverse_protected_phase_blocks_direct_and_observation_reactions(phase, skill_sub):
    context, combat, role = inverse_combat(phase, skill_sub)
    context.run_recognition = Mock(side_effect=AssertionError("protected phase must not probe warning"))
    assert combat.is_observation_protected()
    assert not role.action._auto_dodge(b"frame")
    blind_attack_click(context, combat=combat)
    assert context.actions == []


@pytest.mark.parametrize("phase,skill_sub", [
    ("idle", ""), ("farm", ""), ("ball_dump", ""), ("switch", ""),
    ("skill", "charge"),
])
def test_inverse_ordinary_phase_still_responds_to_warning(phase, skill_sub):
    context, combat, role = inverse_combat(phase, skill_sub)
    assert not combat.is_observation_protected()
    assert role.action._auto_dodge(b"frame")
    assert context.actions == ["闪避"]


def test_pipeline_reaction_uses_observed_warning_then_dodges_before_attacking():
    context = FakeContext()
    context.run_recognition = Mock(side_effect=AssertionError("must not repeat warning inference"))
    assert DodgeAfterWarning().run(context, SimpleNamespace(reco_detail=make_hit()))
    assert context.actions == ["闪避", "攻击"]


def test_failed_pipeline_dodge_does_not_continue_attack():
    context = FakeContext()
    context.run_action = Mock(return_value=SimpleNamespace(success=False))
    assert not DodgeAfterWarning().run(context, SimpleNamespace(reco_detail=make_hit()))
    context.run_action.assert_called_once_with("闪避")


def test_stop_after_pipeline_dodge_prevents_following_attack():
    context = FakeContext()
    def act(name):
        context.actions.append(name)
        context.tasker.stopping = True
        return True
    context.run_action = act
    assert not DodgeAfterWarning().run(context, SimpleNamespace(reco_detail=make_hit()))
    assert context.actions == ["闪避"]
