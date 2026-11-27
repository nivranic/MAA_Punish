"""Pipeline 兑换码程序（原作者 overflow65537）。"""

import json
import os
from pathlib import Path
import tempfile
from threading import Lock
import time

from maa.context import Context
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from logger_component import LoggerComponent


CODE_NODE = "可以输入兑换码"
CHECK_NODE = "检查是否需要运行"
CONFIRM_NODE = "确认兑换"
HISTORY_PATH = Path("inv_code.json")
_HISTORY_LOCK = Lock()

logger_component = LoggerComponent(__name__)
logger = logger_component.logger


def _code_list(value) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(code, str) or not code.strip() for code in value):
        raise ValueError("bad redemption code list")
    return value


def _history_object(pairs) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate redemption history field")
    return result


def _load_history() -> dict:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"), object_pairs_hook=_history_object)
    except FileNotFoundError:
        data = {}
    if data is None:
        data = {}  # 兼容旧版空记录。
    if not isinstance(data, dict):
        raise ValueError("bad redemption history")
    # 旧 inv_code 只用于防重；它本身也不是游戏结果证明。
    data["inv_code"] = _code_list(data.get("inv_code", []))
    data["unconfirmed"] = _code_list(data.get("unconfirmed", []))
    return data


def _held_codes(history: dict) -> set[str]:
    return set(history["inv_code"]) | set(history["unconfirmed"])


def _remember_unconfirmed(code: str) -> bool:
    """Store the possible submission before input. Never call it a redemption."""
    with _HISTORY_LOCK:
        history = _load_history()
        if code in _held_codes(history):
            return False
        history["unconfirmed"].append(code)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=HISTORY_PATH.parent,
                prefix=".inv_code.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(history, stream, ensure_ascii=False, indent=4)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, HISTORY_PATH)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True


def load_codes(context: Context) -> list[str]:
    pipeline_obj = context.get_node_data(CODE_NODE)
    if pipeline_obj is None:
        raise ValueError("redemption node missing")
    return _code_list(
        pipeline_obj.get("action", {})
        .get("param", {})
        .get("custom_action_param", {})
        .get("code", [])
    )


def _focus_input(context: Context) -> bool:
    name = "点击兑换码"
    task = context.run_task(name, {name: {"next": [], "on_error": []}})
    if task is None:
        return False
    for node_id in task.node_id_list:
        node = context.tasker.get_node_detail(node_id)
        if node is not None and node.name == name and node.completed:
            return True
    return False


class RedeemCode(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            held = _held_codes(_load_history())
            code = next((code for code in load_codes(context) if code not in held), None)
        except (OSError, ValueError, AttributeError, RecursionError):
            return self._fail(context, "兑换码配置或本地记录不可读，未提交")
        if code is None:
            # 识别与动作之间如果记录变化，不在原页面继续尝试其他操作。
            return self._fail(context, "没有待提交的兑换码")

        if context.tasker.stopping or not _focus_input(context):
            return self._fail(context, "兑换码输入框未确认，未提交")
        if context.tasker.stopping or not context.tasker.controller.post_input_text(code).wait().succeeded:
            return self._fail(context, "兑换码文字未确认输入，未提交")

        # 保留原有输入后稳定等待，但不把 ACK 或兑换说明当成确认按钮。
        time.sleep(0.1)
        capture = context.tasker.controller.post_screencap().wait()
        image = capture.get() if capture.succeeded else None
        button = context.run_recognition(CONFIRM_NODE, image) if image is not None else None
        if button is None or not button.hit or button.box is None or context.tasker.stopping:
            return self._fail(context, "未确认兑换按钮，未提交")

        try:
            if not _remember_unconfirmed(code):
                return self._fail(context, "已有兑换码记录，未重复提交")
        except (OSError, ValueError, RecursionError):
            return self._fail(context, "兑换码结果记录保存失败，未提交")
        if context.tasker.stopping:
            return self._fail(context, "任务已停止，保留结果未确认记录，不自动重试")

        # ACK / NACK 均未证明兑换成功、已兑换或失效。此处不继续提交其他码。
        result = context.run_action(CONFIRM_NODE, button.box)
        if result is None or not result.success:
            return self._fail(context, "兑换提交无确认回执；结果未确认，不自动重试")
        return self._fail(context, "兑换码已发送；游戏结果未确认，不自动重试")

    def _fail(self, context: Context, msg: str) -> CustomAction.RunResult:
        if not context.tasker.stopping:
            notify = "兑换码本地提示"
            context.run_task(notify, {notify: {
                "focus": {"Node.Recognition.Succeeded": msg}, "next": [], "on_error": [],
            }})
        return CustomAction.RunResult(success=False)


class CheckRedeemCode(CustomRecognition):
    """只路由未提交的码；旧记录和结果未确认记录均不自动重试。"""

    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg) -> CustomRecognition.AnalyzeResult | None:
        try:
            codes = load_codes(context)
            history = _load_history()
        except (OSError, ValueError, AttributeError, RecursionError):
            # 只是跳过入口，不是「没有待提交的码」；把原因写进 agent 日志，避免任务成功却什么都没做。
            logger.warning("[RedeemCode] skip reason=history_unreadable")
            return None
        held = _held_codes(history)
        if any(code not in held for code in codes):
            return CustomRecognition.AnalyzeResult(box=(0, 0, 0, 0), detail={"status": "need_submit"})
        if any(code in history["unconfirmed"] for code in codes):
            logger.warning("[RedeemCode] skip reason=unconfirmed_pending")
        return None
