"""Daily game-day, storage failure and admission lifecycle contract."""

import datetime as dt
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from action.basics import daily_progress as dp
from action.basics import role_cache_policy as cache_policy

BEFORE = dt.datetime(2026, 11, 25, 4, 59, 59)
AFTER = dt.datetime(2026, 11, 25, 5, 0, 1)
ENTRY = "领取体力"
KEY = "领取体力"
GUARD = "日常断点_领取体力"
MARK = "日常完成_领取体力"


class Context:
    def __init__(self):
        self.tasker = SimpleNamespace(stopping=False)
        self.nodes = {
            ENTRY: {"attach": {dp.GUARD_POINTER: GUARD}},
            GUARD: {"attach": {"daily_progress_key": KEY}, "recognition": {
                "type": "Custom", "param": {"custom_recognition": "DailyProgressCheck"},
            }},
            MARK: {"attach": {"daily_progress_key": KEY}},
            dp.ADMIT_NODE: {"next": [], "attach": {}},
        }

    def get_node_data(self, name):
        return deepcopy(self.nodes.get(name))

    def override_pipeline(self, patch):
        for name, node in patch.items():
            self.nodes[name]["attach"].update(node["attach"])
        return True

    def override_next(self, name, next_list):
        self.nodes[name]["next"] = next_list
        return True


def arg(name, *, task_id=77, entry=ENTRY, param=""):
    return SimpleNamespace(node_name=name, task_detail=SimpleNamespace(task_id=task_id, entry=entry),
                           custom_action_param=param, custom_recognition_param=param)


@pytest.fixture
def clock_ledger(tmp_path, monkeypatch):
    clock = {"now": BEFORE}
    path = tmp_path / "daily_progress.json"
    monkeypatch.setattr(dp, "_now", lambda: clock["now"])
    monkeypatch.setattr(dp, "ledger_path", lambda: path)
    return path, clock


def test_game_day_boundary_five_oclock():
    hour = cache_policy.DEFAULT_REFRESH_HOUR
    base = dt.datetime(2026, 11, 23)
    for now in [base, base.replace(hour=4, minute=59, second=59)]:
        assert dp.game_day(now, hour) == "2026-11-22"
    for now in [base.replace(hour=5), base.replace(hour=5, minute=1), base.replace(hour=23, minute=59)]:
        assert dp.game_day(now, hour) == "2026-11-23"


@pytest.mark.parametrize("raw,reason", [
    (b"\xffbroken", "ledger_bad_encoding"), (b"{ not json", "ledger_bad_json"),
    (b"[]", "ledger_unsupported_format"), (b'{"version":true,"entries":{}}', "ledger_unsupported_format"),
    (b'{"version":1,"entries":[]}', "ledger_unsupported_entries"),
    (b'{"version":1,"entries":{},"entries":{}}', "ledger_duplicate_field"),
    (b'{"version":1,"entries":{},"meta":NaN}', "ledger_bad_number"),
    (b'{"version":1,"entries":{},"meta":{"values":[1e999]}}', "ledger_bad_number"),
    (b'{"version":1,"entries":{},"meta":-1e999}', "ledger_bad_number"),
    (rb'{"version":1,"entries":{},"meta":["\ud800"]}', "ledger_bad_encoding"),
    (rb'{"version":1,"entries":{"\udfff":{"game_day":"2026-11-24","result":"done"}}}', "ledger_bad_encoding"),
    (b'{"version":1,"entries":{"test":{"game_day":"2026-02-31","result":"done"}}}', "ledger_unsupported_entries"),
])
def test_corrupt_ledger_is_distinct_from_missing_and_preserved(clock_ledger, raw, reason):
    path, _ = clock_ledger
    assert dp.read_ledger() is None
    path.write_bytes(raw)
    with pytest.raises(dp.LedgerError, match=reason):
        dp.read_ledger()
    assert not dp.DailyProgressReset().run(Context(), arg("重置日常断点")).success
    assert path.read_bytes() == raw


def test_rewritable_optional_metadata_survives_mark(clock_ledger):
    path, _ = clock_ledger
    path.write_bytes(rb'{"version":1,"entries":{},"meta":{"\ud83d\ude00":[1.25e3]}}')
    context = Context()
    assert dp.DailyProgressAdmit().run(context, arg(dp.ADMIT_NODE)).success
    assert dp.DailyProgressMark().run(context, arg(MARK)).success
    ledger = dp.read_ledger()
    assert ledger["meta"] == {"\U0001f600": [1250.0]}
    assert ledger["entries"][KEY]["game_day"] == dp.game_day(BEFORE)


def test_permission_error_blocks_instead_of_running(clock_ledger, monkeypatch):
    path, _ = clock_ledger
    path.write_text('{"version":1,"entries":{}}', encoding="utf-8")
    original_open = Path.open
    with monkeypatch.context() as patch:
        def inaccessible(self, *args, **kwargs):
            if self == path:
                raise PermissionError("private path not for logs")
            return original_open(self, *args, **kwargs)

        patch.setattr(Path, "open", inaccessible)
        ctx = Context()
        assert not dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
        assert ctx.nodes[dp.ADMIT_NODE]["next"] == []
    assert path.exists()


