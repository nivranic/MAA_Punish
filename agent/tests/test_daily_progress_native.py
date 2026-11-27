"""Exercise real task-local ledger callbacks and language/option routing in Maa 5.14.2."""

import datetime as dt
import json
from pathlib import Path
from threading import Event

import pytest
from maa.custom_action import CustomAction
from maa.tasker import Tasker

from action.basics import daily_progress as dp
from test_daily_activity_native import MemoryController, native_resource
from test_support.daily_progress import register_daily_progress
from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
BEFORE = dt.datetime(2026, 11, 25, 4, 59, 59)
AFTER = dt.datetime(2026, 11, 25, 5, 0, 1)
FAIL = "任务失败优雅退出"
ADMIT = dp.ADMIT_NODE
MARK = "日常完成_领取体力"
GUARD = "日常断点_领取体力"


def node_names(tasker, job):
    return [tasker.get_node_detail(i).name for i in job.get().node_id_list]


@pytest.fixture
def ledger_hub(tmp_path, monkeypatch):
    tasker = None
    clock = {"now": BEFORE}
    monkeypatch.setattr(dp, "_now", lambda: clock["now"])

    def bind(lang="base"):
        nonlocal tasker
        inputs = []

        class Sink(MemoryController):
            def click(self, *args):
                inputs.append(args)
                return True

        ctrl = Sink()
        resource = native_resource(tmp_path, lang)
        ledger = tmp_path / "daily_progress.json"
        register_daily_progress(resource, monkeypatch, ledger)
        assert ctrl.post_connection().wait().succeeded
        tasker = Tasker()
        assert tasker.bind(resource, ctrl)
        return tasker, resource, ledger, inputs, clock

    yield bind
    if tasker is not None:
        stop_and_wait_idle(tasker)


CLICK = {"type": "Click", "param": {"target": [700, 400, 1, 1]}}
FAST = {"pre_delay": 0, "post_delay": 0, "rate_limit": 0}


def stamina_override():
    return {
        "领取体力": dict(FAST),
        "补给包售罄": {**FAST, "recognition": "DirectHit", "action": CLICK},
        MARK: dict(FAST),
        "返回主菜单": {**FAST, "recognition": "DirectHit", "action": "DoNothing", "next": []},
        # If a broken callback enters the inherited failure chain, this records real input.
        FAIL: {**FAST, "action": CLICK, "next": []},
    }


@pytest.mark.parametrize("language", ["base", "ja_JP"])
def test_real_stamina_entry_only_skips_positive_completion(ledger_hub, language):
    tasker, _, ledger, inputs, _ = ledger_hub(language)
    patch = stamina_override()
    first = tasker.post_task("领取体力", patch).wait()
    assert first.succeeded, node_names(tasker, first)
    assert node_names(tasker, first) == ["领取体力", ADMIT, GUARD, "补给包售罄", MARK, "返回主菜单"]
    assert len(inputs) == 1
    assert dp.read_ledger(ledger)["entries"]["领取体力"]["game_day"] == dp.game_day(BEFORE)
    second = tasker.post_task("领取体力", patch).wait()
    assert second.succeeded and node_names(tasker, second) == ["领取体力", ADMIT, "空任务"]
    assert len(inputs) == 1


