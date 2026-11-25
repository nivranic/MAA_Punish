"""Recruit the stronger affordable support version without guessing its power."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from maa.define import OCRResult

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from test_support.fakes import FakeContext, FakeJob


OWNED = "薇拉·瑰丽"
TRIAL = OWNED + "[试用]"


def scene(monkeypatch, pages, *, start=0, budget=(1, 5)):
    reader, context = R03Recruitment(RoleSelection()), FakeContext()
    state = {"page": start, "budget": budget, "visible": True}
    context.tasker.controller.post_screencap = lambda: FakeJob(image=state["page"])
    context.run_recognition = Mock(side_effect=lambda *_: SimpleNamespace(hit=state["visible"]))
    monkeypatch.setattr(reader, "read_budget", lambda *_: state["budget"])
    monkeypatch.setattr(reader.selector, "_get_role_element_task", lambda *_: None)

    def candidates(_ctx, page, name, _action):
        power, cost = pages[page].get(name, (None, 1))
        return [((142, 395, 82, 82), budget, cost)] if power is not None and cost <= budget[0] else []

    def power(_ctx, page, _box):
        # Each synthetic page contains exactly one version.
        return next(iter(pages[page].values()))[0] if pages[page] else None

    def swipe(name):
        step = -1 if name == "反向滑动_选人" else 1
        state["page"] = max(0, min(len(pages) - 1, state["page"] + step))
        return True

    monkeypatch.setattr(reader, "candidates", candidates)
    monkeypatch.setattr(reader, "read_power", power, raising=False)
    context.run_action = Mock(side_effect=swipe)
    reader._recruit_from_list = Mock(return_value=True)
    return reader, context, state


@pytest.mark.parametrize("pages,start,expected", [
    ([{OWNED: (600, 1)}, {TRIAL: (5400, 1)}], 0, TRIAL),
    ([{TRIAL: (5400, 1)}, {OWNED: (600, 1)}], 1, TRIAL),
    ([{TRIAL: (5400, 1)}, {OWNED: (7100, 1)}], 0, OWNED),
    ([{TRIAL: (5400, 2)}, {OWNED: (600, 1)}], 0, OWNED),
    ([{}, {TRIAL: (5400, 1)}], 0, TRIAL),
    ([{TRIAL: (600, 1)}, {OWNED: (600, 1)}], 0, OWNED),
])
def test_version_is_chosen_from_all_affordable_pages(monkeypatch, pages, start, expected):
    reader, context, _ = scene(monkeypatch, pages, start=start)
    assert reader.recruit_support(context, {OWNED: {}}, OWNED, 3, (1, 5)) == expected
    reader._recruit_from_list.assert_called_once_with(context, {OWNED: {}}, expected, 3)
    assert context.run_action.call_count <= 8


@pytest.mark.parametrize("failure", ["budget", "page", "stop", "power", "deduction"])
def test_unknown_state_never_claims_a_recruited_support(monkeypatch, failure):
    reader, context, state = scene(monkeypatch, [{OWNED: (600, 1)}, {TRIAL: (5400, 1)}])
    if failure == "budget":
        state["budget"] = (0, 5)
    elif failure == "page":
        state["visible"] = False
    elif failure == "stop":
        def stop(*_):
            context.tasker.stopping = True
            return 5400
        reader.read_power = stop
    elif failure == "power":
        reader.read_power = lambda *_: None
    else:
        reader._recruit_from_list.return_value = False
    assert reader.recruit_support(context, {OWNED: {}}, OWNED, 3, (1, 5)) is None
    if failure != "deduction":
        reader._recruit_from_list.assert_not_called()
    if failure in {"budget", "page"}:
        context.run_action.assert_not_called()


def test_page_change_during_scan_stops_before_further_swipe_or_recruit(monkeypatch):
    reader, context, state = scene(monkeypatch, [{OWNED: (600, 1)}, {TRIAL: (5400, 1)}])
    original = context.run_action.side_effect

    def change(name):
        original(name)
        state["budget"] = (0, 5)
        return True

    context.run_action.side_effect = change
    assert reader.recruit_support(context, {OWNED: {}}, OWNED, 3, (1, 5)) is None
    assert context.run_action.call_count == 1
    reader._recruit_from_list.assert_not_called()


@pytest.mark.parametrize("text,expected", [
    ("5400", 5400), ("600", 600), ("600.", None), ("0", None),
    ("-600", None), ("6/00", None), ("600 5400", None), ("9999999", None),
])
def test_power_requires_one_complete_positive_integer(text, expected):
    context = FakeContext()
    context.run_recognition = Mock(return_value=SimpleNamespace(
        hit=True, best_result=OCRResult(box=(0, 0, 76, 30), score=1.0, text=text),
    ))
    assert R03Recruitment.read_power(context, object(), (142, 395, 82, 82)) == expected


def test_support_filters_once_then_confirms_the_selected_trial_deduction(monkeypatch):
    reader, context, state = scene(monkeypatch, [{TRIAL: (5400, 1)}, {OWNED: (600, 1)}])
    # Exercise the real recruitment path, including its element navigation.
    reader.recruit = R03Recruitment.recruit.__get__(reader)
    reader._recruit_from_list = R03Recruitment._recruit_from_list.__get__(reader)
    roles = {OWNED: {"template": ["synthetic.png"]}}
    monkeypatch.setattr(reader.selector, "_get_role_element_task", lambda *_: "打开暗属性")
    entries = []

    def run_task(entry, pipeline_override=None):
        entries.append(entry)
        if entry == "打开暗属性":
            assert entries.count(entry) == 1, "selected element has no unselected template"
            return SimpleNamespace(name="确认进入暗属性", completed=True)
        if entry == "矩阵循生_点选角色":
            return SimpleNamespace(name="矩阵循生_选中角色", completed=True)
        assert entry == "矩阵循生_编入角色"
        state["budget"] = (0, 5)
        return SimpleNamespace(name="矩阵循生_编入后确认", completed=True)

    context.run_task = run_task
    monkeypatch.setattr(reader, "completed", lambda _ctx, detail, name: detail.completed and detail.name == name)
    monkeypatch.setattr(reader, "read_budget", lambda *_args, **_kwargs: state["budget"])
    assert reader.recruit_support(context, roles, OWNED, 3, (1, 5)) == TRIAL
    assert entries == ["打开暗属性", "矩阵循生_点选角色", "矩阵循生_编入角色"]
    assert state["budget"] == (0, 5)


@pytest.mark.parametrize("support", [False, True])
def test_filter_failure_is_returned_without_running_nested_global_cleanup(monkeypatch, support):
    reader, context, _ = scene(monkeypatch, [{TRIAL: (5400, 1)}])
    reader.recruit = R03Recruitment.recruit.__get__(reader)
    monkeypatch.setattr(reader.selector, "_get_role_element_task", lambda *_: "打开暗属性")

    def run_task(entry, pipeline_override=None):
        assert entry in {"打开暗属性", "确认进入暗属性"}
        expected = {"确认进入暗属性"}
        if entry == "打开暗属性":
            expected.add(entry)
        assert set(pipeline_override) == expected
        assert all(node["on_error"] == [] for node in pipeline_override.values())
        return None

    context.run_task = Mock(side_effect=run_task)
    roles = {OWNED: {"template": ["synthetic.png"]}}
    result = (reader.recruit_support(context, roles, OWNED, 3, (1, 5)) if support
              else reader.recruit(context, roles, TRIAL, 3))
    assert not result
    assert [call.args[0] for call in context.run_task.call_args_list] == [
        "打开暗属性", "确认进入暗属性", "打开暗属性",
    ]
    context.run_action.assert_not_called()
