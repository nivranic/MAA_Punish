"""R03 team bookkeeping rejects unknown deductions and unconfirmed color slots."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from action.combat.core.team import TEAM_ROSTER_NODE, load_team_roster_from_context
from test_support.fakes import FakeContext

ATTACKER = "露西亚·深红囚影"
SUPPORTS = ["丽芙·流光", "薇拉·瑰丽[试用]"]


def formation(budget=5):
    return {"budget": (budget, 5), "empty": {"B": True, "R": True, "Y": True}, "red_first": False}


def scene(monkeypatch, *, main_cost=4, support_result=True, wrong_slot=False):
    reader, context = R03Recruitment(RoleSelection()), FakeContext()
    state = {"formation": formation(), "list": True, "slot": None}

    def capture(*_args):
        return deepcopy(state["formation"]) if not state["list"] else None

    def return_to_formation(_context):
        state["list"] = False
        return capture()

    def open_slot(_context, color):
        assert state["formation"]["empty"][color]
        state.update({"list": True, "slot": color})
        return True

    def recruit(_context, _roles, name, _tries):
        assert state["list"]
        cost = main_cost if name == ATTACKER else 1
        current, maximum = state["formation"]["budget"]
        if name != ATTACKER and support_result is not True:
            if support_result == "unknown_deduction":
                state["formation"]["budget"] = (current - 1, maximum)
            return False
        assert current >= cost
        state["formation"]["budget"] = (current - cost, maximum)
        state["formation"]["empty"]["B" if wrong_slot else state["slot"]] = False
        state["list"] = False
        return True

    def ensure_red(_context):
        state["formation"]["red_first"] = True
        return True

    monkeypatch.setattr(reader, "return_to_formation", Mock(side_effect=return_to_formation))
    monkeypatch.setattr(reader, "open_slot", Mock(side_effect=open_slot))
    monkeypatch.setattr(reader, "recruit", Mock(side_effect=recruit))
    monkeypatch.setattr(reader, "read_formation", Mock(side_effect=capture))
    monkeypatch.setattr(reader, "ensure_red_leader", Mock(side_effect=ensure_red))
    monkeypatch.setattr(reader, "red_ready", Mock(side_effect=lambda *_: state["formation"]["red_first"]))
    monkeypatch.setattr(reader, "read_budget", Mock(side_effect=lambda *_: state["formation"]["budget"]))
    context.run_recognition = Mock(return_value=SimpleNamespace(hit=True))
    return reader, context, state


@pytest.mark.parametrize("main_cost,expected", [(4, {"R": ATTACKER, "B": SUPPORTS[0], "Y": ""}),
                                                (2, {"R": ATTACKER, "B": SUPPORTS[0], "Y": SUPPORTS[1]})])
def test_team_publishes_actual_slots_and_never_recruits_both_versions(monkeypatch, main_cost, expected):
    reader, context, state = scene(monkeypatch, main_cost=main_cost)
    candidates = [SUPPORTS[0], SUPPORTS[0] + "[试用]", SUPPORTS[1]]
    assert reader.recruit_team(context, {}, ATTACKER, candidates, 3) == expected
    assert [row.args[2] for row in reader.recruit.call_args_list] == [ATTACKER] + [n for n in expected.values() if n and n != ATTACKER]
    assert state["formation"]["budget"] == ((0, 5) if main_cost == 4 else (1, 5))


def test_successful_deduction_into_wrong_color_rejects_the_entire_roster(monkeypatch):
    reader, context, _state = scene(monkeypatch, wrong_slot=True)
    assert reader.recruit_team(context, {}, ATTACKER, SUPPORTS, 3) is None
    reader.ensure_red_leader.assert_not_called()


@pytest.mark.parametrize("outcome", [False, "unknown_deduction"])
def test_missing_support_only_allows_solo_after_unchanged_budget_return(monkeypatch, outcome):
    reader, context, _state = scene(monkeypatch, support_result=outcome)
    result = reader.recruit_team(context, {}, ATTACKER, SUPPORTS, 3)
    assert result == ({"R": ATTACKER, "B": "", "Y": ""} if outcome is False else None)
    assert reader.return_to_formation.call_count == (2 if outcome is False else 1)


def test_existing_party_is_not_replaced_or_claimed(monkeypatch):
    reader, context, state = scene(monkeypatch)
    state["formation"]["empty"]["B"] = False
    assert reader.recruit_team(context, {}, ATTACKER, SUPPORTS, 3) is None
    reader.open_slot.assert_not_called()
    reader.recruit.assert_not_called()


def test_unconfirmed_leader_does_not_attempt_supports(monkeypatch):
    reader, context, _state = scene(monkeypatch)
    reader.ensure_red_leader.side_effect = None
    reader.ensure_red_leader.return_value = False
    assert reader.recruit_team(context, {}, ATTACKER, SUPPORTS, 3) is None
    assert [row.args[1] for row in reader.open_slot.call_args_list] == ["R"]


def test_stopping_after_team_confirmation_does_not_publish(monkeypatch, tmp_path):
    selector = RoleSelection()
    selector._cache_path = Mock(return_value=tmp_path / "absent.json")
    selector._load_cache = Mock(return_value={ATTACKER: {"power": 7586}})
    selector.send_msg = Mock()
    context = FakeContext(node_data={"pick": {"attach": {"selection_mode": "roguelike_3", "need_multi": True}}})

    def stop(*_args):
        context.tasker.stopping = True
        return {"R": ATTACKER, "B": SUPPORTS[0], "Y": ""}

    monkeypatch.setattr(R03Recruitment, "recruit_team", stop)
    assert not selector.run(context, SimpleNamespace(node_name="pick", custom_action_param="{}")).success
    assert load_team_roster_from_context(context) is None


@pytest.mark.parametrize("confirmed", [False, True])
def test_role_selection_publishes_exact_new_team_only_after_confirmation(monkeypatch, tmp_path, confirmed):
    selector = RoleSelection()
    selector._cache_path = Mock(return_value=tmp_path / "absent.json")
    selector._load_cache = Mock(return_value={ATTACKER: {"power": 7586}, SUPPORTS[0]: {"power": 456}})
    selector.send_msg = Mock()
    selector._publish_combat_team_roster = Mock(side_effect=AssertionError("R03 must not merge stale colors"))
    team = {"R": ATTACKER, "B": SUPPORTS[0], "Y": ""}
    recruit = Mock(return_value=team if confirmed else None)
    monkeypatch.setattr(R03Recruitment, "recruit_team", recruit)
    context = FakeContext(node_data={
        "pick": {"attach": {"selection_mode": "roguelike_3", "need_multi": True}},
        TEAM_ROSTER_NODE: {"attach": {"R": "Oblivion", "B": "Luminance", "Y": "Glory"}},
    })
    result = selector.run(context, SimpleNamespace(node_name="pick", custom_action_param="{}"))
    assert result.success is confirmed
    assert recruit.call_args.args[3] == SUPPORTS
    assert load_team_roster_from_context(context) == (
        {"R": "CrimsonWeave", "B": "GeneralFight", "Y": ""} if confirmed
        else {"R": "Oblivion", "B": "Luminance", "Y": "Glory"}
    )
    if confirmed:
        assert context.node_data[TEAM_ROSTER_NODE]["attach"]["role_names"] == team
