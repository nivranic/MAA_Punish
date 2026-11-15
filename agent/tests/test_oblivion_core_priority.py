"""A ready core must not wait for a five-elimination batch to finish."""
import pytest

from test_oblivion_yield import make_role


@pytest.mark.parametrize("phase,counter", [("farm", "_farm_ticks")])
@pytest.mark.parametrize("cleared", [0, 1, 3, 5])
def test_ready_core_preempts_remaining_balls_and_yields(phase, counter, cleared):
    role, state, inputs = make_role(phase)
    role._clear_ticks = cleared
    role.action.check_status = lambda _: True
    role.action.long_press_attack = lambda ms: inputs.append(("core", ms))

    role.perform()

    assert inputs == [("core", 2000)]
    assert role.phase == phase
    assert getattr(role, counter) == 1 and role._clear_ticks == 0
    state.allowed = False
    role.perform()
    assert state.checks == 2 and inputs == [("core", 2000)]


@pytest.mark.parametrize("phase,next_phase", [("farm", "ult_open"), ("ult_loop", "ult_close")])
def test_ready_ultimate_keeps_priority_over_core(phase, next_phase):
    role, state, inputs = make_role(phase)
    state.ready = True
    role.action.check_status = lambda _: pytest.fail("Core must not delay an available ultimate")
    role.action.long_press_attack = lambda ms: inputs.append(("core", ms))

    role.perform()

    assert role.phase == next_phase and inputs == []


@pytest.mark.parametrize("phase", ["farm"])
@pytest.mark.parametrize("stopped_at", ["core_miss", "core_hit", "energy_recheck"])
def test_stop_during_core_observation_suppresses_all_following_inputs(phase, stopped_at):
    role, _, inputs = make_role(phase)
    def core(_):
        if stopped_at != "energy_recheck":
            role.combat.context.tasker.stopping = True
        return stopped_at != "core_miss"
    checks = 0
    def energy():
        nonlocal checks
        checks += 1
        if stopped_at == "energy_recheck" and checks == 2:
            role.combat.context.tasker.stopping = True
        return False
    role.action.check_status = core
    role.action.check_Skill_energy_bar = energy
    role.action.long_press_attack = lambda ms: inputs.append(("core", ms))

    role.perform()

    assert inputs == [] and role._clear_ticks == 0


@pytest.mark.parametrize("phase", ["farm"])
def test_unavailable_core_preserves_one_elimination_per_identity_check(phase):
    role, state, inputs = make_role(phase)
    role.action.check_status = lambda _: False
    role.action.long_press_attack = lambda _: pytest.fail("Core is unavailable")

    role.perform()

    assert inputs == ["attack", ("ball", 1), "attack"]
    assert state.checks == 1 and role._clear_ticks == 1
