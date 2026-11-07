"""A confirmed generic teammate requires its name and same-frame bench evidence."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from types import SimpleNamespace

import cv2
import pytest
from maa.custom_action import CustomAction

from action.combat.core import session
from action.combat.core.provider import CombatCheck, HpObservation, Roguelike3IdentityError
from action.combat.core.role_factory import create_role
from action.combat.core.session import CombatTask
from action.combat.core.team import TEAM_ROSTER_NODE, TeamSnapshot, publish_team_roster
from test_r03_recruitment_native import native_hub
from test_support.fakes import FakeContext


ROSTER = {"R": "Oblivion", "B": "GeneralFight", "Y": ""}
NAMES = {"R": "露娜·终焉", "B": "薇拉·瑰丽", "Y": ""}


def make_pair(context, roster=ROSTER):
    item = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
    item.team = TeamSnapshot(**roster, current="R")
    item.roles = {color: create_role(item, color, item.team.cls_at(color))
                  for color in item.team.filled_colors()}
    return item


def test_real_generic_return_uses_confirmed_name_and_same_frame_qte_without_input(tmp_path):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    context.override_pipeline({TEAM_ROSTER_NODE: {"attach": {
                        **ROSTER, "role_names": NAMES,
                    }}})
                    item = make_pair(context)
                    bindings = item.roles.copy()
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / "r03_guili_return_hud.png"))
                    item.frame = frame
                    hp = item.combat_check.read_hp_from_frame(context, frame)
                    assert hp.alive and (hp.current, hp.maximum) == (1078, 1078)
                    assert session.detect_visible_team_colors(context, frame) == ["R"]
                    assert session.detect_current_role(context, frame, on_tick=None) == ("瑰丽", "GeneralFight")
                    assert item.refresh_field_role(item.roles["R"]) is True
                    assert item.team.current == "B" and item.field_identity == "confirmed"
                    assert {color: item.team.cls_at(color) for color in ROSTER} == ROSTER
                    assert item.roles == bindings
                    assert item.refresh_field_role(item.roles["B"]) is False
                    observations.append(True)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("GenericFieldReturnProbe", Probe())
        job = tasker.post_task("GenericFieldReturnProbe", {
            "GenericFieldReturnProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "GenericFieldReturnProbe"}},
                "next": [], "on_error": [],
            },
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and observations == [True] and not inputs


@pytest.mark.parametrize("fixture,roster,names,expected_hp", [
    ("r03_guili_bright_bench_hud.png", ROSTER, NAMES, (1978, 1978)),
    ("r03_crimson_guili_bench_conflict_hud.png",
     {"R": "CrimsonWeave", "B": "GeneralFight", "Y": ""},
     {"R": "露西亚·深红囚影", "B": "薇拉·瑰丽", "Y": ""}, (628, 628)),
])
def test_conflicting_badge_without_a_confirmed_bench_face_still_rejects_without_input(
    tmp_path, fixture, roster, names, expected_hp,
):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    publish_team_roster(context, roster, role_names=names)
                    item = make_pair(context, roster)
                    bindings = item.roles.copy()
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / fixture))
                    if roster["R"] == "Oblivion":
                        # Keep the real HP, attack icon and blue badge, removing
                        # only the additional positive bench-face evidence.
                        frame[122:172, 1140:1195] = 0
                    item.frame = frame
                    hp = item.combat_check.read_hp_from_frame(context, frame)
                    assert hp.alive and (hp.current, hp.maximum) == expected_hp
                    assert session.detect_current_role(context, frame, on_tick=None) == ("瑰丽", "GeneralFight")
                    assert session.detect_visible_team_colors(context, frame) == ["B"]
                    with pytest.raises(Roguelike3IdentityError, match="r3_strategy_identity_mismatch"):
                        item.refresh_field_role(item.roles["R"])
                    assert item.team.current == "R" and item.roles == bindings
                    assert item.field_identity == "unknown"
                    observations.append(True)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("BrightBenchConflictProbe", Probe())
        job = tasker.post_task("BrightBenchConflictProbe", {
            "BrightBenchConflictProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "BrightBenchConflictProbe"}},
                "next": [], "on_error": [],
            },
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and observations == [True] and not inputs


def test_real_blue_badge_with_an_oblivion_bench_face_confirms_existing_guili(tmp_path):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    publish_team_roster(context, ROSTER, role_names=NAMES)
                    item = make_pair(context)
                    bindings = item.roles.copy()
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / "r03_guili_bright_bench_hud.png"))
                    item.frame = frame
                    hp = item.combat_check.read_hp_from_frame(context, frame)
                    assert hp.alive and (hp.current, hp.maximum) == (1978, 1978)
                    assert session.detect_current_role(context, frame, on_tick=None) == ("瑰丽", "GeneralFight")
                    assert session.detect_visible_team_colors(context, frame) == ["B"]
                    assert item.refresh_field_role(item.roles["R"]) is True
                    assert context.run_recognition("矩阵循生_场外终焉头像", frame).hit
                    assert item.team.current == "B" and item.field_identity == "confirmed"
                    assert item.roles == bindings
                    assert {c: item.team.cls_at(c) for c in ROSTER} == ROSTER
                    assert item.refresh_field_role(item.roles["B"]) is False
                    observations.append(True)
                    return True
                except BaseException as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("BenchFaceProbe", Probe())
        job = tasker.post_task("BenchFaceProbe", {
            "BenchFaceProbe": {"recognition": {"type": "DirectHit"},
                               "action": {"type": "Custom", "param": {"custom_action": "BenchFaceProbe"}},
                               "next": [], "on_error": []},
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and observations == [True] and not inputs


@pytest.mark.parametrize("fixture,expected", [
    ("r03_guili_return_hud.png", True),
    ("r03_guili_bright_bench_hud.png", True),
    ("r03_crimson_guili_bench_conflict_hud.png", False),
    ("r03_multi_oblivion_hud.png", False),
    (None, False),
])
def test_oblivion_bench_template_requires_the_actual_portrait(tmp_path, fixture, expected):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations = []

        class Probe(CustomAction):
            def run(self, context, argv):
                if fixture:
                    frame[:] = cv2.imread(str(EVIDENCE_DIR / fixture))
                result = context.run_recognition("矩阵循生_场外终焉头像", frame)
                observations.append(bool(result and result.hit))
                return True

        assert resource.register_custom_action("BenchTemplateProbe", Probe())
        job = tasker.post_task("BenchTemplateProbe", {"BenchTemplateProbe": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "BenchTemplateProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired and observations == [expected] and not inputs


@pytest.mark.parametrize("invalid", [
    "missing_names", "wrong_support", "wrong_main", "stale_roster", "third_member",
    "missing_face", "missing_result", "no_badge", "extra_badge", "wrong_detected_name",
    "dead", "fallback",
])
def test_bench_face_cannot_authorize_an_unconfirmed_or_inconsistent_roster(monkeypatch, invalid):
    context = FakeContext()
    publish_team_roster(context, ROSTER, role_names=NAMES.copy())
    item = make_pair(context)
    item.frame = object()
    names = context.node_data[TEAM_ROSTER_NODE]["attach"]
    colors = ["B"]
    if invalid == "missing_names":
        names.pop("role_names")
    elif invalid == "wrong_support":
        names["role_names"]["B"] = "丽芙·流光"
    elif invalid == "wrong_main":
        names["R"] = item.team.R = "CrimsonWeave"
        names["role_names"]["R"] = "露西亚·深红囚影"
        item.roles["R"] = create_role(item, "R", "CrimsonWeave")
    elif invalid == "stale_roster":
        names["Y"] = "Oblivion"
    elif invalid == "third_member":
        names["Y"] = item.team.Y = "Oblivion"
        names["role_names"]["Y"] = "露娜·终焉"
    elif invalid == "no_badge":
        colors = []
    elif invalid == "extra_badge":
        colors = ["B", "Y"]
    elif invalid == "fallback":
        item.roles["B"].fallback_reason = "requested_strategy_unavailable"
    monkeypatch.setattr(item.combat_check, "read_hp_from_frame", lambda *_:
                        HpObservation(0 if invalid == "dead" else 1978, 1978, "alive"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw:
                        ("绯耀" if invalid == "wrong_detected_name" else "瑰丽", "GeneralFight"))
    monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: colors)
    monkeypatch.setattr(context, "run_recognition", lambda *_, **kw:
                        None if invalid == "missing_result" else SimpleNamespace(hit=invalid != "missing_face"))
    before = (item.team.R, item.team.B, item.team.Y, item.team.current)
    bindings = item.roles.copy()
    with pytest.raises(Roguelike3IdentityError, match="r3_strategy_identity_mismatch"):
        item.refresh_field_role(item.roles["R"])
    assert (item.team.R, item.team.B, item.team.Y, item.team.current) == before
    assert item.roles == bindings
    assert not context.actions and not context.tasker.controller.clicks


@pytest.mark.parametrize("invalid", [
    "missing_names", "wrong_name", "class_mismatch", "stale_roster", "duplicate",
    "missing_old_qte", "field_qte", "extra_qte", "dead", "fallback",
])
def test_generic_return_still_rejects_unconfirmed_or_conflicting_evidence(monkeypatch, invalid):
    context = FakeContext(node_data={TEAM_ROSTER_NODE: {"attach": {
        **ROSTER, "role_names": NAMES.copy(),
    }}})
    item = make_pair(context)
    item.frame = object()
    colors = ["R"]
    attach = context.node_data[TEAM_ROSTER_NODE]["attach"]
    if invalid == "missing_names":
        attach.pop("role_names")
    elif invalid == "wrong_name":
        attach["role_names"]["B"] = "丽芙·流光"
    elif invalid == "class_mismatch":
        attach["role_names"]["B"] = "露娜·终焉"
    elif invalid == "stale_roster":
        attach["Y"] = "Oblivion"
    elif invalid == "duplicate":
        item.team.Y = "GeneralFight"
        attach["Y"] = "GeneralFight"
        attach["role_names"]["Y"] = "丽芙·流光"
    elif invalid == "missing_old_qte":
        colors = []
    elif invalid == "field_qte":
        colors = ["R", "B"]
    elif invalid == "extra_qte":
        colors = ["R", "Y"]
    elif invalid == "fallback":
        item.roles["B"].fallback_reason = "requested_strategy_unavailable"
    monkeypatch.setattr(item.combat_check, "read_hp_from_frame", lambda *_:
                        HpObservation(0 if invalid == "dead" else 1078, 1078, "alive"))
    monkeypatch.setattr(session, "detect_current_role", lambda *_, **kw: ("瑰丽", "GeneralFight"))
    monkeypatch.setattr(session, "detect_visible_team_colors", lambda *_: colors)
    before = (item.team.R, item.team.B, item.team.Y, item.team.current)
    with pytest.raises(Roguelike3IdentityError, match="r3_strategy_identity_mismatch"):
        item.refresh_field_role(item.roles["R"])
    assert (item.team.R, item.team.B, item.team.Y, item.team.current) == before
    assert not context.actions and not context.tasker.controller.clicks


def test_publishing_a_new_roster_clears_previous_confirmed_names():
    context = FakeContext()
    publish_team_roster(context, ROSTER, role_names=NAMES)
    assert context.node_data[TEAM_ROSTER_NODE]["attach"]["role_names"] == NAMES
    publish_team_roster(context, ROSTER)
    assert context.node_data[TEAM_ROSTER_NODE]["attach"]["role_names"] == {}
