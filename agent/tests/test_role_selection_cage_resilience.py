"""幻痛囚笼编入韧性：槽位打开失败降级、耐力不足判定与名册只认领实际编入色位。

背景（2026-10-30 D06 真机证据）：编入队伍点击后命中"角色耐力不足"弹窗时，
流水线重选链会嵌套重新配队；旧实现忽略 run_task 结果——
1) 打开黄色位置超时后仍在错误页面滑动找角色约 40 秒；
2) 编入确认点击（find_role 内部识别）成功即清除囚笼次数缓存，编入被拒后缓存失真；
3) 名册发布未编入的色位，战斗侧拿到幻影成员。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.basics.RoleSelection import RoleSelection
from action.combat.core.team import (
    TEAM_ROSTER_NODE,
    role_display_name_to_cls,
    roster_from_role_selection,
)
from test_support.fakes import FakeContext, FakeTaskDetail

ATTACKER = "比安卡·晖暮"
TANK = "七实·芒星之迹"
SUPPORT = "布偶熊·骇影"
NESTED_ATTACKER = "露娜·终焉"

STAMINA_REJECTION_NODES = [
    "编入队伍",
    "角色耐力不足",
    "返回_重新选人",
    "选择首发_重新选人",
    "选择角色程序_onlyNode",
]

ATTACH = {"cage": True, "need_element": "", "pick": [], "need_multi": True}


@pytest.fixture
def fielding(tmp_path, monkeypatch):
    path = tmp_path / "role_cache.json"
    path.write_text(json.dumps({"main_update_at": 1}), encoding="utf-8")
    selector = RoleSelection()
    monkeypatch.setattr(selector, "_cache_path", lambda: path)
    monkeypatch.setattr(selector, "send_msg", Mock())
    monkeypatch.setattr(selector, "save_screenshot", Mock(return_value=True))
    monkeypatch.setattr(selector, "find_role", Mock(return_value=True))
    monkeypatch.setattr(
        selector,
        "select_best_team",
        lambda role_weight, *, need_multi=True: {
            "attacker": {"name": ATTACKER, "weight": 6900.0},
            "tank": {"name": TANK, "weight": 4600.0},
            "support": {"name": SUPPORT, "weight": 6900.0},
        },
    )

    cache = {
        ATTACKER: {"dark": 100, "cage": 3},
        TANK: {"lightning": 100, "cage": 3},
        SUPPORT: {"ice": 100, "cage": 3},
    }
    monkeypatch.setattr(selector, "_load_cache", lambda freq: dict(cache))
    monkeypatch.setattr(
        selector, "save_cache", lambda data, freq: cache.update(data)
    )

    context = FakeContext(node_data={"pick": {"attach": dict(ATTACH)}})
    # 槽位默认成功：进入选人界面已确认
    context.run_task_results["打开黄色位置"] = FakeTaskDetail(
        "打开黄色位置", ["打开黄色位置", "确认进入选人界面"]
    )
    context.run_task_results["打开蓝色位置"] = FakeTaskDetail(
        "打开蓝色位置", ["打开蓝色位置", "确认进入选人界面"]
    )
    argv = SimpleNamespace(node_name="pick", custom_action_param=None)
    monkeypatch.setattr("action.basics.RoleSelection.time.sleep", lambda s: None)
    return selector, context, argv, cache


def published_roster(context: FakeContext) -> dict[str, str]:
    node = context.get_node_data(TEAM_ROSTER_NODE) or {}
    return node.get("attach") or {}


def run_standard(selector, context, argv):
    argv.custom_action_param = json.dumps({"selection_mode": "standard"})
    return selector.run(context, argv)


def test_clean_fielding_consumes_cache_and_claims_all_slots(fielding):
    selector, context, argv, cache = fielding

    assert run_standard(selector, context, argv).success

    assert all(role["cage"] == 0 for role in cache.values())
    assert published_roster(context) == roster_from_role_selection(
        ATTACKER, TANK, SUPPORT
    )
    fielded = [call.args[2] for call in selector.find_role.call_args_list]
    assert fielded == [ATTACKER, TANK, SUPPORT]
    entries = [call["entry"] for call in context.run_task_calls]
    assert entries.count("编入队伍") == 3
    assert "返回" not in entries


def test_stamina_rejection_consumes_but_does_not_claim_attacker(fielding):
    selector, context, argv, cache = fielding
    # 首次编入（首发位）命中耐力不足；游戏证实无囚笼次数，缓存应清除
    # （否则重选链会再次选中同一角色形成循环），但首发色位不得认领。
    context.run_task_results["编入队伍"] = [
        FakeTaskDetail("编入队伍", STAMINA_REJECTION_NODES)
    ]

    assert run_standard(selector, context, argv).success

    assert cache[ATTACKER]["cage"] == 0
    assert cache[TANK]["cage"] == 0
    assert cache[SUPPORT]["cage"] == 0
    roster = published_roster(context)
    assert roster["R"] == ""
    assert roster["Y"] == role_display_name_to_cls(TANK)
    assert roster["B"] == role_display_name_to_cls(SUPPORT)
    assert selector._cage_detour_count == 1


def test_unknown_fielding_failure_preserves_cache(fielding):
    selector, context, argv, cache = fielding
    # 首次编入子任务未启动：结果未知，不得清除囚笼次数
    context.run_task_results["编入队伍"] = [None]

    assert run_standard(selector, context, argv).success

    assert cache[ATTACKER]["cage"] == 3
    assert cache[TANK]["cage"] == 0
    assert cache[SUPPORT]["cage"] == 0
    roster = published_roster(context)
    assert roster["R"] == ""
    assert selector._cage_detour_count == 0


def test_fielding_without_click_preserves_cache(fielding):
    selector, context, argv, cache = fielding
    # 首次编入子任务启动但未确认点击编入（OCR 超时）：结果未知，保留缓存
    context.run_task_results["编入队伍"] = [FakeTaskDetail("编入队伍", [])]

    assert run_standard(selector, context, argv).success

    assert cache[ATTACKER]["cage"] == 3
    roster = published_roster(context)
    assert roster["R"] == ""


def test_slot_open_failure_skips_role_search_and_claim(fielding):
    selector, context, argv, cache = fielding
    context.run_task_results["打开黄色位置"] = FakeTaskDetail(
        "打开黄色位置", ["打开黄色位置"]
    )

    assert run_standard(selector, context, argv).success

    fielded = [call.args[2] for call in selector.find_role.call_args_list]
    assert TANK not in fielded
    assert SUPPORT in fielded
    entries = [call["entry"] for call in context.run_task_calls]
    assert "返回" not in entries  # 未进入选人页时不得盲点返回
    assert cache[TANK]["cage"] == 3
    roster = published_roster(context)
    assert roster["Y"] == ""
    assert roster["R"] == role_display_name_to_cls(ATTACKER)
    assert roster["B"] == role_display_name_to_cls(SUPPORT)


def test_slot_role_not_found_returns_and_skips_claim(fielding):
    selector, context, argv, cache = fielding
    selector.find_role.side_effect = lambda ctx, rd, name, *a, **k: name != TANK

    assert run_standard(selector, context, argv).success

    entries = [call["entry"] for call in context.run_task_calls]
    assert "返回" in entries  # 已进入选人页, 按既有约定返回
    assert cache[TANK]["cage"] == 3
    roster = published_roster(context)
    assert roster["Y"] == ""
    assert roster["R"] == role_display_name_to_cls(ATTACKER)
    assert roster["B"] == role_display_name_to_cls(SUPPORT)


def test_detour_keeps_nested_attacker_claim(fielding):
    selector, context, argv, cache = fielding
    # D06 真机事故形态：首发/黄位正常编入，支援位耐力不足触发重选链；
    # 嵌套 run 已重新编入首发并发布名册（此处以前置 attach 模拟）。
    nested = roster_from_role_selection(NESTED_ATTACKER, None, None)
    context.node_data[TEAM_ROSTER_NODE] = {"attach": dict(nested)}

    state = {"fieldings": 0}

    def scripted_run_task(entry, pipeline_override=None):
        if entry == "编入队伍":
            state["fieldings"] += 1
            if state["fieldings"] == 3:  # 支援位编入被拒
                detail = FakeTaskDetail("编入队伍", STAMINA_REJECTION_NODES)
                detail.node_id_list = context.tasker.register_nodes(
                    detail.node_names
                )
                return detail
        return FakeContext.run_task(context, entry, pipeline_override)

    context.run_task = scripted_run_task

    assert run_standard(selector, context, argv).success

    roster = published_roster(context)
    # 本 run 的首发认领已失效, 保留嵌套重选链发布的首发
    assert roster["R"] == role_display_name_to_cls(NESTED_ATTACKER)
    assert roster["Y"] == role_display_name_to_cls(TANK)
    assert roster["B"] == ""
    assert selector._cage_detour_count == 1


def test_exclusive_slot_open_failure_skips_claim(tmp_path, monkeypatch):
    path = tmp_path / "role_cache.json"
    path.write_text(json.dumps({"main_update_at": 1}), encoding="utf-8")
    selector = RoleSelection()
    monkeypatch.setattr(selector, "_cache_path", lambda: path)
    monkeypatch.setattr(selector, "send_msg", Mock())
    monkeypatch.setattr(selector, "find_role", Mock(return_value=True))
    monkeypatch.setattr(
        selector,
        "_team_from_exclusive_pick",
        lambda pick, need_multi: {
            "attacker": ATTACKER,
            "tank": TANK,
            "support": SUPPORT,
        },
    )
    cache = {
        ATTACKER: {"dark": 100, "cage": 3},
        TANK: {"lightning": 100, "cage": 3},
        SUPPORT: {"ice": 100, "cage": 3},
    }
    monkeypatch.setattr(selector, "_load_cache", lambda freq: dict(cache))
    monkeypatch.setattr(
        selector, "save_cache", lambda data, freq: cache.update(data)
    )
    context = FakeContext(node_data={"pick": {"attach": dict(ATTACH)}})
    context.run_task_results["打开黄色位置"] = FakeTaskDetail(
        "打开黄色位置", ["打开黄色位置", "确认进入选人界面"]
    )
    context.run_task_results["打开蓝色位置"] = FakeTaskDetail(
        "打开蓝色位置", ["打开蓝色位置"]
    )
    argv = SimpleNamespace(
        node_name="pick",
        custom_action_param=json.dumps(
            {
                "weight_mode": "exclusive",
                "pick": ["露娜·终焉", "罗塞塔·极锋", "涅缇娅·亡歌"],
            }
        ),
    )
    monkeypatch.setattr("action.basics.RoleSelection.time.sleep", lambda s: None)

    assert selector.run(context, argv).success

    fielded = [call.args[2] for call in selector.find_role.call_args_list]
    assert SUPPORT not in fielded
    roster = published_roster(context)
    assert roster["B"] == ""
    assert roster["R"] == role_display_name_to_cls(ATTACKER)
    assert roster["Y"] == role_display_name_to_cls(TANK)
    assert cache[SUPPORT]["cage"] == 3
