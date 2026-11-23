"""daily_progress：游戏日账本与守卫/落账/重置契约。"""

import datetime as dt
import json
from types import SimpleNamespace

from action.basics import daily_progress as dp
from action.basics import role_cache_policy as cache_policy


class _Ctx:
    def __init__(self, attach=None):
        self._attach = attach or {}

    def get_node_data(self, name):
        return {"attach": self._attach}


def _check(ctx, node="日常断点_领取体力", param=""):
    return dp.DailyProgressCheck().analyze(
        ctx,
        SimpleNamespace(node_name=node, custom_recognition_param=param),
    )


def _mark(ctx, node="日常完成_领取体力", param=""):
    return dp.DailyProgressMark().run(
        ctx,
        SimpleNamespace(node_name=node, custom_action_param=param),
    )


def test_game_day_boundary_five_oclock():
    hour = cache_policy.DEFAULT_REFRESH_HOUR
    base = dt.datetime(2026, 11, 23, 0, 0, 0)
    assert dp.game_day(base, hour) == "2026-11-22"
    assert dp.game_day(base.replace(hour=4, minute=59), hour) == "2026-11-22"
    assert dp.game_day(base.replace(hour=5, minute=0), hour) == "2026-11-23"
    assert dp.game_day(base.replace(hour=5, minute=1), hour) == "2026-11-23"
    assert dp.game_day(base.replace(hour=23, minute=59), hour) == "2026-11-23"


def test_read_missing_or_corrupt_ledger_returns_empty(tmp_path):
    assert dp.read_ledger(tmp_path / "none.json")["entries"] == {}
    bad = tmp_path / "daily_progress.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert dp.read_ledger(bad)["entries"] == {}
    bad.write_text("[]", encoding="utf-8")
    assert dp.read_ledger(bad)["entries"] == {}


def test_mark_and_done_same_game_day_only(tmp_path):
    path = tmp_path / "daily_progress.json"
    evening = dt.datetime(2026, 11, 22, 23, 30)
    dp.mark_done("领取体力", now=evening, path=path)
    assert dp.is_done("领取体力", now=evening, path=path)
    # 04:59 仍属同一游戏日
    assert dp.is_done("领取体力", now=dt.datetime(2026, 11, 23, 4, 59), path=path)
    # 05:00 起翻日，断点失效
    assert not dp.is_done("领取体力", now=dt.datetime(2026, 11, 23, 5, 0), path=path)


def test_write_ledger_atomic_no_tmp_left(tmp_path):
    path = tmp_path / "daily_progress.json"
    dp.mark_done("a", now=dt.datetime(2026, 11, 23, 6, 0), path=path)
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["entries"]["a"]["result"] == "done"


def test_reset_entries_clears(tmp_path):
    path = tmp_path / "daily_progress.json"
    dp.mark_done("a", path=path)
    dp.mark_done("b", path=path)
    assert dp.reset_entries(path=path) == 2
    assert dp.read_ledger(path)["entries"] == {}
    assert dp.reset_entries(path=path) == 0


def test_resolve_key_precedence():
    attach = {"daily_progress_key": "购买物品/逆元碎片-普通商店"}
    params = {"key": "参数键"}
    assert dp.resolve_key("兜底", attach, params) == "购买物品/逆元碎片-普通商店"
    assert dp.resolve_key("兜底", {}, params) == "参数键"
    assert dp.resolve_key("兜底", {}, {}) == "兜底"
    assert dp.resolve_key("兜底", {"daily_progress_key": "  "}, {}) == "兜底"


def test_check_hits_when_pending_and_misses_when_done(tmp_path):
    monkey_path = tmp_path / "daily_progress.json"
    dp.mark_done("领取体力", now=dt.datetime(2026, 11, 23, 6, 0), path=monkey_path)
    real = dp.ledger_path
    dp.ledger_path = lambda: monkey_path
    try:
        ctx = _Ctx({"daily_progress_key": "领取体力"})
        assert _check(ctx) is None  # 已完成 → 跳过
        ctx_other = _Ctx({"daily_progress_key": "领取邮件"})
        result = _check(ctx_other)  # 未完成 → 继续工作链
        assert result is not None
        assert result.detail["status"] == "pending"
    finally:
        dp.ledger_path = real


def test_mark_writes_key_from_attach_and_param(tmp_path):
    ledger = tmp_path / "daily_progress.json"
    real = dp.ledger_path
    dp.ledger_path = lambda: ledger
    try:
        assert _mark(_Ctx({"daily_progress_key": "购买物品/涂装设计草稿-宿舍商店"})).success
        assert dp.is_done("购买物品/涂装设计草稿-宿舍商店", path=ledger)
        assert _mark(_Ctx(), param='{"key": "战令"}').success
        assert dp.is_done("战令", path=ledger)
    finally:
        dp.ledger_path = real


def test_reset_action_clears(tmp_path):
    ledger = tmp_path / "daily_progress.json"
    dp.mark_done("a", path=ledger)
    real = dp.ledger_path
    dp.ledger_path = lambda: ledger
    try:
        result = dp.DailyProgressReset().run(
            _Ctx(), SimpleNamespace(node_name="x", custom_action_param="")
        )
        assert result.success
        assert dp.read_ledger(ledger)["entries"] == {}
    finally:
        dp.ledger_path = real
