"""Combat screenshots must never turn a failed job's cache into a new frame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from action.combat.capture import CombatCaptureError, capture_frame
from action.combat.config.LoadSetting import ROLE_ACTIONS
from action.combat.core import switch
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.roles.aeternion import Aeternion
from action.combat.roles.arete import Arete
from action.combat.roles.dirge import Dirge
from action.combat.roles.lacrimosa import Lacrimosa
from test_support.fakes import FakeContext, FakeController, make_hit


def _image() -> np.ndarray:
    return np.zeros((4, 6, 3), dtype=np.uint8)


@dataclass
class _ScreenshotJob:
    image: Any = None
    succeeded: bool = True
    done: bool = True
    error_at: str = ""
    error: Exception | None = None
    waited: bool = False
    get_calls: int = 0

    def wait(self):
        self.waited = True
        if self.error_at == "wait":
            raise self.error
        return self

    def get(self):
        assert self.waited
        self.get_calls += 1
        if self.error_at == "get":
            raise self.error
        return self.image


class _ScreenshotController(FakeController):
    def __init__(self, *jobs: _ScreenshotJob):
        super().__init__()
        self.jobs = list(jobs)

    def post_screencap(self):
        job = self.jobs.pop(0)
        if job.error_at == "post":
            raise job.error
        return job


def _combat(*jobs: _ScreenshotJob) -> CombatTask:
    context = FakeContext()
    context.tasker.controller = _ScreenshotController(*jobs)
    return CombatTask(context, CombatCheck())


def test_success_is_published_only_after_waiting_and_validation(monkeypatch):
    image = _image()
    job = _ScreenshotJob(image=image)
    combat = _combat(job)
    combat.frame = _image()
    controller = combat.context.tasker.controller
    original_post = controller.post_screencap

    def post():
        assert combat.frame is None
        return original_post()

    monkeypatch.setattr(controller, "post_screencap", post)
    assert capture_frame(combat.context, combat) is image
    assert combat.frame is image
    assert job.waited and job.get_calls == 1


@pytest.mark.parametrize("done", [True, False], ids=["failed", "unfinished"])
def test_failed_or_unfinished_job_does_not_read_previous_success_cache(done):
    cached = _image()
    failed = _ScreenshotJob(image=cached, succeeded=False, done=done)
    combat = _combat(_ScreenshotJob(image=cached), failed)
    assert combat._capture_frame() is cached

    with pytest.raises(CombatCaptureError):
        combat._capture_frame()

    assert failed.waited and failed.get_calls == 0
    assert combat.frame is None
    assert combat.context.actions == []


@pytest.mark.parametrize(
    "image",
    [
        None,
        b"cached bytes",
        np.zeros((0, 6, 3), dtype=np.uint8),
        np.zeros((4, 0, 3), dtype=np.uint8),
        np.zeros((4, 6), dtype=np.uint8),
        np.zeros((4, 6, 4), dtype=np.uint8),
        np.zeros((4, 6, 3), dtype=np.float32),
    ],
    ids=["none", "bytes", "no-height", "no-width", "grayscale", "bgra", "float"],
)
def test_successful_job_with_invalid_image_cannot_publish_frame(image):
    combat = _combat(_ScreenshotJob(image=image))
    combat.frame = _image()
    with pytest.raises(CombatCaptureError):
        capture_frame(combat.context, combat)
    assert combat.frame is None


@pytest.mark.parametrize("stage", ["post", "wait", "get"])
def test_controller_exceptions_propagate_unchanged_and_invalidate_frame(
    stage, monkeypatch
):
    error = OSError("controller disconnected")
    combat = _combat(_ScreenshotJob(image=_image(), error_at=stage, error=error))
    combat.frame = _image()
    if stage == "post":
        def failed_post():
            raise error

        monkeypatch.setattr(combat.context.tasker.controller, "post_screencap", failed_post)

    with pytest.raises(OSError) as raised:
        capture_frame(combat.context, combat)
    assert raised.value is error
    assert combat.frame is None


@pytest.mark.parametrize(
    "consumer",
    [
        lambda combat: Aeternion(combat, "R", "Aeternion").action._get_image(fresh=True),
        lambda combat: combat.combat_check._get_frame(combat.context, combat),
        lambda combat: combat._capture_frame(),
        lambda combat: switch.blind_attack_click(combat.context, combat=combat),
        lambda combat: switch.detect_visible_team_colors(combat.context),
        lambda combat: switch.click_qte_by_color(combat.context, "R"),
        lambda combat: switch.attempt_switch_to_color(
            combat.context, "R", "Aeternion", combat=combat
        ),
        lambda combat: switch.click_qte_until_done(combat.context, "R"),
    ],
    ids=["actions", "provider", "session", "blind-attack", "visible-qte", "click-qte", "switch", "follow-qte"],
)
def test_combat_capture_consumers_propagate_failure_without_input(consumer):
    job = _ScreenshotJob(image=_image(), succeeded=False)
    combat = _combat(job)
    with pytest.raises(CombatCaptureError):
        consumer(combat)
    assert job.get_calls == 0
    assert combat.frame is None
    assert combat.context.actions == []
    assert combat.context.tasker.controller.clicks == []


def test_actions_may_reuse_valid_frame_but_failed_refresh_discards_it():
    job = _ScreenshotJob(image=_image(), succeeded=False)
    combat = _combat(job)
    role = Aeternion(combat, "R", "Aeternion")
    cached = _image()
    combat.frame = cached
    assert role.action._get_image() is cached
    assert not job.waited
    with pytest.raises(CombatCaptureError):
        role.action._get_image(fresh=True)
    assert combat.frame is None


def test_switch_failure_after_first_click_stops_followup_input(monkeypatch):
    combat = _combat(
        _ScreenshotJob(image=_image()),
        _ScreenshotJob(image=_image(), succeeded=False),
    )
    combat.context.recognition_map["切换红色QTE"] = make_hit()
    monkeypatch.setattr(switch, "is_switch_arrived", lambda *_: False)
    attacks = []
    with pytest.raises(CombatCaptureError):
        switch.attempt_switch_to_color(
            combat.context,
            "R",
            "Aeternion",
            attacker_callback=lambda: attacks.append("attack"),
            combat=combat,
        )
    assert attacks == ["attack"]
    assert len(combat.context.tasker.controller.clicks) == 1
    assert combat.frame is None


class _EntryCheck(CombatCheck):
    def in_combat(self, context, combat):
        return True

    def detect_team(self, context, combat):
        return TeamSnapshot.solo("GeneralFight")


@pytest.mark.parametrize("stage", ["enter", "load-team", "main-loop"])
def test_capture_failure_at_any_session_stage_runs_existing_cleanup(stage, monkeypatch):
    failed = _ScreenshotJob(image=_image(), succeeded=False)
    combat = _combat(*([_ScreenshotJob(image=_image())] if stage == "main-loop" else []), failed)
    combat.combat_check = _EntryCheck()
    combat._role_swap_cd_until = {"R": 10.0}
    combat._hp_zero_since = 1.0
    if stage == "load-team":
        def entered():
            combat._in_combat = True

        monkeypatch.setattr(combat, "_wait_in_combat", entered)
    monkeypatch.setattr(combat, "_correct_role_from_field", lambda *_args, **_kwargs: ("未知", "GeneralFight"))

    with pytest.raises(CombatCaptureError):
        combat.combat_once()

    assert combat.frame is None
    assert combat.team is None and combat.roles == {}
    assert not combat._in_combat and not combat.combat_ui_visible
    assert combat._role_swap_cd_until == {}
    assert combat._hp_zero_since is None
    assert combat.context.actions == []


@pytest.mark.parametrize("role_cls", [Aeternion, Arete, Dirge, Lacrimosa])
def test_normal_role_step_keeps_attack_before_and_after_body(role_cls, monkeypatch):
    role = role_cls(_combat(), "R", role_cls.__name__)
    events = []
    monkeypatch.setattr(role.action, "attack", lambda: events.append("attack"))
    role._run_step(lambda: events.append("body"))
    assert events == ["attack", "body", "attack"]


@pytest.mark.parametrize("role_cls", [Aeternion, Arete, Dirge, Lacrimosa])
@pytest.mark.parametrize("failure", ["capture", "body"])
def test_exceptional_role_step_propagates_without_trailing_attack(role_cls, failure, monkeypatch):
    job = _ScreenshotJob(image=_image(), succeeded=False)
    role = role_cls(_combat(job), "R", role_cls.__name__)
    events = []
    monkeypatch.setattr(role.action, "attack", lambda: events.append("attack"))
    error = ValueError("failed step")

    def body():
        events.append("body")
        if failure == "capture":
            role.action.check_status("测试角色状态")
        raise error

    with pytest.raises(CombatCaptureError if failure == "capture" else ValueError) as raised:
        role._run_step(body)
    if failure == "body":
        assert raised.value is error
    else:
        assert job.get_calls == 0
    assert events == ["attack", "body"]


def test_failed_capture_still_releases_held_dodge():
    combat = _combat(_ScreenshotJob(image=_image(), succeeded=False))
    role = Aeternion(combat, "R", "Aeternion")
    with pytest.raises(CombatCaptureError):
        role.action.long_press_dodge_until("充能完成")
    assert combat.context.actions == ["按下闪避", "松开闪避"]


def _arrange_failure_job(failure: str) -> _ScreenshotJob:
    return _ScreenshotJob(
        image=None if failure == "invalid-image" else _image(),
        succeeded=failure != "failed-job",
        error_at=failure if failure in {"post", "wait", "get"} else "",
        error=OSError("screenshot backend disconnected"),
    )


def _arranging_role(combat: CombatTask) -> Aeternion:
    role = Aeternion(combat, "R", "Aeternion")
    # Exercise the shared Arrange consumer with an existing populated template.
    role.action.template = ROLE_ACTIONS["里·超刻"]["skill_template"]
    return role


@pytest.mark.parametrize("failure", ["failed-job", "invalid-image", "post", "wait", "get"])
def test_arrange_signal_balls_propagates_capture_failures(failure):
    job = _arrange_failure_job(failure)
    combat = _combat(job)
    role = _arranging_role(combat)
    assert role.action.template
    expected = CombatCaptureError if failure in {"failed-job", "invalid-image"} else OSError

    with pytest.raises(expected) as raised:
        role.action.Arrange_Signal_Balls()

    if expected is OSError:
        assert raised.value is job.error
    assert combat.frame is None
    assert combat.context.actions == []


@pytest.mark.parametrize("failure", ["failed-job", "invalid-image", "post", "wait", "get"])
def test_role_step_arrange_failure_never_runs_attack_after_fault(failure):
    job = _arrange_failure_job(failure)
    followup = _ScreenshotJob(image=_image())
    combat = _combat(_ScreenshotJob(image=_image()), job, followup)
    role = _arranging_role(combat)
    expected = CombatCaptureError if failure in {"failed-job", "invalid-image"} else OSError

    with pytest.raises(expected) as raised:
        role._run_step(role.action.Arrange_Signal_Balls)

    if expected is OSError:
        assert raised.value is job.error
    assert combat.context.actions == ["攻击"]
    assert combat.frame is None
    assert len(combat.context.tasker.controller.jobs) == 1
    assert combat.context.tasker.controller.jobs[0] is followup
    assert not followup.waited
