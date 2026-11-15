"""A newly observed ultimate must end farming without a trailing attack."""
import pytest

from test_oblivion_yield import make_role


@pytest.mark.parametrize("boundary", ["opening", "core", "balls"])
def test_ready_during_farming_advances_and_yields_before_cast(boundary):
    role, state, inputs = make_role("farm")
    role._opening_charge_attempted = boundary != "opening"
    role._observe_form = lambda: ("normal", True)
    role.action.check_status = lambda _: boundary == "core"
    reads = 0

    def energy():
        nonlocal reads
        reads += 1
        return reads == 2

    role.action.check_Skill_energy_bar = energy
    role.perform()

    assert role.phase == "ult_open"
    assert inputs == (["attack"] if boundary == "balls" else [])
    assert role._farm_ticks == 0 and role._clear_ticks == 0
    assert not role._open_cast_requested
    state.allowed = False
    role.perform()
    assert state.checks == 2
    assert inputs == (["attack"] if boundary == "balls" else [])


@pytest.mark.parametrize("boundary", ["first", "opening", "core", "balls"])
@pytest.mark.parametrize("ready", [False, True])
def test_stop_during_ready_observation_never_sends_tail_or_cast(boundary, ready):
    role, _, inputs = make_role("farm")
    role._opening_charge_attempted = boundary != "opening"
    role._observe_form = lambda: ("normal", True)
    role.action.check_status = lambda _: boundary == "core"
    reads = 0

    def energy():
        nonlocal reads
        reads += 1
        if reads == (1 if boundary == "first" else 2):
            role.combat.context.tasker.stopping = True
            return ready
        return False

    role.action.check_Skill_energy_bar = energy
    role.perform()

    assert role.phase == "farm"
    assert inputs == (["attack"] if boundary == "balls" else [])
    assert role._farm_ticks == 0 and role._clear_ticks == 0


def test_ready_during_later_elimination_keeps_unspent_batch_until_next_tick():
    role, _, inputs = make_role("farm")
    role._clear_ticks = 3
    reads = iter([False, True])
    role.action.check_Skill_energy_bar = lambda: next(reads)
    role.perform()
    assert role.phase == "ult_open" and inputs == []
    assert role._clear_ticks == 3 and role._farm_ticks == 0


def test_no_balls_still_completes_original_farm_tick():
    role, state, inputs = make_role("farm")
    state.balls = 0
    role.perform()
    assert role.phase == "farm" and inputs == ["attack", "attack"]
    assert role._farm_ticks == 1 and role._clear_ticks == 0
