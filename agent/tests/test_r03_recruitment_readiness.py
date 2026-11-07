"""Do not finish opening an empty slot on a different OCR budget."""
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from test_support.fakes import FakeContext
from test_r03_recruitment_budget_native import native_probe


@pytest.mark.parametrize("readings,expected", [
    (["1/51", "1/5"], True),
    (["1/51"], False),
    (["1/51", "0/5"], False),
])
def test_open_slot_waits_for_its_unchanged_formation_budget(monkeypatch, readings, expected):
    reader, context = R03Recruitment(RoleSelection()), FakeContext()
    monkeypatch.setattr(reader, "read_formation", Mock(return_value={
        "budget": (1, 5), "empty": {"R": False, "B": True, "Y": True}, "red_first": True,
    }))

    # Model the existing native Ready node: it completes on the first OCR
    # fraction matching its effective expected pattern, within the same task.
    def run_task(entry, pipeline_override=None):
        assert set(pipeline_override) == {
            "矩阵循生_招募能量_列表_整区", "矩阵循生_招募能量_列表_定位",
        }
        patterns = {node["recognition"]["param"]["expected"]
                    for node in pipeline_override.values()}
        assert patterns == {"^1/5$"}
        pattern = patterns.pop()
        matched = next((text for text in readings if re.fullmatch(pattern, text)), None)
        return SimpleNamespace(completed=matched is not None,
                               budget=tuple(map(int, matched.split("/"))) if matched else None)

    monkeypatch.setattr(context, "run_task", run_task)
    monkeypatch.setattr(reader, "completed", lambda _context, detail, _name: detail.completed)
    monkeypatch.setattr(reader, "_completed_list_budget", lambda _context, detail: detail.budget)
    assert reader.open_slot(context, "B") is expected
    assert not context.actions and not context.tasker.controller.clicks


def test_exact_opening_budget_does_not_constrain_a_later_budget_read(tmp_path):
    observations, errors = [], []

    def probe(context, frames):
        try:
            reader = R03Recruitment(RoleSelection())
            observations.append(reader.open_slot(context, "B"))
            observations.append(reader.read_budget(context, frames["owned"]))
        except BaseException as exc:
            errors.append(repr(exc))

    inputs = native_probe(tmp_path, probe, event_team="formation_red", opening="flow_owned")
    assert not errors, repr(errors)
    assert len(inputs) == 1 and observations == [True, (3, 3)]


@pytest.mark.parametrize("ready,budget,expected", [
    (True, (5, 5), True), (False, (5, 5), False), (True, None, False),
])
def test_return_waits_for_current_list_evidence_before_navigation(monkeypatch, ready, budget, expected):
    reader, context = R03Recruitment(RoleSelection()), FakeContext()
    # The initial animation snapshot is unreadable (the device read 515).
    monkeypatch.setattr(reader, "read_budget", Mock(return_value=None))
    monkeypatch.setattr(reader, "read_formation", Mock(return_value={"budget": (5, 5)}))
    monkeypatch.setattr(reader, "_completed_list_budget", lambda *_: budget)
    calls = []

    def run_task(entry, pipeline_override=None):
        calls.append(entry)
        return SimpleNamespace(completed=ready if entry == "矩阵循生_招募列表就绪" else True)

    monkeypatch.setattr(context, "run_task", run_task)
    monkeypatch.setattr(reader, "completed", lambda _context, detail, _name: detail.completed)
    result = reader.return_to_formation(context)
    assert (result is not None) is expected
    if expected:
        assert calls == ["矩阵循生_招募列表就绪", "矩阵循生_返回编队"]
    else:
        assert "矩阵循生_返回编队" not in calls
    assert not context.actions and not context.tasker.controller.clicks
