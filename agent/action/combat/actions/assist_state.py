"""方案 3.4：每个辅助只维护「可用、请求中、已确认/未确认」的最小状态。

约束（方案 3.4 [G8]）：
- 同一旧图不反复点（帧 token 守卫）；持续 ready 的重复点击由调用方的
  周期纪律（每周期一次）约束，一个循环可有多次真实机会；
- 达芬奇等联动后重新取图，再决定另一个 QTE（调用方每次决策新采帧）；
- 循环复位只清统计：真实冷却与待确认请求的记账保留，待确认请求不被
  静默丢弃——新请求会先把旧请求收尾为「未确认」再登记；
- 动作转接、遮挡或不允许输入的时段不计为「可释放却漏放」（调用方
  只在允许输入的窗口记漏放）。
"""

from __future__ import annotations

import time
from typing import Callable


class AssistReleaseTracker:
    AVAILABLE = "available"
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"

    # 点击到「ready 消失」的确认窗口：超窗仍 ready 判未确认（点击未落地）。
    PENDING_TIMEOUT = 5.0
    # 未确认后的保守重试冷却（真实冷却不因循环复位缩短）。
    RETRY_COOLDOWN = 3.0

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._slots: dict[str, dict] = {}
        self._stats = {"requests": 0, "confirmed": 0, "unconfirmed": 0, "missed": 0}

    def _slot(self, slot: str) -> dict:
        return self._slots.setdefault(slot, {
            "state": self.AVAILABLE,
            "frame_token": None,
            "requested_at": 0.0,
            "rearm_after": 0.0,
        })

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def state(self, slot: str) -> str:
        return self._slot(slot)["state"]

    def _close_pending(self, entry: dict, now: float, *, confirmed: bool) -> None:
        entry["state"] = self.CONFIRMED if confirmed else self.UNCONFIRMED
        self._stats["confirmed" if confirmed else "unconfirmed"] += 1
        if not confirmed:
            entry["rearm_after"] = now + self.RETRY_COOLDOWN

    def _expire_pending(self) -> None:
        now = self._clock()
        for entry in self._slots.values():
            if (
                entry["state"] == self.REQUESTED
                and now - entry["requested_at"] >= self.PENDING_TIMEOUT
            ):
                self._close_pending(entry, now, confirmed=False)

    def can_request(self, slot: str, frame_token) -> bool:
        self._expire_pending()
        entry = self._slot(slot)
        if entry["frame_token"] == frame_token:
            return False
        return self._clock() >= entry["rearm_after"]

    def record_request(self, slot: str, frame_token) -> None:
        now = self._clock()
        entry = self._slot(slot)
        if entry["state"] == self.REQUESTED:
            # 待确认请求不静默丢弃：新请求前先把旧请求收尾为未确认。
            self._close_pending(entry, now, confirmed=False)
        entry.update(
            state=self.REQUESTED,
            frame_token=frame_token,
            requested_at=now,
        )
        self._stats["requests"] += 1

    def observe_slot(self, slot: str, ready: bool) -> None:
        """用新帧观测结算待确认请求：ready 消失=已确认；超窗仍在=未确认。"""
        entry = self._slot(slot)
        if entry["state"] != self.REQUESTED:
            return
        if not ready:
            self._close_pending(entry, self._clock(), confirmed=True)
        else:
            self._expire_pending()

    def note_missed(self, window_allowed: bool = True) -> None:
        """漏放记账。只在允许输入的窗口调用；转接/遮挡/禁输入时段不计。"""
        if window_allowed:
            self._stats["missed"] += 1

    def cycle_reset(self) -> None:
        """循环复位只清统计：真实冷却与待确认请求的记账保留。"""
        self._stats = {"requests": 0, "confirmed": 0, "unconfirmed": 0, "missed": 0}
