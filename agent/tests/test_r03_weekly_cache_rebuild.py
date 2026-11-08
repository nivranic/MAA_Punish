"""R03 周缓存失效（缓存为空）时必须走识别扫描重建，试玩候选只作末位兜底。

周一 ISO 周翻转后 role_cache 判无效：此前试玩候选在读取阶段预填，
使既有的扫描重建分支不可达，招募页按 [试用] 招募必然失败
（新演算首发页仅提供每日试玩角色）。回归锁定三种结果：
扫描有适配候选→实名招募；扫描无适配候选→降级试玩；扫描全空→快速失败。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from test_support.fakes import FakeContext

ATTACKER = "露西亚·深红囚影"
TEAM = {"R": ATTACKER, "B": "", "Y": ""}


def make_context():
    return FakeContext(node_data={
        "pick": {"attach": {"selection_mode": "roguelike_3", "need_multi": True, "max_try": 1}},
    })


def run_selector(selector, context, monkeypatch, scanned):
    monkeypatch.setattr(selector, "send_msg", Mock())
    monkeypatch.setattr(selector, "save_screenshot", Mock(return_value=True))
    team = Mock(return_value=TEAM)
    monkeypatch.setattr(R03Recruitment, "recruit_team", team)
    monkeypatch.setattr(selector, "recognize_role", Mock(return_value=dict(scanned)))
    result = selector.run(context, SimpleNamespace(node_name="pick", custom_action_param="{}"))
    return result, team


@pytest.fixture
def selector(tmp_path):
    instance = RoleSelection()
    instance._cache_path = Mock(return_value=tmp_path / "absent-role-cache.json")
    return instance


def test_r03_empty_cache_scans_and_recruits_real_attacker(selector, monkeypatch):
    """缓存为空：扫描到适配候选与实名 support 时按实名招募，不落试玩兜底。"""
    context = make_context()
    scanned = {ATTACKER: {"power": 7200}, "丽芙·流光": {"power": 456}}
    trial_builder = Mock(side_effect=AssertionError("扫描有适配候选时不得使用试玩兜底"))
    monkeypatch.setattr(selector, "_build_roguelike3_role_data", trial_builder)

    result, team = run_selector(selector, context, monkeypatch, scanned)

    assert result.success
    selector.recognize_role.assert_called()
    trial_builder.assert_not_called()
    assert team.call_args.args[2] == ATTACKER
    # 薇拉未扫到 → 明确标记的试玩候选；丽芙扫到 → 实名。
    assert team.call_args.args[3] == ["丽芙·流光", "薇拉·瑰丽[试用]"]


def test_r03_scan_without_r3_candidates_falls_back_to_trial(selector, monkeypatch):
    """扫描结果无矩阵适配候选（如页面停在无适配角色的元素筛选）：降级试玩兜底。"""
    context = make_context()
    scanned = {"丽芙·流光": {"power": 456}}
    trial_builder = Mock(return_value={ATTACKER + "[试用]": {"power": 0}})
    monkeypatch.setattr(selector, "_build_roguelike3_role_data", trial_builder)

    result, team = run_selector(selector, context, monkeypatch, scanned)

    assert result.success
    trial_builder.assert_called_once()
    assert "[试用]" in team.call_args.args[2]
    # 扫描到的实名 support 仍按实名解析。
    assert team.call_args.args[3][0] == "丽芙·流光"


def test_r03_completely_empty_scan_fails_fast_without_trial(selector, monkeypatch):
    """扫描一个角色都未识别（页面异常）：快速失败，不再叠试玩招募。"""
    context = make_context()
    trial_builder = Mock(side_effect=AssertionError("扫描全空时不得进入试玩招募"))
    monkeypatch.setattr(selector, "_build_roguelike3_role_data", trial_builder)

    result, team = run_selector(selector, context, monkeypatch, {})

    assert not result.success
    trial_builder.assert_not_called()
    team.assert_not_called()
    assert "角色扫描未识别到任何角色" in selector.send_msg.call_args.args[1]


def test_r03_valid_cache_skips_scan_unchanged(selector, monkeypatch):
    """正式缓存可用：行为不变，不触发扫描，support 名单沿用缓存。"""
    context = make_context()
    selector._load_cache = Mock(return_value={ATTACKER: {"power": 7200}, "丽芙·流光": {"power": 456}})
    trial_builder = Mock(side_effect=AssertionError("缓存可用时不得触发试玩兜底"))
    monkeypatch.setattr(selector, "_build_roguelike3_role_data", trial_builder)

    result, team = run_selector(selector, context, monkeypatch, scanned={"x": {"power": 1}})

    assert result.success
    selector.recognize_role.assert_not_called()
    trial_builder.assert_not_called()
    assert team.call_args.args[2] == ATTACKER
    assert team.call_args.args[3] == ["丽芙·流光", "薇拉·瑰丽[试用]"]
