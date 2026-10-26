"""Single-thread, bounded input ownership for the opt-in combat path.

Polling bounds Python waits, not native calls or synchronous recognition. A
timed-out native job cannot be cancelled here. ``stop_unconfirmed`` therefore
survives cleanup until the outer host establishes that task/controller work has
finished; a fresh scope is not permission to reuse that controller.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

from action.combat.capture import capture_frame


class CombatInputError(RuntimeError):
    """Input or its completion could not be established safely."""


class CombatInputTimeout(CombatInputError):
    """A native job did not finish within the local wait budget."""


_PRIMARY_NODES = frozenset(
    ("攻击", "按下攻击", "技能", "长按1号球", *(f"消球{i}" for i in range(1, 9)))
)


def primary_input_position(context: Any, node_name: str) -> tuple[int, int]:
    """Resolve one fixed touch target from the current calibrated Context.

    Only the single-contact primary actions are supported. Dynamic recognition
    targets, keyboard nodes and size-changing layout offsets need their own
    validation and are deliberately rejected. Apply target_offset exactly once.
    """
    if node_name not in _PRIMARY_NODES:
        raise CombatInputError(f"未验证的主输入节点: {node_name}")
    node = context.get_node_data(node_name)
    action = node.get("action") if isinstance(node, dict) else None
    if not isinstance(action, dict) or action.get("type") not in {
        "Click", "LongPress", "TouchDown"
    }:
        raise CombatInputError(f"节点不是可用的触屏主动作: {node_name}")
    param = action.get("param")
    if not isinstance(param, dict):
        raise CombatInputError("主输入节点缺少参数")
    contact = param.get("contact", 0)
    if type(contact) is not int or contact != 0:
        raise CombatInputError("专项持有仅支持已验证的 contact 0")
    target = param.get("target")
    offset = param.get("target_offset", [0, 0, 0, 0])
    if (
        not isinstance(target, list)
        or len(target) not in (2, 4)
        or any(type(v) is not int for v in target)
        or not isinstance(offset, list)
        or len(offset) != 4
        or any(type(v) is not int for v in offset)
        or offset[2:] != [0, 0]
    ):
        raise CombatInputError("主输入坐标或布局偏移未解析/无效")
    x, y = target[:2]
    if len(target) == 4:
        width, height = target[2:]
        if width < 0 or height < 0:
            raise CombatInputError("主输入目标矩形无效")
        x += width // 2
        y += height // 2
    x += offset[0]
    y += offset[1]
    if x < 0 or y < 0:
        raise CombatInputError("主输入坐标超出有效布局")
    return x, y


class CombatInputScope:
    """One session's screenshot jobs and one owned primary touch contact."""

    def __init__(
        self,
        context: Any,
        combat: Any = None,
        *,
        generation: int,
        capture_timeout: float = 1.0,
        input_timeout: float = 0.5,
        release_timeout: float = 0.5,
        poll_interval: float = 0.02,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        for value in (capture_timeout, input_timeout, release_timeout, poll_interval):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("输入/截图等待预算必须为有限正秒数")
        self.context = context
        self.combat = combat
        self.generation = generation
        self.capture_timeout = capture_timeout
        self.input_timeout = input_timeout
        self.release_timeout = release_timeout
        self.poll_interval = poll_interval
        self.monotonic = monotonic
        self.sleep = sleep
        self._thread_id = threading.get_ident()
        self.closed = False
        self.blocked = False
        self.release_pending = False
        self.stop_unconfirmed = False
        self.failure_reason = ""
        self.owned_contact: int | None = None
        self.pending_capture: Any = None
        self.capture_in_flight = False
        self.frame_metadata: Any = None
        self.last_frame_metadata: Any = None
        self.capture_sequence = 0
        self._down_job: Any = None
        self._down_post_uncertain = False
        self._release_job: Any = None
        self._release_after_down_terminal = False
        self._stop_requested = False
        self.stop_job: Any = None
        self._controller: Any = None

    def controller_for_jobs(self) -> Any:
        """Keep one remote Controller while any owned job can still be queried.

        MaaFramework 5.14.2 replaces its C++ RemoteController on every
        Tasker.controller getter. A Python Job retains its Python wrapper, but
        cannot keep that replaced native object alive. Down, captures and Up
        must therefore share one getter result until cleanup is confirmed.
        """
        self._assert_owner()
        if self._controller is None:
            self._controller = self.context.tasker.controller
        return self._controller

    def discard_idle_controller(self) -> None:
        """Allow a new operation to acquire a fresh remote handle.

        Other combat actions may access Tasker.controller between operations.
        Never reuse the previous handle after all of our jobs are terminal.
        Pending capture/release ownership keeps the original handle instead.
        """
        # This only drops our Python reference; an already replaced scope can
        # discard an idle reference without touching the new session/native.
        if (
            self.owned_contact is None
            and not self.release_pending
            and not self.capture_in_flight
        ):
            self._controller = None

    def _is_current(self) -> bool:
        return self.combat is None or getattr(self.combat, "input_scope", None) is self

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._thread_id:
            raise CombatInputError("输入作用域只能由创建线程消费")
        if not self._is_current():
            raise CombatInputError("输入作用域已被其他会话替换")

    def invalidate_frame(self) -> None:
        self.frame_metadata = None
        if self.combat is not None and self._is_current():
            self.combat.frame = None

    def request_stop(self, reason: str) -> None:
        """Request asynchronously; never wait behind this Custom callback."""
        self._assert_owner()
        self.blocked = True
        self.failure_reason = self.failure_reason or reason
        self.stop_unconfirmed = True
        self.invalidate_frame()
        if self._stop_requested:
            return
        self._stop_requested = True
        if self.context.tasker.stopping:
            return
        try:
            self.stop_job = self.context.tasker.post_stop()
        except Exception:
            # A failed request is still not proof that controller work stopped.
            self.stop_job = None

    def fail(self, reason: str) -> None:
        self.blocked = True
        self.failure_reason = self.failure_reason or reason
        self.invalidate_frame()
        if self._is_current():
            self.request_stop(reason)
        else:
            self.stop_unconfirmed = True

    def assert_active(self) -> None:
        self._assert_owner()
        if self.closed or self.blocked:
            raise CombatInputError(self.failure_reason or "输入作用域已关闭")
        if self.context.tasker.stopping:
            self.request_stop("stopped")
            raise CombatInputError("战斗已请求停止")

    def wait_job(self, job: Any, deadline: float, *, releasing: bool = False) -> None:
        """Poll native status without invoking its unbounded wait method."""
        while True:
            self._assert_owner()
            if not releasing:
                self.assert_active()
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise CombatInputTimeout("控制器 Job 等待期限已到")
            if job.done:
                if not job.succeeded:
                    raise CombatInputError("控制器 Job 未成功完成")
                # Native status queries are synchronous and may themselves
                # cross the deadline; success is not permission to run late.
                if self.monotonic() >= deadline:
                    raise CombatInputTimeout("控制器 Job 状态返回时已超过期限")
                return
            self.sleep(min(self.poll_interval, remaining))

    def capture_frame(self, *, deadline: float | None = None):
        return capture_frame(self.context, self.combat, scope=self, deadline=deadline)

    def _release(self) -> bool:
        """Release only our contact, retaining every unconfirmed outcome."""
        if self.owned_contact is None:
            return not self.release_pending
        try:
            self._assert_owner()
            self.invalidate_frame()
            deadline = self.monotonic() + self.release_timeout
            if (
                self._release_job is not None
                and self._release_job.done
                and not self._release_job.succeeded
            ):
                # Retry terminal failure on this cleanup attempt. A pending
                # or running Up must retain its existing job ownership.
                self._release_job = None
            if self._release_job is None:
                self._release_after_down_terminal = (
                    not self._down_post_uncertain
                    and self._down_job is not None
                    and self._down_job.done
                )
                self._release_job = self.controller_for_jobs().post_touch_up(
                    contact=self.owned_contact
                )
            self.wait_job(self._release_job, deadline, releasing=True)
            if not self._release_after_down_terminal:
                # An Up before a possible late Down is not a release receipt.
                # Cleanup must post a new Up instead of reusing this one.
                self._release_job = None
                raise CombatInputError("按下仍可能迟到，抬起不能确认最终触点状态")
        except Exception as error:
            self.release_pending = True
            self.fail(f"release_unconfirmed: {error}")
            return False
        self.owned_contact = None
        self.release_pending = False
        self._down_job = None
        self._release_job = None
        self._release_after_down_terminal = False
        self.discard_idle_controller()
        return True

    def tap_semantic(
        self,
        node_name: str,
        *,
        start_deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None,
        position_validator: Callable[[tuple[int, int]], bool] | None = None,
    ) -> bool:
        """Post one Down and immediately release its successful terminal Job.

        No active delay, capture or observer runs while the contact is held.
        Native calls still have no physical duration guarantee; late Down/Up
        results retain the same blocked ownership as ``hold_semantic``.
        ``start_deadline`` bounds the host's Down request after all synchronous
        prechecks; it cannot bound when a native physical touch is delivered.
        """
        def require_idle():
            self.assert_active()
            if (
                self.owned_contact is not None or self.release_pending
                or self.capture_in_flight or self.pending_capture is not None
                or self._down_post_uncertain or self._release_job is not None
            ):
                raise CombatInputError("当前主触点或截图已被占用")

        require_idle()
        if start_deadline is not None:
            if type(start_deadline) not in (int, float):
                raise ValueError("点按开始期限必须为有限数值")
            try:
                finite_deadline = math.isfinite(start_deadline)
            except OverflowError:
                finite_deadline = False
            if not finite_deadline:
                raise ValueError("点按开始期限必须为有限数值")
        try:
            x, y = primary_input_position(self.context, node_name)
            controller = self.controller_for_jobs()
            if position_validator is not None and position_validator((x, y)) is not True:
                raise CombatInputError("主输入坐标校验未确认")
            if should_stop is not None and should_stop():
                self.request_stop("observation_stop")
                raise CombatInputError("共享观察要求停止")
            # Resolution, controller acquisition and the guard are synchronous.
            # Nothing may have claimed this contact or stopped us in those calls.
            require_idle()
            # This comparison must remain after the last SDK status query.
            # Repeating the observer here would add another unbounded call.
            if start_deadline is not None and self.monotonic() >= start_deadline:
                raise CombatInputTimeout("点按开始期限已到")
        except BaseException:
            # A rejected start owns no Job. A later external controller getter
            # may replace this idle native handle before another attempt.
            self.discard_idle_controller()
            raise
        self.invalidate_frame()
        self.owned_contact = 0
        self.release_pending = True
        self._down_post_uncertain = True
        try:
            self._down_job = controller.post_touch_down(x, y, contact=0, pressure=1)
            self._down_post_uncertain = False
            self.wait_job(self._down_job, self.monotonic() + self.input_timeout)
            # A synchronous status query can itself observe/trigger Stop.
            self.assert_active()
        except BaseException:
            self.fail("主输入点按未完成")
            raise
        finally:
            released = self._release()
        if not released:
            raise CombatInputError(self.failure_reason or "触点释放未确认")
        self.assert_active()
        return True

    def hold_semantic(
        self,
        node_name: str,
        *,
        duration: float,
        observe: Callable[[Any], bool | None],
        should_stop: Callable[[], bool] | None = None,
        position_validator: Callable[[tuple[int, int]], bool] | None = None,
    ) -> bool:
        """Hold a main input until observe returns True or duration expires.

        ``observe`` must only observe the provided new frame. False/None keep
        holding within the same stage budget; timeout returns False after Up.
        Input/capture/stop failures raise and block all subsequent scope input.
        Optional ``position_validator`` receives the immutable resolved point
        consumed by this Down. Only literal True permits input; rejection or
        an exception precedes contact registration and posts no Down/Up/Stop.
        """
        self.assert_active()
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("持有预算必须为有限正秒数")
        if self.owned_contact is not None or self.capture_in_flight:
            raise CombatInputError("当前主触点或截图已被占用")
        x, y = primary_input_position(self.context, node_name)
        if position_validator is not None and position_validator((x, y)) is not True:
            raise CombatInputError("主输入坐标校验未确认")
        if should_stop is not None and should_stop():
            self.request_stop("observation_stop")
            raise CombatInputError("共享观察要求停止")
        # Context resolution and optional callbacks can synchronously stop or
        # invalidate this scope even when should_stop is absent or returns False.
        self.assert_active()
        deadline = self.monotonic() + duration
        self.invalidate_frame()
        # Register before posting: even a post exception can leave a late Down.
        self.owned_contact = 0
        self.release_pending = True
        self._down_post_uncertain = True
        completed = False
        try:
            self._down_job = self.controller_for_jobs().post_touch_down(
                x, y, contact=0, pressure=1
            )
            self._down_post_uncertain = False
            self.wait_job(
                self._down_job, min(deadline, self.monotonic() + self.input_timeout)
            )
            while self.monotonic() < deadline:
                self.assert_active()
                if should_stop is not None and should_stop():
                    self.request_stop("observation_stop")
                    raise CombatInputError("共享观察要求停止")
                image = self.capture_frame(deadline=deadline)
                completed = observe(image) is True
                self.assert_active()
                if self.monotonic() >= deadline:
                    raise CombatInputTimeout("观察返回时已超过持有期限")
                if should_stop is not None and should_stop():
                    self.request_stop("observation_stop")
                    raise CombatInputError("共享观察要求停止")
                if completed:
                    break
                remaining = deadline - self.monotonic()
                if remaining > 0:
                    self.sleep(min(self.poll_interval, remaining))
        except BaseException:
            self.fail("主输入持有未完成")
            raise
        finally:
            released = self._release()
        if not released:
            raise CombatInputError(self.failure_reason or "触点释放未确认")
        return completed

    def close(self) -> bool:
        """Idempotent bounded cleanup; it does not certify host/native stop."""
        self._assert_owner()
        self.closed = True
        self.invalidate_frame()
        released = self._release()
        if self.capture_in_flight:
            self.fail("会话关闭时截图作业仍未确认")
        return released and not self.stop_unconfirmed
