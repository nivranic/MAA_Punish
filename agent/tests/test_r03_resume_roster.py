"""Cold R03 resumes publish only positive solo identity; never repair mismatches."""
from types import SimpleNamespace
import json
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest
from maa.custom_action import CustomAction

from action.basics.PPOverride import PPOverride
from action.combat.core.provider import HpObservation
from action.combat.core.team import TEAM_ROSTER_NODE, load_team_roster_from_context
from action.combat.entry import roguelike3_resume as module
from test_support.fakes import FakeContext
from test_r03_recruitment_native import native_hub

POLICY = {"attach": {"roguelike3_identity": {"version": 1, "require_known_field": True}}}
ARGV = SimpleNamespace(node_name="战斗中", custom_action_param="{}")


def setup(monkeypatch, *, hp=None, colors=(), identity=("终焉", "Oblivion")):
    context = FakeContext(node_data={"战斗中": POLICY})
    frame, seen = object(), []
    monkeypatch.setattr(module, "capture_frame", lambda ctx: frame)
    monkeypatch.setattr(module.CombatCheck, "read_hp_from_frame", lambda self, ctx, image: (
        seen.append(image) or hp or HpObservation(6788, 9832, "alive")))
    monkeypatch.setattr(module, "detect_visible_team_colors", lambda ctx, image: (
        seen.append(image) or list(colors)))

    def detect(ctx, image, *, on_tick):
        assert on_tick is None
        seen.append(image)
        return identity

    monkeypatch.setattr(module, "detect_current_role", detect)
    delegated = []
    monkeypatch.setattr(module.CombatRunner, "run", lambda self, ctx, args: (
        delegated.append(load_team_roster_from_context(ctx)) or "original-guard"))
    return context, frame, seen, delegated


def test_positive_solo_restores_before_original_guard_without_input(monkeypatch):
    context, frame, seen, delegated = setup(monkeypatch)
    assert module.R03CombatRunner().run(context, ARGV) == "original-guard"
    assert seen == [frame] * 3
    assert delegated == [{"R": "Oblivion", "B": "", "Y": ""}]
    assert not context.actions and not context.tasker.controller.clicks


@pytest.mark.parametrize("options", [
    {"hp": HpObservation(None, None, "hp_not_recognized")},
    {"hp": HpObservation(0, 9832, "dead")},
    {"colors": ("B",)}, {"colors": ("Y", "R")},
    {"identity": ("未知", "GeneralFight")},
    {"identity": ("终焉", "MissingImplementation")},
])
def test_incomplete_evidence_cannot_publish(monkeypatch, options):
    context, _, _, delegated = setup(monkeypatch, **options)
    assert module.R03CombatRunner().run(context, ARGV) == "original-guard"
    assert delegated == [None]
    assert not context.actions and not context.tasker.controller.clicks


@pytest.mark.parametrize("roster", [
    {"R": "GeneralFight", "B": "", "Y": ""},
    {"R": "Pyroath", "B": "GeneralFight", "Y": ""},
])
def test_existing_roster_is_never_rewritten(monkeypatch, roster):
    context, _, seen, delegated = setup(monkeypatch)
    context.node_data[TEAM_ROSTER_NODE] = {"attach": roster.copy()}
    monkeypatch.setattr(module, "capture_frame", lambda ctx: pytest.fail("existing roster"))
    assert module.R03CombatRunner().run(context, ARGV) == "original-guard"
    assert not seen and delegated == [roster]


def test_non_r03_does_not_observe_or_publish(monkeypatch):
    context, _, seen, delegated = setup(monkeypatch)
    context.node_data["战斗中"] = {}
    assert module.R03CombatRunner().run(context, ARGV) == "original-guard"
    assert not seen and delegated == [None]


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_observation_failure_returns_false_without_combat(monkeypatch, error):
    context, _, _, delegated = setup(monkeypatch)
    def broken(ctx):
        raise error("private capture error")
    monkeypatch.setattr(module, "capture_frame", broken)
    runner = module.R03CombatRunner()
    assert not runner.run(context, ARGV).success
    assert not delegated and load_team_roster_from_context(context) is None
    assert runner.last_outcome.exit_reason == "r3_resume_observation_error"
    assert runner.last_outcome.exception_type == error.__name__
    assert not context.actions and not context.tasker.controller.clicks


def test_stopped_task_never_captures_or_publishes(monkeypatch):
    context, _, _, delegated = setup(monkeypatch)
    context.tasker.stopping = True
    monkeypatch.setattr(module, "capture_frame", lambda ctx: pytest.fail("stopped task"))
    assert module.R03CombatRunner().run(context, ARGV) == "original-guard"
    assert delegated == [None]


@pytest.mark.parametrize("visible", [True, False])
def test_real_hud_restores_through_r03_initializer_then_keeps_local_stop(tmp_path, monkeypatch, visible):
    root = Path(__file__).resolve().parents[2]
    task = json.loads((root / "tasks/矩阵循生.json").read_bytes())
    observed = []

    def original_guard(self, context, argv):
        assert module.roguelike3_identity_required(context.get_node_data(argv.node_name))
        observed.append(load_team_roster_from_context(context))
        # Native error routing must still stop the parent before any input.
        return CustomAction.RunResult(success=False)

    monkeypatch.setattr(module.CombatRunner, "run", original_guard)
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        if visible:
            fixture = cv2.imread(str(EVIDENCE_DIR / "r03_resume_solo_hud.png"))
            assert fixture is not None and fixture.shape == frame.shape
            frame[:] = fixture
        assert resource.register_custom_action("PPOverride", PPOverride())
        assert resource.register_custom_action("R03CombatRunner", module.R03CombatRunner())
        patch = task["task"][0]["pipeline_override"]
        patch.update({
            "矩阵循生": {"next": ["战斗中"], "post_delay": 0},
            # The real frame used the recorded attack-zone offset (-95,-120).
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        })
        patch["战斗中"]["recognition"] = {"type": "DirectHit"}
        job = tasker.post_task("矩阵循生", patch).wait()
        assert job.succeeded and not expired and not inputs
        detail = tasker.get_task_detail(job.job_id)
        assert [tasker.get_node_detail(n).name for n in detail.node_id_list] == [
            "矩阵循生", "战斗中", "矩阵循生_身份拒绝停止",
        ]
        assert observed == ([{"R": "Oblivion", "B": "", "Y": ""}] if visible else [None])
        assert resource.get_node_data("战斗中")["action"]["param"]["custom_action"] == "CombatRunner"


def test_collapsed_run_discards_roster_before_starting_another_run(tmp_path):
    observed = []

    class Probe(CustomAction):
        def run(self, context, argv):
            observed.append(load_team_roster_from_context(context))
            return True

    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        assert resource.register_custom_action("PPOverride", PPOverride())
        assert resource.register_custom_action("R03NextRunRoster", Probe())
        job = tasker.post_task("链路崩溃", {
            TEAM_ROSTER_NODE: {"attach": {"R": "Oblivion", "B": "GeneralFight", "Y": ""}},
            "链路崩溃": {"recognition": {"type": "DirectHit"}, "post_delay": 0},
            "矩阵循生": {
                "action": {"type": "Custom", "param": {"custom_action": "R03NextRunRoster"}},
                "next": [],
            },
        }).wait()
        assert job.succeeded and not expired and not inputs
        assert observed == [None]
