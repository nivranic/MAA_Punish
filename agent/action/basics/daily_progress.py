"""Task-local daily admission and completion accounting (local 05:00 reset)."""

from __future__ import annotations

import datetime
import json
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from action.basics import role_cache_policy as cache_policy
from logger_component import LoggerComponent

LEDGER_NAME = "daily_progress.json"
ADMIT_NODE = "日常账本准入"
REJECT_NODE = "日常账本准入_拒绝"
GUARD_POINTER = "daily_progress_guard_node"
TICKET = "daily_progress_ticket"
_WRITE_LOCK = Lock()

logger_component = LoggerComponent(__name__)
logger = logger_component.logger


class LedgerError(RuntimeError):
    """A safe reason code, never a path or raw ledger content."""


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def game_day(
    now: datetime.datetime | None = None,
    refresh_hour: int = cache_policy.DEFAULT_REFRESH_HOUR,
) -> str:
    """The interval before the refresh hour belongs to the previous day."""
    shifted = (now if now is not None else _now()) - datetime.timedelta(hours=max(0, refresh_hour))
    return shifted.date().isoformat()


def ledger_path() -> Path:
    # Same deployed root as role_cache_policy.cache_path.
    return Path(__file__).resolve().parents[3] / LEDGER_NAME


def _empty() -> dict:
    return {"version": 1, "entries": {}}


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    data = {}
    for key, value in pairs:
        if key in data:
            raise LedgerError("ledger_duplicate_field")
        data[key] = value
    return data


def _invalid_constant(_constant: str) -> None:
    raise LedgerError("ledger_bad_number")


def _require_serializable_values(data: dict) -> None:
    # JSON decoding can accept exponent overflow and escaped lone surrogates.
    # Reject them during admission, not when re-encoding after business input.
    pending: list[Any] = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise LedgerError("ledger_bad_number")
        elif isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise LedgerError("ledger_bad_encoding") from exc


