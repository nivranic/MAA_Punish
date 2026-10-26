"""Validated screenshot acquisition shared by combat consumers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from maa.context import Context

    from action.combat.core.session import CombatTask
    from action.combat.input_control import CombatInputScope


class CombatCaptureError(RuntimeError):
    """The current screenshot job did not produce a usable frame."""


@dataclass(frozen=True)
class FrameMetadata:
    generation: int
    sequence: int
    captured_at: float
    job_id: int


def _validate_image(image: Any) -> None:
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[2] != 3
        or image.size == 0
    ):
        raise CombatCaptureError("战斗截图未返回有效的 BGR 图像")


def capture_frame(
    context: Context,
    combat: CombatTask | None = None,
    *,
    scope: CombatInputScope | None = None,
    deadline: float | None = None,
) -> np.ndarray:
    """Invalidate the session frame before acquiring a successful BGR image.

    MaaFramework 5.14.2 ``Controller._get_screencap`` reads a shared cache;
    ``Job.wait`` does not assert success. Check the current job before ``get``.
    The ordinary path retains blocking compatibility. An explicitly enabled
    input scope polls within an absolute monotonic deadline and rejects late
    results. captured_at records local acquisition completion, not device age.
    """
    if scope is None and combat is not None:
        scope = getattr(combat, "input_scope", None)
    if scope is not None:
        return _capture_scoped(context, combat, scope, deadline)
    if deadline is not None:
        raise ValueError("有界截图必须提供会话输入作用域")
    if combat is not None:
        combat.frame = None

    job = context.tasker.controller.post_screencap()
    job.wait()
    if not job.succeeded:
        raise CombatCaptureError("战斗截图 Job 未成功完成")

    image = job.get()
    _validate_image(image)

    if combat is not None:
        combat.frame = image
    return image


def _capture_scoped(context, combat, scope, deadline):
    # Never invalidate or publish into another session's frame.
    scope.assert_active()
    if context is not scope.context or combat is not scope.combat:
        raise CombatCaptureError("截图 Context/会话与输入作用域不一致")
    scope.invalidate_frame()
    if scope.capture_in_flight:
        raise CombatCaptureError("同一会话已有未完成截图")
    limit = scope.monotonic() + scope.capture_timeout
    if deadline is not None:
        if not math.isfinite(deadline):
            raise ValueError("截图绝对期限必须为有限单调时间")
        limit = min(limit, deadline)
    if scope.monotonic() >= limit:
        scope.fail("截图等待期限已到")
        raise CombatCaptureError("截图等待期限已到，未下发新截图")
    scope.capture_in_flight = True
    try:
        job = scope.controller_for_jobs().post_screencap()
        scope.pending_capture = job
        scope.wait_job(job, limit)
        scope.assert_active()
        if scope.monotonic() >= limit:
            raise CombatCaptureError("截图缓存读取前已超过期限")
        image = job.get(wait=False)
        _validate_image(image)
        scope.assert_active()
        if scope.monotonic() >= limit:
            raise CombatCaptureError("截图结果返回时已超过期限")
        metadata = FrameMetadata(
            scope.generation,
            scope.capture_sequence + 1,
            scope.monotonic(),
            job.job_id,
        )
    except Exception as error:
        # Only a confirmed terminal job releases screenshot ownership. Pending
        # jobs stay recorded and can never feed a later session/cache read.
        try:
            terminal = scope.pending_capture is not None and scope.pending_capture.done
        except Exception:
            terminal = False
        if terminal:
            scope.pending_capture = None
            scope.capture_in_flight = False
        scope.fail(f"capture_failed: {error}")
        scope.discard_idle_controller()
        raise CombatCaptureError(str(error)) from error
    scope.pending_capture = None
    scope.capture_in_flight = False
    scope.discard_idle_controller()
    scope.capture_sequence = metadata.sequence
    scope.frame_metadata = scope.last_frame_metadata = metadata
    if combat is not None:
        combat.frame = image
    return image