def test_mark_same_day_existing_v1_and_new_game_day(clock_ledger):
    path, clock = clock_ledger
    ctx = Context()
    assert dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
    assert ctx.nodes[dp.ADMIT_NODE]["next"] == [GUARD, dp.REJECT_NODE]
    assert dp.DailyProgressCheck().analyze(ctx, arg(GUARD)).detail == {"status": "pending", "key": KEY}
    assert dp.DailyProgressMark().run(ctx, arg(MARK)).success
    assert dp.is_done(KEY) and not dp.is_done(KEY, AFTER)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1, "entries": {KEY: {"game_day": "2026-11-24", "result": "done", "updated_at": BEFORE.isoformat()}}
    }
    assert not list(path.parent.glob("*.tmp"))
    second_ctx = Context()
    assert dp.DailyProgressAdmit().run(second_ctx, arg(dp.ADMIT_NODE, task_id=88)).success
    assert second_ctx.nodes[dp.ADMIT_NODE]["next"] == ["空任务"]
    assert dp.TICKET not in second_ctx.nodes[dp.ADMIT_NODE]["attach"]
    clock["now"] = AFTER
    another_ctx = Context()
    assert dp.DailyProgressAdmit().run(another_ctx, arg(dp.ADMIT_NODE, task_id=89)).success
    assert another_ctx.nodes[dp.ADMIT_NODE]["next"] == [GUARD, dp.REJECT_NODE]


@pytest.mark.parametrize("failed_override", ["override_pipeline", "override_next"])
def test_mandatory_admission_fails_when_cannot_publish_or_route(clock_ledger, monkeypatch, failed_override):
    ctx = Context()
    monkeypatch.setattr(ctx, failed_override, lambda *args: False)
    assert not dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
    assert ctx.nodes[dp.ADMIT_NODE]["next"] == []


def test_skip_does_not_finish_if_override_crosses_game_day(clock_ledger, monkeypatch, caplog):
    _, clock = clock_ledger
    dp.mark_done(KEY)
    ctx = Context()
    override = ctx.override_next

    def crossing_route(name, next_list):
        assert next_list == ["空任务"]
        clock["now"] = AFTER
        return override(name, next_list)

    monkeypatch.setattr(ctx, "override_next", crossing_route)
    assert not dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
    assert "admission_blocked reason=game_day_changed" in caplog.text


@pytest.mark.parametrize("case", ["day", "task_id", "entry", "key", "stopped"])
def test_no_wrong_or_stale_admission_can_mark(clock_ledger, case):
    path, clock = clock_ledger
    dp.mark_done("另一个实例")
    before = path.read_bytes()
    ctx = Context()
    assert dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
    options = {}
    if case == "day":
        clock["now"] = AFTER
        # The same native task can re-enter through navigation; it may not re-admit itself.
        assert not dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
    elif case == "entry":
        options["entry"] = "另一个任务"
    elif case == "key":
        ctx.nodes[MARK]["attach"]["daily_progress_key"] = "另一个实例"
    elif case == "task_id":
        options["task_id"] = 78
    elif case == "stopped":
        ctx.tasker.stopping = True
    assert dp.DailyProgressMark().run(ctx, arg(MARK, **options)).success
    assert path.read_bytes() == before


def test_terminal_callback_without_admission_does_not_write(clock_ledger, caplog):
    ledger, _ = clock_ledger
    assert dp.DailyProgressMark().run(Context(), arg(MARK)).success
    assert "mark_omitted reason=admission_missing_or_mismatched" in caplog.text
    assert not ledger.exists()


@pytest.mark.parametrize("failure", ["after_admission_corruption", "replace_failure", "late_stop", "late_rollover"])
def test_mark_failure_keeps_previous_bytes_and_releases_own_temp_file(clock_ledger, monkeypatch, failure, caplog):
    path, clock = clock_ledger
    dp.mark_done("另一个实例")
    ctx = Context()
    assert dp.DailyProgressAdmit().run(ctx, arg(dp.ADMIT_NODE)).success
    if failure == "after_admission_corruption":
        path.write_bytes(b"\xffbroken")
    elif failure == "replace_failure":
        def blocked(*args):
            raise PermissionError("private path not for logs")

        monkeypatch.setattr(dp.os, "replace", blocked)
    else:
        sync = dp.os.fsync

        def after_sync(fd):
            sync(fd)
            if failure == "late_rollover":
                clock["now"] = AFTER
            else:
                ctx.tasker.stopping = True

        monkeypatch.setattr(dp.os, "fsync", after_sync)
    before = path.read_bytes()
    assert dp.DailyProgressMark().run(ctx, arg(MARK)).success
    assert path.read_bytes() == before and not list(path.parent.glob("*.tmp"))
    assert "mark_omitted reason=" in caplog.text and "private path" not in caplog.text


def test_reset_preserves_version_and_clears_valid_entries(clock_ledger):
    ledger, _ = clock_ledger
    dp.mark_done("a")
    dp.mark_done("b")
    assert dp.DailyProgressReset().run(Context(), arg("重置日常断点")).success
    assert dp.read_ledger(ledger) == {"version": 1, "entries": {}}
    assert dp.reset_entries() == 0


def test_resolve_key_precedence():
    attach = {"daily_progress_key": "购买物品/逆元碎片-普通商店"}
    params = {"key": "参数键"}
    assert dp.resolve_key("兜底", attach, params) == "购买物品/逆元碎片-普通商店"
    assert dp.resolve_key("兜底", {}, params) == "参数键"
    assert dp.resolve_key("兜底", {}, {}) == "兜底"
    assert dp.resolve_key("兜底", {"daily_progress_key": "  "}, {}) == "兜底"