def read_ledger(path: Path | None = None) -> dict | None:
    """Return valid v1 data, None for missing, or raise if unsafe to use."""
    try:
        with (path or ledger_path()).open("r", encoding="utf-8") as stream:
            data = json.load(stream, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LedgerError("ledger_unreadable") from exc
    except UnicodeDecodeError as exc:
        raise LedgerError("ledger_bad_encoding") from exc
    except (ValueError, RecursionError) as exc:
        raise LedgerError("ledger_bad_json") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise LedgerError("ledger_unsupported_format")
    entries = data.get("entries")
    if not isinstance(entries, dict) or any(
        not key.strip() or not isinstance(entry, dict) or entry.get("result") != "done"
        or not _valid_date(entry.get("game_day"))
        for key, entry in entries.items()
    ):
        raise LedgerError("ledger_unsupported_entries")
    _require_serializable_values(data)
    return data


def write_ledger(
    data: dict,
    path: Path | None = None,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    target = path or ledger_path()
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("[DailyProgress] temporary_cleanup_failed")


def _is_done(data: dict | None, key: str, day: str) -> bool:
    return data is not None and data["entries"].get(key, {}).get("game_day") == day


def is_done(
    key: str,
    now: datetime.datetime | None = None,
    refresh_hour: int = cache_policy.DEFAULT_REFRESH_HOUR,
    path: Path | None = None,
) -> bool:
    return _is_done(read_ledger(path), key, game_day(now, refresh_hour))


def mark_done(
    key: str,
    now: datetime.datetime | None = None,
    refresh_hour: int = cache_policy.DEFAULT_REFRESH_HOUR,
    path: Path | None = None,
    *,
    before_replace: Callable[[], None] | None = None,
) -> dict:
    now = now if now is not None else _now()
    entry = {"game_day": game_day(now, refresh_hour), "result": "done", "updated_at": now.isoformat(timespec="seconds")}
    with _WRITE_LOCK:
        data = read_ledger(path) or _empty()
        previous = data["entries"].get(key)
        if previous is not None and previous["game_day"] > entry["game_day"]:
            raise LedgerError("ledger_has_newer_day")
        data["entries"][key] = entry
        write_ledger(data, path, before_replace=before_replace)
    return entry


def reset_entries(
    path: Path | None = None, *, before_replace: Callable[[], None] | None = None,
) -> int:
    with _WRITE_LOCK:
        data = read_ledger(path) or _empty()
        count = len(data["entries"])
        data["entries"] = {}
        write_ledger(data, path, before_replace=before_replace)
    return count


def _parse_params(raw_params: Any) -> dict:
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str):
        try:
            parsed = json.loads(raw_params)
        except (ValueError, RecursionError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def resolve_key(node_name: str, attach: dict, params: dict) -> str:
    """attach.daily_progress_key > custom param key > the node name."""
    for source in (attach.get("daily_progress_key"), params.get("key")):
        if isinstance(source, str) and source.strip():
            return source.strip()
    return node_name


def _attach(node: dict) -> dict:
    attach = node.get("attach")
    return attach if isinstance(attach, dict) else {}


def _key_from(context, node_name: str, raw_params: Any) -> str:
    return resolve_key(node_name, _attach(context.get_node_data(node_name) or {}), _parse_params(raw_params))


def _ticket(context):
    return _attach(context.get_node_data(ADMIT_NODE) or {}).get(TICKET)


def _task_binding(argv, key: str | None = None) -> dict:
    detail = getattr(argv, "task_detail", None)
    task_id = getattr(detail, "task_id", None)
    entry = getattr(detail, "entry", None)
    if type(task_id) is not int or task_id <= 0 or not isinstance(entry, str) or not entry:
        raise LedgerError("task_binding_missing")
    binding = {"task_id": task_id, "entry": entry}
    if key is not None:
        binding["key"] = key
    return binding


def _require_admission(context, binding: dict) -> dict:
    admission = _ticket(context)
    if not isinstance(admission, dict) or any(admission.get(k) != v for k, v in binding.items()):
        raise LedgerError("admission_missing_or_mismatched")
    return admission


def _require_ticket(context, argv, key: str, day: str) -> None:
    admission = _require_admission(context, _task_binding(argv, key))
    if admission.get("admitted_game_day") != day:
        raise LedgerError("game_day_changed")


def require_current_purchase_day(context, argv) -> None:
    """The ordinary daily entry cannot use a previous day's admission to click."""
    binding = _task_binding(argv)
    if binding["entry"] != "购买物品":
        # This time check is not a new admission rule for direct node tools.
        return
    admission = _require_admission(context, binding)
    admitted_day = admission.get("admitted_game_day")
    if not _valid_date(admitted_day):
        raise LedgerError("admission_missing_or_mismatched")
    _require_running(context)
    _require_game_day(admitted_day)


def _stopping(context) -> bool:
    return bool(context.tasker.stopping)


def _require_running(context) -> None:
    if _stopping(context):
        raise LedgerError("stopped")


def _require_game_day(day: str) -> None:
    if game_day() != day:
        raise LedgerError("game_day_changed")


def _route(context, candidates: list[str]) -> None:
    _require_running(context)
    if not context.override_next(ADMIT_NODE, candidates):
        raise LedgerError("admission_route_rejected")
    current = (context.get_node_data(ADMIT_NODE) or {}).get("next")
    if current != candidates and not (
        isinstance(current, list) and len(current) == len(candidates)
        and all(isinstance(item, dict) and item.get("name") == name
                and not item.get("jump_back") and not item.get("anchor")
                for item, name in zip(current, candidates))
    ):
        raise LedgerError("admission_route_unconfirmed")


def _reason(error: Exception) -> str:
    return str(error) if isinstance(error, LedgerError) else type(error).__name__


class DailyProgressAdmit(CustomAction):
    """Run, skip (positive same-day done), or fail without recovery input."""

    def run(self, context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            if argv.node_name == REJECT_NODE:
                # A guard miss must be a named action failure, not an anonymous timeout.
                raise LedgerError("work_guard_rejected")
            binding = _task_binding(argv, "")
            entry = context.get_node_data(binding["entry"]) or {}
            name = _attach(entry).get(GUARD_POINTER)
            if argv.node_name != ADMIT_NODE or not isinstance(name, str) or not name.startswith("日常断点_"):
                raise LedgerError("guard_pointer_invalid")
            guard = context.get_node_data(name) or {}
            param = guard.get("recognition", {}).get("param", {})
            if param.get("custom_recognition") != "DailyProgressCheck":
                raise LedgerError("guard_recognition_invalid")
            key = resolve_key(name, _attach(guard), _parse_params(param.get("custom_recognition_param")))
            binding["key"] = key
            data = read_ledger()
            day = game_day()
            if _ticket(context) is not None:
                # In-task navigation can return to the root; never refresh that task's day.
                _require_ticket(context, argv, key, day)
            already_done = _is_done(data, key, day)
            if already_done:
                _route(context, ["空任务"])
            else:
                ticket = {**binding, "admitted_game_day": day}
                _require_running(context)
                if not context.override_pipeline({ADMIT_NODE: {"attach": {TICKET: ticket}}}):
                    raise LedgerError("admission_write_rejected")
                _require_ticket(context, argv, key, day)
                _route(context, [name, REJECT_NODE])
            _require_running(context)
            # A positive skip from the previous day is not valid if routing crossed 05:00.
            _require_game_day(day)
            if already_done:
                logger.info("[DailyProgress] skip %s：本游戏日已完成", key)
            else:
                logger.info("[DailyProgress] run %s：本游戏日未完成", key)
            return self.RunResult(success=True)
        except Exception as error:
            logger.warning("[DailyProgress] admission_blocked reason=%s", _reason(error))
            return self.RunResult(success=False)


class DailyProgressCheck(CustomRecognition):
    """Validate admission before the existing work chain; a miss cannot skip."""

    def analyze(self, context, argv: CustomRecognition.AnalyzeArg) -> CustomRecognition.AnalyzeResult | None:
        try:
            _require_running(context)
            day = game_day()
            key = _key_from(context, argv.node_name, argv.custom_recognition_param)
            _require_ticket(context, argv, key, day)
            _require_running(context)
            _require_game_day(day)
        except Exception as error:
            logger.warning("[DailyProgress] guard_blocked reason=%s", _reason(error))
            return None
        return self.AnalyzeResult(box=(0, 0, 0, 0), detail={"status": "pending", "key": key})


class DailyProgressMark(CustomAction):
    """Write only a confirmed business tail in the same task and game day."""

    def run(self, context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            _require_running(context)
            now = _now()
            day = game_day(now)
            key = _key_from(context, argv.node_name, argv.custom_action_param)
            _require_ticket(context, argv, key, day)

            def before_replace() -> None:
                _require_running(context)
                _require_game_day(day)

            entry = mark_done(key, now=now, before_replace=before_replace)
            logger.info("[DailyProgress] mark %s -> %s", key, entry["game_day"])
        except Exception as error:
            # A failed write is not a reason to retry or to enter the default error chain.
            logger.warning("[DailyProgress] mark_omitted reason=%s", _reason(error))
        return self.RunResult(success=True)


class DailyProgressReset(CustomAction):
    """Explicit maintenance on readable ledger data only."""

    def run(self, context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            count = reset_entries(before_replace=lambda: _require_running(context))
            logger.info("[DailyProgress] reset：已清空 %s 条断点记录", count)
            return self.RunResult(success=True)
        except Exception as error:
            logger.warning("[DailyProgress] reset_blocked reason=%s", _reason(error))
            return self.RunResult(success=False)
