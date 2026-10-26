"""Bounded navigation after a Pipeline failure, never a business retry.

The callback always fails the original task. Android must separately consume
the task's recovery-node history before posting another task; post_stop alone
does not provide that round-level guarantee in MaaFramework 5.14.2.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from maa.custom_action import CustomAction
from maa.define import Rect
from maa.pipeline import JActionType, JClick

from action.combat.input_control import CombatInputError, CombatInputScope
from logger_component import LoggerComponent


RECOVERY_NODE = "返回主菜单_err"
HOME_NODE = "检查主界面2"
GUILD_NODE = "检查是否在指挥局内部"
BACK_NODE = "返回_图标"
BACK_ROI = (27, 10, 51, 53)
BACK_TEMPLATE = "指挥局/返回.png"
TOTAL_SECONDS = 6.0
FRAME_SECONDS = 1.0
MAX_RETURNS = 1
MAX_FRAMES = 4
MAX_HISTORY = 512

_ROOMS = ("蔚蓝之夏", "岁雪新宵", "庭梨栖雪", "漫花清夏", "鲸律星屿", "云海空岛", "经典舱室")
_D03_NAVIGATION = {"指挥局": "DoNothing", GUILD_NODE: "DoNothing", "打开指挥局": "Click",
                   "切换主界面状态": "Click", "意外点击_主界面": "Click", "识别文字颜色": "DoNothing",
                   **{"移动_" + name: "Swipe" for name in _ROOMS},
                   **{"识别_" + name: "DoNothing" for name in _ROOMS}}
_D03_BUSINESS = frozenset({"检查指定按钮", "关闭奖励_指挥局", "返回", BACK_NODE})


class _ControllerLease:
    """A second Python controller getter invalidates remote native jobs."""

    def __init__(self, tasker):
        self._tasker = tasker
        self._controller = None
        self.acquisitions = 0

    @property
    def controller(self):
        if self._controller is None:
            self._controller = self._tasker.controller
            self.acquisitions += 1
        return self._controller

    @property
    def stopping(self):
        return self._tasker.stopping

    def post_stop(self):
        return self._tasker.post_stop()


class ScopedRecovery(CustomAction):
    def __init__(self):
        super().__init__()
        self._logger_component = LoggerComponent(__name__)
        self._retained = None
        self.last_result = None
        self._monotonic = time.monotonic
        self._sleep = time.sleep

    def _task(self, context):
        # get(wait=False) reads the executing task; waiting on it here deadlocks.
        detail = context.get_task_job().get(wait=False)
        if (detail is None or type(detail.task_id) is not int or detail.task_id <= 0
                or type(detail.entry) is not str or not detail.entry
                or getattr(detail.status, "running", False) is not True):
            raise CombatInputError("task_identity_unknown")
        return detail

    @staticmethod
    def _hit(result):
        if result is None or type(result.hit) is not bool:
            raise CombatInputError("recognition_result_unknown")
        return result.hit

    def _before_business(self, context, detail, active):
        ids = detail.node_id_list
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_HISTORY
                or any(type(value) is not int or value <= 0 for value in ids)
                or len(set(ids)) != len(ids)):
            raise CombatInputError("task_history_unknown")
        names = []
        for index, node_id in enumerate(ids):
            active()
            node = context.tasker.get_node_detail(node_id)
            active()
            if node is None or type(node.name) is not str:
                raise CombatInputError("task_history_incomplete")
            # Actual 5.14.2 Next-timeout callback exposes a final unnamed,
            # incomplete placeholder for the currently executing recovery.
            if node.name == "" and index == len(ids) - 1 and node.completed is False:
                if node.action is not None or node.recognition is not None:
                    raise CombatInputError("task_history_placeholder_unknown")
                continue
            if node.name == RECOVERY_NODE and index == len(ids) - 1:
                if node.completed is not False:
                    raise CombatInputError("repeated_recovery")
                continue
            if node.name in _D03_BUSINESS:
                raise CombatInputError("d03_business_already_attempted")
            if (node.name not in _D03_NAVIGATION or node.completed is not True
                    or node.action is None or node.action.success is not True
                    or node.action.action != _D03_NAVIGATION[node.name]):
                raise CombatInputError("d03_history_not_safe_navigation")
            names.append(node.name)
        if not names or names[0] != "指挥局":
            raise CombatInputError("d03_entry_history_missing")
        return names

    @staticmethod
    def _back_box(context, result, image):
        node = context.get_node_data(BACK_NODE)
        if (not isinstance(node, dict) or node.get("recognition", {}).get("type") != "TemplateMatch"
                or node.get("action", {}).get("type") != "Click"):
            raise CombatInputError("back_node_contract_changed")
        template = node.get("recognition", {}).get("param", {}).get("template")
        # Maa 5.14.2 normalizes the source string into a one-element list.
        templates = [template] if type(template) is str else template
        if type(templates) is not list or templates != [BACK_TEMPLATE]:
            raise CombatInputError("back_template_changed")
        roi = node.get("recognition", {}).get("param", {}).get("roi")
        if (not isinstance(roi, list) or len(roi) != 4
                or any(type(value) is not int for value in roi)
                or tuple(roi) != BACK_ROI):
            raise CombatInputError("back_roi_unknown")
        best = result.best_result
        raw = None if best is None else best.box
        if not isinstance(raw, (Rect, list, tuple)):
            raise CombatInputError("back_box_unknown")
        box = tuple(raw)
        if len(box) != 4 or any(type(value) is not int for value in box):
            raise CombatInputError("back_box_unknown")
        x, y, width, height = box
        rx, ry, rw, rh = roi
        if (width <= 0 or height <= 0 or x < 0 or y < 0
                or x + width > image.shape[1] or y + height > image.shape[0]
                or rw <= 0 or rh <= 0 or x < rx or y < ry
                or x + width > rx + rw or y + height > ry + rh):
            raise CombatInputError("back_box_outside_verified_roi")
        return box

    def run(self, context, argv):
        started = self._monotonic()
        deadline = started + TOTAL_SECONDS
        report = {"event": "scoped_recovery", "business_success": False,
                  "outcome": "stopped_unknown", "returns": 0, "entry": "unknown",
                  "stop_requested": False, "native_hard_cancel": False,
                  "original_failure_cleanup_verified": False}
        lease = _ControllerLease(context.tasker)
        scope_context = SimpleNamespace(tasker=lease)
        scope = CombatInputScope(scope_context, generation=id(context), capture_timeout=1.0,
                                 monotonic=self._monotonic, sleep=self._sleep)
        action_unconfirmed = False

        def active():
            scope.assert_active()
            if self._monotonic() >= deadline:
                raise CombatInputError("recovery_budget_exhausted")

        def fresh(metadata):
            # Pure local check after the last synchronous SDK read; native
            # dispatch itself and OS scheduling cannot be made atomic here.
            now = self._monotonic()
            if (now >= deadline or scope.frame_metadata is not metadata
                    or not 0 <= now - metadata.captured_at <= FRAME_SECONDS):
                raise CombatInputError("recovery_frame_expired")

        def recognize(name, image):
            active()
            result = context.run_recognition(name, image)
            active()
            return result

        def observe_delay():
            until = min(deadline, self._monotonic() + 0.1)
            while self._monotonic() < until:
                active()
                self._sleep(min(0.02, until - self._monotonic()))

        try:
            if self._retained is not None:
                raise CombatInputError("prior_recovery_cleanup_unconfirmed")
            node = context.get_node_data(argv.node_name)
            if (argv.node_name != RECOVERY_NODE or not isinstance(node, dict)
                    or node.get("action", {}).get("type") != "Custom"
                    or node.get("action", {}).get("param", {}).get("custom_action") != "ScopedRecovery"
                    or node.get("next") != [] or node.get("on_error") != []):
                raise CombatInputError("recovery_entry_contract_changed")
            detail = self._task(context)
            report.update(entry=detail.entry, task_id=detail.task_id)
            active()
            for index in range(MAX_FRAMES):
                image = scope.capture_frame(deadline=deadline)
                metadata = scope.frame_metadata
                if self._hit(recognize(HOME_NODE, image)):
                    fresh(metadata)
                    report["outcome"] = "home_original_task_failed"
                    break
                if report["returns"] >= MAX_RETURNS:
                    # A successful native click is not evidence that the page
                    # has changed. Observe only; never blindly press Back twice.
                    if index + 1 == MAX_FRAMES:
                        raise CombatInputError("home_after_return_unconfirmed")
                    observe_delay()
                    continue
                if detail.entry != "指挥局":
                    raise CombatInputError("page_or_task_has_no_navigation_policy")
                navigation = self._before_business(context, detail, active)
                if not self._hit(recognize(GUILD_NODE, image)):
                    raise CombatInputError("guild_page_unconfirmed")
                # A reward/result page or a confirmation is not a navigation
                # page, even if a Guild header remains visible behind it.
                if (self._hit(recognize("关闭奖励_指挥局", image))
                        or self._hit(recognize("确定", image))):
                    raise CombatInputError("business_result_or_dialog_visible")
                back = recognize(BACK_NODE, image)
                if not self._hit(back):
                    raise CombatInputError("guild_back_icon_unconfirmed")
                box = self._back_box(context, back, image)
                current = self._task(context)
                if current.task_id != detail.task_id or current.entry != detail.entry:
                    raise CombatInputError("task_binding_changed")
                if (current.node_id_list != detail.node_id_list
                        or self._before_business(context, current, active) != navigation):
                    raise CombatInputError("task_history_changed")
                active()
                fresh(metadata)
                report["returns"] += 1
                action_unconfirmed = True
                # Same native Click as 返回_图标, consuming only its recognized
                # rectangle. RunActionDirect has no next/on_error traversal.
                result = context.run_action_direct(JActionType.Click, JClick(target=box), box=box)
                if result is None or result.success is not True:
                    raise CombatInputError("back_action_unconfirmed")
                action_unconfirmed = False
                active()
                observe_delay()
            else:
                raise CombatInputError("navigation_budget_exhausted")
        except Exception as error:
            report.update(reason=str(error), error_type=type(error).__name__)
            try:
                scope.request_stop(str(error))
            except Exception as stop_error:
                report["stop_request_error"] = type(stop_error).__name__
        finally:
            try:
                confirmed = scope.close()
            except Exception as error:
                confirmed = False
                report["cleanup_error"] = type(error).__name__
            report["stop_requested"] = scope._stop_requested
            report["cleanup"] = {"confirmed": confirmed and not action_unconfirmed,
                "scope": "recovery_owned_jobs_only",
                "pending_capture": scope.pending_capture is not None,
                "capture_in_flight": scope.capture_in_flight,
                "stop_unconfirmed": scope.stop_unconfirmed,
                "action_unconfirmed": action_unconfirmed}
            report["controller_acquisitions"] = lease.acquisitions
            report["elapsed_seconds"] = self._monotonic() - started
            if not report["cleanup"]["confirmed"]:
                # Never replace a controller that an unresolved job can use.
                self._retained = self._retained or (context, scope_context, scope, lease)
            self.last_result = report
            try:
                self._logger_component.logger.warning(json.dumps(report, ensure_ascii=False, sort_keys=True))
            except Exception:
                pass
        return CustomAction.RunResult(success=False)