@pytest.mark.parametrize("bad", [
    b"\xffbroken", b"{ not json", b'{"version":2,"entries":{}}',
    b'{"version":1,"entries":{},"entries":{}}', b'{"version":1,"entries":{},"meta":Infinity}',
    b'{"version":1,"entries":{},"meta":{"read_only":[1e999]}}',
    rb'{"version":1,"entries":{},"meta":["\ud800"]}',
])
def test_bad_ledger_is_failed_no_input_and_next_task_can_run(ledger_hub, bad, capsys):
    tasker, _, ledger, inputs, _ = ledger_hub("ja_JP")
    ledger.write_bytes(bad)
    patch = stamina_override()
    first = tasker.post_task("领取体力", patch).wait()
    assert first.failed and node_names(tasker, first) == ["领取体力", ADMIT]
    assert not inputs and ledger.read_bytes() == bad
    reset = tasker.post_task("重置日常断点", {FAIL: patch[FAIL]}).wait()
    assert reset.failed and node_names(tasker, reset) == ["重置日常断点"]
    assert not inputs and ledger.read_bytes() == bad
    assert "Exception ignored" not in capsys.readouterr().err
    # Simulate a user restoring a valid ledger, then reuse the Tasker/resources unchanged.
    dp.write_ledger({"version": 1, "entries": {}}, ledger)
    after = tasker.post_task("领取体力", patch).wait()
    assert after.succeeded and MARK in node_names(tasker, after) and len(inputs) == 1


def test_crossing_reset_does_not_credit_either_day_or_skip_next_task(ledger_hub, caplog):
    tasker, resource, ledger, inputs, clock = ledger_hub()
    dp.mark_done("领取邮件", now=BEFORE, path=ledger)
    old_bytes = ledger.read_bytes()
    patch = stamina_override()

    class Cross(CustomAction):
        def run(self, context, argv):
            clock["now"] = AFTER
            return True

    assert resource.register_custom_action("TestCrossGameDay", Cross())
    patch["补给包售罄"]["action"] = {"type": "Custom", "param": {"custom_action": "TestCrossGameDay"}}
    first = tasker.post_task("领取体力", patch).wait()
    assert first.succeeded and node_names(tasker, first)[-2:] == [MARK, "返回主菜单"]
    assert ledger.read_bytes() == old_bytes and not inputs
    assert "mark_omitted reason=game_day_changed" in caplog.text
    direct = tasker.post_task(MARK, patch).wait()
    assert direct.succeeded and ledger.read_bytes() == old_bytes
    assert "mark_omitted reason=admission_missing_or_mismatched" in caplog.text
    fresh = tasker.post_task("领取体力", stamina_override()).wait()
    assert fresh.succeeded and MARK in node_names(tasker, fresh)
    data = dp.read_ledger(ledger)["entries"]
    assert data["领取体力"]["game_day"] == dp.game_day(AFTER)
    assert data["领取邮件"]["game_day"] == dp.game_day(BEFORE)
    assert len(inputs) == 1


@pytest.mark.parametrize("failure", ["ledger_read", "ticket_write", "route_override"])
def test_unconfirmed_admission_is_native_failure_without_inputs(ledger_hub, monkeypatch, failure):
    from maa.context import Context

    tasker, _, ledger, inputs, _ = ledger_hub()
    if failure == "ledger_read":
        original = Path.open

        def reject(self, *args, **kwargs):
            if self == ledger:
                raise PermissionError("read unavailable")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", reject)
    elif failure == "ticket_write":
        monkeypatch.setattr(Context, "override_pipeline", lambda *_: False)
    else:
        monkeypatch.setattr(Context, "override_next", lambda *_: False)
    job = tasker.post_task("领取体力", stamina_override()).wait()
    assert job.failed and node_names(tasker, job) == ["领取体力", ADMIT]
    assert not inputs and not ledger.exists()


def test_post_stop_discards_private_admission_and_keeps_ledger(ledger_hub):
    tasker, resource, ledger, inputs, _ = ledger_hub()
    entered, release = Event(), Event()

    class Block(CustomAction):
        def run(self, context, argv):
            assert (context.get_node_data(ADMIT)["attach"][dp.TICKET]["task_id"] == argv.task_detail.task_id)
            entered.set()
            return release.wait(5)

    assert resource.register_custom_action("TestPendingAdmittedTask", Block())
    patch = stamina_override()
    patch["补给包售罄"]["action"] = {
        "type": "Custom", "param": {"custom_action": "TestPendingAdmittedTask"},
    }
    job = tasker.post_task("领取体力", patch)
    try:
        assert entered.wait(5)
        stop = tasker.post_stop()
    finally:
        release.set()
    stop.wait()
    job.wait()
    assert MARK not in node_names(tasker, job) and not inputs and not ledger.exists()
    fresh = tasker.post_task("领取体力", stamina_override()).wait()
    assert fresh.succeeded and MARK in node_names(tasker, fresh) and len(inputs) == 1


