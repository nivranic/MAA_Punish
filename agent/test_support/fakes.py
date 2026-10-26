"""Fake Maa Framework objects for agent unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class FakeBestResult:
    box: tuple[int, int, int, int]
    text: str = ""


@dataclass
class FakeRecognitionResult:
    hit: bool = False
    best_result: FakeBestResult | None = None
    filtered_results: list[FakeBestResult] = field(default_factory=list)


@dataclass
class FakeJob:
    image: Any = field(default_factory=lambda: np.zeros((2, 2, 3), dtype=np.uint8))
    succeeded: bool = True
    done: bool = True
    job_id: int = 1

    def wait(self) -> FakeJob:
        return self

    def get(self, wait: bool = False) -> Any:
        if wait:
            self.wait()
        return self.image


@dataclass
class FakeClock:
    """Local monotonic clock; sleeping advances time without wall-clock waits."""

    now: float = 0.0
    on_sleep: Any = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(self.now)


@dataclass
class TimedFakeJob:
    """A controllable native Job: pending -> running -> succeeded/failed.

    These jobs must be polled; wait raises to catch accidental unbounded waits.
    get still returns a cache on failure, matching the real API's danger.
    """

    clock: FakeClock
    finish_at: float = 0.0
    start_at: float = 0.0
    success: bool = True
    image: Any = field(default_factory=lambda: np.zeros((2, 2, 3), dtype=np.uint8))
    job_id: int = 1
    get_calls: int = 0

    @property
    def done(self) -> bool:
        return self.clock.now >= self.finish_at

    @property
    def succeeded(self) -> bool:
        return self.done and self.success

    @property
    def failed(self) -> bool:
        return self.done and not self.success

    @property
    def pending(self) -> bool:
        return not self.done and self.clock.now < self.start_at

    @property
    def running(self) -> bool:
        return not self.done and not self.pending

    def wait(self):
        raise AssertionError("bounded paths must never call Job.wait()")

    def get(self, wait: bool = False):
        if wait:
            self.wait()
        self.get_calls += 1
        return self.image


@dataclass
class FakeController:
    clicks: list[tuple[int, int]] = field(default_factory=list)
    swipes: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    image: Any = field(default_factory=lambda: np.zeros((2, 2, 3), dtype=np.uint8))
    touches: list[tuple] = field(default_factory=list)

    def post_screencap(self) -> FakeJob:
        return FakeJob(image=self.image)

    def post_click(self, x: int, y: int, contact: int = 0, pressure: int = 1) -> FakeJob:
        self.clicks.append((x, y))
        return FakeJob()

    def post_touch_down(
        self, x: int, y: int, contact: int = 0, pressure: int = 1
    ) -> FakeJob:
        self.touches.append(("down", x, y, contact, pressure))
        return FakeJob()

    def post_touch_up(self, contact: int = 0) -> FakeJob:
        self.touches.append(("up", contact))
        return FakeJob()

    def post_swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration: int
    ) -> FakeJob:
        self.swipes.append((x1, y1, x2, y2, duration))
        return FakeJob()


@dataclass
class FakeTasker:
    controller: FakeController = field(default_factory=FakeController)
    stopping: bool = False
    stop_requests: int = 0

    def post_stop(self) -> FakeJob:
        self.stop_requests += 1
        return FakeJob()


@dataclass
class FakeContext:
    """Minimal Maa Context stand-in for unit tests."""

    recognition_map: dict[str, FakeRecognitionResult] = field(default_factory=dict)
    check_role_results: list[FakeRecognitionResult | None] = field(default_factory=list)
    check_role_calls: list[dict[str, Any]] = field(default_factory=list)
    node_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    pipeline_overrides: list[dict[str, Any]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    tasker: FakeTasker = field(default_factory=FakeTasker)

    def run_recognition(
        self,
        entry: str,
        image: Any = None,
        pipeline_override: dict[str, Any] | None = None,
    ) -> FakeRecognitionResult | None:
        if entry == "检查角色":
            self.check_role_calls.append(
                {
                    "entry": entry,
                    "image": image,
                    "pipeline_override": pipeline_override,
                }
            )
            if self.check_role_results:
                return self.check_role_results.pop(0)
            return FakeRecognitionResult(hit=False)

        if entry in self.recognition_map:
            return self.recognition_map[entry]

        return FakeRecognitionResult(hit=False)

    def get_node_data(self, name: str) -> dict[str, Any] | None:
        return self.node_data.get(name)

    def override_pipeline(self, patch: dict[str, Any]) -> None:
        self.pipeline_overrides.append(patch)
        for node_name, node_patch in patch.items():
            current = self.node_data.setdefault(node_name, {})
            if isinstance(node_patch, dict):
                current.update(node_patch)

    def run_action(
        self,
        entry: str,
        pipeline_override: dict[str, Any] | None = None,
    ) -> bool:
        self.actions.append(entry)
        return True


def make_hit(box: tuple[int, int, int, int] = (100, 200, 40, 40)) -> FakeRecognitionResult:
    best = FakeBestResult(box=box)
    return FakeRecognitionResult(hit=True, best_result=best, filtered_results=[best])


def make_miss() -> FakeRecognitionResult:
    return FakeRecognitionResult(hit=False)
