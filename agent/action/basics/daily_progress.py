"""
日常断点账本（游戏日 05:00 窗口）

preset_daily 实例级断点续接：
- 入口守卫 DailyProgressCheck：命中 = 本游戏日未完成（继续工作链）；
  未命中 = 已完成（落到 空任务 收尾）。
- 业务成功尾节点 DailyProgressMark：写账。优雅退出/失败链不经过它，天然不写账。
游戏日语义与 role_cache_policy.DEFAULT_REFRESH_HOUR（国服 5 点日切）及
RoleSelection._r3_memo_game_day 对齐：now 减 refresh_hour 后取日期，
00:00—05:00 归前一天。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from action.basics import role_cache_policy as cache_policy
from logger_component import LoggerComponent

LEDGER_NAME = "daily_progress.json"

logger_component = LoggerComponent(__name__)
logger = logger_component.logger


def game_day(
    now: datetime.datetime | None = None,
    refresh_hour: int = cache_policy.DEFAULT_REFRESH_HOUR,
) -> str:
    """按刷新时刻对齐的游戏日；00:00—refresh_hour 归前一天。"""
    now = now or datetime.datetime.now()
    shifted = now - datetime.timedelta(hours=max(0, refresh_hour))
    return shifted.date().isoformat()


def ledger_path() -> Path:
    # 与 role_cache_policy.cache_path 同目录约定（部署源码根）。
    return Path(__file__).resolve().parents[3] / LEDGER_NAME


def _empty() -> dict:
    return {"version": 1, "entries": {}}


def read_ledger(path: Path | None = None) -> dict:
    target = path or ledger_path()
    if not target.exists():
        return _empty()
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    data.setdefault("version", 1)
    return data


def write_ledger(data: dict, path: Path | None = None) -> None:
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def is_done(
    key: str,
    now: datetime.datetime | None = None,
    refresh_hour: int = cache_policy.DEFAULT_REFRESH_HOUR,
    path: Path | None = None,
) -> bool:
    entry = read_ledger(path).get("entries", {}).get(key)
    return isinstance(entry, dict) and entry.get("game_day") == game_day(now, refresh_hour)


def mark_done(
    key: str,
    now: datetime.datetime | None = None,
    refresh_hour: int = cache_policy.DEFAULT_REFRESH_HOUR,
    path: Path | None = None,
) -> dict:
    now = now or datetime.datetime.now()
    data = read_ledger(path)
    entry = {
        "game_day": game_day(now, refresh_hour),
        "result": "done",
        "updated_at": now.isoformat(timespec="seconds"),
    }
    data["entries"][key] = entry
    write_ledger(data, path)
    return entry


def reset_entries(path: Path | None = None) -> int:
    data = read_ledger(path)
    count = len(data.get("entries", {}))
    data["entries"] = {}
    write_ledger(data, path)
    return count


def _parse_params(raw_params: Any) -> dict:
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str):
        try:
            parsed = json.loads(raw_params)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def resolve_key(node_name: str, attach: dict, params: dict) -> str:
    """attach.daily_progress_key > 自定义参数 key > 节点名兜底。

    attach 是 dict merge，任务选项（如 购买物品 三实例）可安全注入不同 key；
    custom_*_param 会整体覆盖，不用于跨选项共享。
    """
    for source in (attach.get("daily_progress_key"), params.get("key")):
        if isinstance(source, str) and source.strip():
            return source.strip()
    return node_name


def _key_from(context, node_name: str, raw_params: Any) -> str:
    node_data = context.get_node_data(node_name) or {}
    attach = node_data.get("attach", {}) or {}
    return resolve_key(node_name, attach, _parse_params(raw_params))


class DailyProgressCheck(CustomRecognition):
    """命中 = 本游戏日未完成（继续工作链）；未命中 = 已完成（落到 空任务 收尾）。"""

    def analyze(
        self,
        context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> CustomRecognition.AnalyzeResult | None:
        key = _key_from(context, argv.node_name, argv.custom_recognition_param)
        if is_done(key):
            logger.info(f"[DailyProgress] skip {key}：本游戏日已完成")
            return None
        logger.info(f"[DailyProgress] run {key}：本游戏日未完成")
        return CustomRecognition.AnalyzeResult(
            box=(0, 0, 0, 0),
            detail={"status": "pending", "key": key},
        )


class DailyProgressMark(CustomAction):
    """业务成功尾节点落账。"""

    def run(
        self,
        context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        key = _key_from(context, argv.node_name, argv.custom_action_param)
        entry = mark_done(key)
        logger.info(f"[DailyProgress] mark {key} -> {entry['game_day']}")
        return CustomAction.RunResult(success=True)


class DailyProgressReset(CustomAction):
    """清空断点账本（tools 任务 重置日常断点）。"""

    def run(
        self,
        context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        count = reset_entries()
        logger.info(f"[DailyProgress] reset：已清空 {count} 条断点记录")
        return CustomAction.RunResult(success=True)