def test_failed_atomic_write_retains_bytes_and_does_not_repeat_business(ledger_hub, monkeypatch, caplog):
    tasker, _, ledger, inputs, _ = ledger_hub()
    dp.mark_done("领取邮件", path=ledger, now=BEFORE)
    prior = ledger.read_bytes()
    original = dp.os.replace

    def reject_ledger(source, target):
        if target == ledger:
            raise PermissionError("replace unavailable")
        return original(source, target)

    monkeypatch.setattr(dp.os, "replace", reject_ledger)
    job = tasker.post_task("领取体力", stamina_override()).wait()
    assert job.succeeded and node_names(tasker, job)[-2:] == [MARK, "返回主菜单"]
    assert len(inputs) == 1 and ledger.read_bytes() == prior and not list(ledger.parent.glob("*.tmp"))
    assert "mark_omitted reason=PermissionError" in caplog.text


def test_rollover_between_admission_return_and_work_guard_is_named_failure(ledger_hub, monkeypatch):
    tasker, _, ledger, inputs, clock = ledger_hub()
    analyze = dp.DailyProgressCheck.analyze

    def cross(context_self, context, argv):
        clock["now"] = AFTER
        return analyze(context_self, context, argv)

    monkeypatch.setattr(dp.DailyProgressCheck, "analyze", cross)
    job = tasker.post_task("领取体力", stamina_override()).wait()
    assert job.failed
    assert node_names(tasker, job) == ["领取体力", ADMIT, dp.REJECT_NODE]
    assert not ledger.exists() and not inputs


def test_admission_does_not_replace_existing_work_owner_timeout(ledger_hub):
    tasker, _, _, inputs, _ = ledger_hub()
    patch = stamina_override()
    patch[GUARD] = {**FAST, "next": ["ProbeUnavailable"], "timeout": 100, "on_error": ["ProbeOwnedFailure"]}
    patch["ProbeUnavailable"] = {"enabled": False}
    patch["ProbeOwnedFailure"] = {**FAST, "action": "StopTask", "next": [], "on_error": []}
    job = tasker.post_task("领取体力", patch).wait()
    names = node_names(tasker, job)
    assert names[:3] == ["领取体力", ADMIT, GUARD] and names[-1] == "ProbeOwnedFailure"
    assert not inputs and FAIL not in names and MARK not in names


def test_purchase_cases_read_and_write_independent_keys_without_a_purchase(ledger_hub):
    tasker, _, ledger, inputs, _ = ledger_hub()
    task = json.loads((ROOT / "tasks/购买物品.json").read_text(encoding="utf-8"))
    for number, case in enumerate(task["option"]["选择物品"]["cases"], 1):
        patch = case.get("pipeline_override", {})
        name = case["name"]
        suffix = "逆元碎片" if number == 1 else "宿舍"
        guard, mark = f"日常断点_购买物品_{suffix}", f"日常完成_购买物品_{suffix}"
        patch.setdefault(guard, {}).update({**FAST, "next": [mark]})
        patch.setdefault(mark, {}).update({**FAST, "next": []})
        job = tasker.post_task("购买物品", patch).wait()
        assert job.succeeded and node_names(tasker, job) == ["购买物品", ADMIT, guard, mark]
        assert dp.is_done("购买物品/" + name, BEFORE, path=ledger)
        assert len(dp.read_ledger(ledger)["entries"]) == number
        skipped = tasker.post_task("购买物品", patch).wait()
        assert skipped.succeeded and node_names(tasker, skipped)[-1] == "空任务"
        assert not inputs
