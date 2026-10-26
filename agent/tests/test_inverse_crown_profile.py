"""Opt-in configuration and fail-closed entry, without claiming game acceptance."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from action.combat.config.inverse_crown_profile import (
    InverseCrownProfile,
    InverseCrownProfileError,
    InverseCrownUnavailable,
)
from action.combat.core import role_factory
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.core.team import TeamSnapshot
from action.combat.entry import runner
from action.combat.roles.general_fight import GeneralFight
from test_support.fakes import FakeContext, make_hit


def _profile(**values):
    return {"attach": {"inverse_crown_profile": values}}


def _combat(enabled=True):
    context = FakeContext(recognition_map={"检查闪避": make_hit(), "自动闪避": make_hit()})
    combat = CombatTask(
        context, CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=enabled),
    )
    combat.team = TeamSnapshot(R="InverseCrown", B="GeneralFight", Y="GeneralFight", current="R")
    combat.roles = {
        color: role_factory.create_role(combat, color, combat.team.cls_at(color))
        for color in ("R", "B", "Y")
    }
    combat.frame = context.tasker.controller.image
    return combat


@pytest.mark.parametrize("data", [{}, {"attach": {}}, _profile(), _profile(enabled=False)])
def test_missing_or_disabled_profile_preserves_ordinary_mode(data):
    profile = InverseCrownProfile.from_node_data(data)
    combat = CombatTask(FakeContext(), CombatCheck(), inverse_crown_profile=profile)
    assert not profile.enabled
    assert combat.input_scope is None
    assert not combat.is_switch_disabled()


@pytest.mark.parametrize("values", [
    {"enabled": 1}, {"enabled": "false"}, {"enabled": None},
    {"scene": "boss"}, {"scene": []}, {"source": "account"},
    {"rank": "A"}, {"rank": "SS5"}, {"rank": 3},
    {"signature_weapon_confirmed": "yes"}, {"signature_weapon_confirmed": 1},
    {"allow_switch": True},
])
def test_bad_values_fail_closed_even_when_disabled(values):
    with pytest.raises(InverseCrownProfileError):
        InverseCrownProfile.from_node_data(_profile(**values))


@pytest.mark.parametrize("data", [None, [], {"attach": None}, {"attach": []},
    {"attach": {"inverse_crown_profile": None}}, {"attach": {"inverse_crown_profile": True}}])
def test_malformed_objects_are_not_silently_defaulted(data):
    with pytest.raises(InverseCrownProfileError):
        InverseCrownProfile.from_node_data(data)


@pytest.mark.parametrize("rank", ["S", "S5", "SS", "SS3", "SSS", "SSS3", "SSS6", "SSS+"])
def test_unknown_source_never_inherits_account_capabilities(rank):
    profile = InverseCrownProfile(rank=rank, signature_weapon_confirmed=True)
    assert profile.effective_rank == "unknown"
    assert profile.effective_signature_weapon is False
    assert InverseCrownProfile(source="trial").effective_rank == "unknown"


def test_each_invocation_copies_facts_and_a_new_run_clears_opt_in_state():
    raw = _profile(enabled=True, scene="multi", source="trial", rank="SS3")
    profile = InverseCrownProfile.from_node_data(raw)
    first = CombatTask(FakeContext(), CombatCheck(), inverse_crown_profile=profile)
    second = CombatTask(FakeContext(), CombatCheck(), inverse_crown_profile=profile)
    ordinary = CombatTask(FakeContext(), CombatCheck())
    raw["attach"]["inverse_crown_profile"]["rank"] = "SSS+"
    role = role_factory.create_role(first, "R", "InverseCrown")
    assert role.inverse_crown_profile.rank == "SS3"
    assert role.inverse_crown_profile.scene == "multi"
    assert role.inverse_crown_profile.effective_rank == "SS3"
    assert first.inverse_crown_profile is not second.inverse_crown_profile
    assert first.input_scope.generation != second.input_scope.generation
    assert ordinary.inverse_crown_profile == InverseCrownProfile()
    assert ordinary.input_scope is None
    assert CombatTask.SWITCH_DISABLED is False
    with pytest.raises(FrozenInstanceError):
        profile.enabled = False


def test_runner_reads_the_call_node_and_copies_profile_on_every_call(monkeypatch):
    context = FakeContext(node_data={
        "first": _profile(enabled=True, source="borrowed", rank="S5"),
        "second": _profile(enabled=False),
        "unconfigured": {},
    })
    seen = []
    layouts = []
    monkeypatch.setattr(runner, "require_inverse_crown_layout", lambda ctx: layouts.append(ctx))
    monkeypatch.setattr(CombatTask, "combat_once", lambda self: seen.append(self))
    for node_name in ("first", "second", "unconfigured"):
        assert runner.CombatRunner().run(context, SimpleNamespace(node_name=node_name)).success
    assert [item.inverse_crown_profile.enabled for item in seen] == [True, False, False]
    assert layouts == [context]
    assert seen[0].inverse_crown_profile.source == "borrowed"
    assert seen[1].inverse_crown_profile.rank == "unknown"
    assert seen[2].input_scope is None


@pytest.mark.parametrize("node_data", [None, [], False, 42, "{}"])
def test_runner_unreadable_or_bad_node_never_constructs_ordinary_combat(node_data, monkeypatch):
    context = FakeContext(node_data={"combat": node_data})
    created = []
    monkeypatch.setattr(runner, "CombatTask", lambda *_args, **_kwargs: created.append(True))
    monkeypatch.setattr(runner, "require_inverse_crown_layout", lambda _ctx: pytest.fail("invalid read cannot reach layout"))
    monkeypatch.setattr(context.tasker.controller, "post_screencap", lambda: pytest.fail("invalid read cannot capture"))
    action = runner.CombatRunner()
    result = action.run(context, SimpleNamespace(node_name="combat"))
    assert result.success is False
    assert created == []
    assert action.last_outcome.exit_reason == "profile_invalid"
    assert action.last_outcome.callback_status == "REJECTED"
    assert action.last_outcome.exception_type == "InverseCrownProfileError"
    assert context.actions == [] and context.tasker.controller.clicks == []


@pytest.mark.parametrize("node_data", [{}, {"attach": {}},
    {"attach": {"other_configuration": True}}, _profile(enabled=False)])
def test_runner_valid_unconfigured_or_no_node_preserves_ordinary_combat(node_data, monkeypatch):
    context = FakeContext(node_data={"ordinary": node_data})
    seen = []

    class OrdinarySpy:
        outcome = None

        def __init__(self, _context, _check, *, inverse_crown_profile):
            seen.append(inverse_crown_profile)

        def combat_once(self):
            seen.append("combat_once")

    monkeypatch.setattr(runner, "CombatTask", OrdinarySpy)
    monkeypatch.setattr(runner, "require_inverse_crown_layout", lambda _ctx: pytest.fail("ordinary config cannot read layout"))
    result = runner.CombatRunner().run(context, SimpleNamespace(node_name="ordinary"))
    assert result.success is True
    assert seen == [InverseCrownProfile(), "combat_once"]
    assert context.actions == [] and context.tasker.controller.clicks == []


@pytest.mark.parametrize("node_data", [_profile(enabled=True), _profile(enabled="true")])
def test_runner_returns_failure_without_any_input_when_unready_or_invalid(node_data, monkeypatch):
    context = FakeContext(node_data={"combat": node_data})
    monkeypatch.setattr(context.tasker.controller, "post_screencap", lambda: pytest.fail("must not start"))
    result = runner.CombatRunner().run(context, SimpleNamespace(node_name="combat"))
    assert result.success is False
    assert context.actions == []
    assert context.tasker.controller.clicks == []


def test_direct_session_and_role_entry_cannot_execute_legacy_strategy(monkeypatch):
    combat = _combat()
    role = combat.roles["R"]
    monkeypatch.setattr(role, "do_perform", lambda: pytest.fail("legacy strategy entered"))
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        role.perform()
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        combat.combat_once()
    assert combat.out_of_combat_reason == "strategy_not_ready"
    assert combat.input_scope.closed
    assert combat.context.actions == []


def test_all_session_and_role_switch_entries_reject_opt_in_but_ordinary_can_switch():
    protected = _combat()
    protected.SWITCH_STUB = True
    role = protected.roles["R"]
    assert not protected.switch_to_color("B", attacker=role)
    assert not protected.request_role_switch(role)
    assert protected.choose_switch_color(role) is None
    assert not protected.can_switch()
    assert not role.switch_next()
    assert not role.switch_red()
    assert not role.switch_blue()
    assert not role.switch_yellow()
    assert protected.team.current == "R"
    assert protected.context.actions == []
    ordinary = _combat(False)
    ordinary.SWITCH_STUB = True
    assert ordinary.roles["R"].switch_blue()
    assert ordinary.team.current == "B"


def test_instance_switch_override_is_respected_by_direct_switch():
    combat = _combat(False)
    combat.SWITCH_STUB = True
    combat.is_switch_disabled = lambda: True
    assert not combat.switch_to_color("B")
    assert combat.team.current == "R"


def test_missing_strategy_has_actual_fallback_identity_and_strict_mode_rejects(monkeypatch):
    monkeypatch.delitem(role_factory.ROLE_CLASS_MAP, "InverseCrown")
    ordinary = CombatTask(FakeContext(), CombatCheck())
    role = role_factory.create_role(ordinary, "R", "InverseCrown")
    assert type(role) is GeneralFight
    assert role.requested_cls_name == "InverseCrown"
    assert role.implementation_name.endswith(".GeneralFight")
    assert role.fallback_reason == "requested_strategy_unavailable"
    protected = CombatTask(FakeContext(), CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=True))
    with pytest.raises(InverseCrownUnavailable, match="strategy_identity_mismatch") as exc:
        role_factory.create_role(protected, "R", "InverseCrown")
    assert exc.value.actual.endswith(".GeneralFight")


def test_forged_cls_name_does_not_satisfy_actual_strategy_check(monkeypatch):
    monkeypatch.setitem(role_factory.ROLE_CLASS_MAP, "InverseCrown", GeneralFight)
    protected = CombatTask(FakeContext(), CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=True))
    with pytest.raises(InverseCrownUnavailable, match="strategy_identity_mismatch"):
        role_factory.create_role(protected, "R", "InverseCrown")


def test_role_import_failure_retains_ordinary_fallback_reason(monkeypatch):
    real_import = role_factory.importlib.import_module
    def failing_import(name):
        if name == "action.combat.roles.inverse_crown":
            raise ImportError("missing implementation dependency")
        return real_import(name)
    monkeypatch.setattr(role_factory, "ROLE_IMPORT_ERRORS", {})
    monkeypatch.setattr(role_factory.importlib, "import_module", failing_import)
    monkeypatch.setattr(role_factory, "ROLE_CLASS_MAP", role_factory._discover_role_classes())
    combat = CombatTask(FakeContext(), CombatCheck())
    role = role_factory.create_role(combat, "R", "InverseCrown")
    assert type(role) is GeneralFight
    assert role.fallback_reason == "strategy_import_failed:ImportError"
    protected = CombatTask(FakeContext(), CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=True))
    with pytest.raises(InverseCrownUnavailable, match="strategy_import_failed"):
        role_factory.create_role(protected, "R", "InverseCrown")


def test_protected_identity_refresh_and_reconciliation_never_attack_or_dodge(monkeypatch):
    combat = _combat()
    monkeypatch.setattr("action.combat.core.session.is_cls_on_field", lambda *_args: True)
    assert not combat.refresh_field_role(combat.roles["R"])
    def detect(*args, on_tick=None, **kwargs):
        if on_tick:
            on_tick()
        return "逆冕", "InverseCrown"
    monkeypatch.setattr("action.combat.core.session.detect_current_role", detect)
    assert combat._correct_role_from_field("R", "InverseCrown", combat.frame) == ("逆冕", "InverseCrown")
    combat._blind_attack_tick()
    assert combat.field_identity == "confirmed"
    assert combat.context.actions == []
    assert combat.context.tasker.controller.clicks == []


@pytest.mark.parametrize("detected, reason", [
    (("未知", "GeneralFight"), "field_identity_unknown"),
    (("灼惘", "Geiravor"), "field_identity_mismatch"),
])
def test_strict_unknown_or_mismatched_field_identity_stops_without_rebinding(monkeypatch, detected, reason):
    combat = _combat()
    monkeypatch.setattr("action.combat.core.session.is_cls_on_field", lambda *_args: False)
    monkeypatch.setattr("action.combat.core.session.detect_current_role", lambda *a, **k: detected)
    with pytest.raises(InverseCrownUnavailable, match=reason):
        combat.refresh_field_role(combat.roles["R"])
    with pytest.raises(InverseCrownUnavailable, match=reason):
        combat._correct_role_from_field("R", "InverseCrown", combat.frame)
    assert combat.team.R == "InverseCrown"
    assert combat.team.current == "R"
    assert combat.context.actions == []


@pytest.mark.parametrize("enabled", [False, True])
def test_provider_detection_keeps_normal_keepalive_but_protected_detection_is_pure(monkeypatch, enabled):
    combat = _combat(enabled)
    def detect(*args, on_tick=None, **kwargs):
        if on_tick:
            on_tick()
        return "逆冕", "InverseCrown"
    monkeypatch.setattr("action.combat.core.provider.detect_current_role", detect)
    CombatCheck().detect_team(combat.context, combat)
    assert combat.context.actions == ([] if enabled else ["闪避", "攻击", "攻击"])


def test_wait_callback_is_pure_when_protected(monkeypatch):
    combat = _combat()
    combat.sleep_check_interval = 0.2
    seen = []
    def delay(*args, on_tick=None, **kwargs):
        seen.append(on_tick)
        if on_tick:
            on_tick()
    monkeypatch.setattr("action.combat.core.session.active_delay", delay)
    combat._sleep_check()
    assert seen == [None]
    assert combat.context.actions == []


def test_cleanup_preserves_unconfirmed_scope_and_failure():
    combat = _combat()
    scope = SimpleNamespace(close=lambda: False, release_pending=True, stop_unconfirmed=True)
    combat.input_scope = scope
    combat.combat_end()
    assert combat.input_scope is scope
    assert scope.release_pending and scope.stop_unconfirmed
    assert combat.out_of_combat_reason == "input_stop_unconfirmed"


def test_hold_observer_checks_shared_exit_without_recapture_or_input(monkeypatch):
    combat = _combat()
    scope = combat.input_scope
    image = combat.frame
    scope.frame_metadata = SimpleNamespace(generation=scope.generation)
    combat.context.recognition_map["重启_寒境曙光"] = make_hit()
    monkeypatch.setattr(combat.context.tasker.controller, "post_screencap", lambda: pytest.fail("observer recaptured"))
    assert combat.observe_hold_state(image)
    assert combat.out_of_combat_reason == "outer_interface"
    assert combat.context.actions == []


def test_hold_observer_rejects_old_generation_and_unknown_frame():
    combat = _combat()
    combat.input_scope.frame_metadata = SimpleNamespace(generation=-1)
    assert combat.observe_hold_state(combat.frame)
    assert combat.out_of_combat_reason == "input_frame_invalid"
    combat.input_scope.frame_metadata = SimpleNamespace(generation=combat.input_scope.generation)
    assert combat.observe_hold_state(object())
    assert combat.context.actions == []


@pytest.mark.parametrize("case", ["stopped", "hp_zero", "transient_hidden_hud"])
def test_hold_observer_uses_existing_stop_hp_and_ui_grace(monkeypatch, case):
    combat = _combat()
    image = combat.frame
    combat.input_scope.frame_metadata = SimpleNamespace(generation=combat.input_scope.generation)
    combat._last_in_combat_time = 100.0
    monkeypatch.setattr("action.combat.core.session.time.monotonic", lambda: 100.5)
    if case == "stopped":
        combat.context.tasker.stopping = True
    elif case == "hp_zero":
        hit = make_hit()
        hit.best_result.text = "0/100"
        combat.context.recognition_map["检查血量"] = hit
        combat._hp_zero_since = 90.0
    assert combat.observe_hold_state(image) is (case != "transient_hidden_hud")
    assert combat.out_of_combat_reason == ("" if case == "transient_hidden_hud" else case)
    assert combat.context.actions == []


def test_missing_identity_templates_are_unknown_in_strict_observation(monkeypatch):
    combat = _combat()
    monkeypatch.setattr("action.combat.core.session.attack_templates_for_cls", lambda _: [])
    with pytest.raises(InverseCrownUnavailable, match="field_identity_unknown"):
        combat.refresh_field_role(combat.roles["R"])
    assert combat.context.actions == []
