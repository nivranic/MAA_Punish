"""方案 3.4 每辅助状态机：同帧不重复点、待确认收尾、循环复位只清统计。"""

from action.combat.actions.assist_state import AssistReleaseTracker


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_tracker(now: float = 0.0) -> tuple[AssistReleaseTracker, FakeClock]:
    clock = FakeClock(now)
    return AssistReleaseTracker(clock=clock), clock


def test_same_frame_token_blocks_repeat_request_per_slot():
    tracker, _ = make_tracker()
    assert tracker.can_request("qte-R", 1) is True
    tracker.record_request("qte-R", 1)
    assert tracker.can_request("qte-R", 1) is False
    assert tracker.can_request("qte-B", 1) is True
    assert tracker.can_request("qte-R", 2) is True


def test_ready_gone_on_new_frame_confirms_request():
    tracker, _ = make_tracker()
    tracker.record_request("qte-R", 1)
    tracker.observe_slot("qte-R", ready=False)
    assert tracker.state("qte-R") == AssistReleaseTracker.CONFIRMED
    assert tracker.stats["confirmed"] == 1


def test_persisting_ready_expires_to_unconfirmed_then_cools_down():
    tracker, clock = make_tracker()
    tracker.record_request("qte-R", 1)
    clock.now = AssistReleaseTracker.PENDING_TIMEOUT - 0.1
    tracker.observe_slot("qte-R", ready=True)
    assert tracker.state("qte-R") == AssistReleaseTracker.REQUESTED
    clock.now = AssistReleaseTracker.PENDING_TIMEOUT + 0.1
    tracker.observe_slot("qte-R", ready=True)
    assert tracker.state("qte-R") == AssistReleaseTracker.UNCONFIRMED
    assert tracker.stats["unconfirmed"] == 1
    # 真实冷却：未确认后不立即重试
    assert tracker.can_request("qte-R", 2) is False
    clock.now += AssistReleaseTracker.RETRY_COOLDOWN
    assert tracker.can_request("qte-R", 2) is True


def test_new_request_closes_stale_pending_as_unconfirmed():
    tracker, _ = make_tracker()
    tracker.record_request("qte-R", 1)
    tracker.record_request("qte-R", 2)
    assert tracker.state("qte-R") == AssistReleaseTracker.REQUESTED
    assert tracker.stats["unconfirmed"] == 1
    assert tracker.stats["requests"] == 2


def test_cycle_reset_clears_stats_but_keeps_pending_and_cooldown():
    tracker, clock = make_tracker(now=10.0)
    tracker.record_request("qte-R", 2)
    clock.now = 10.0 + AssistReleaseTracker.PENDING_TIMEOUT + 0.1
    tracker._expire_pending()
    assert tracker.state("qte-R") == AssistReleaseTracker.UNCONFIRMED
    tracker.record_request("aux", 1)
    tracker.cycle_reset()
    assert tracker.stats == {"requests": 0, "confirmed": 0, "unconfirmed": 0, "missed": 0}
    # 待确认请求的记账保留：aux 仍是请求中，不被复位遗忘
    assert tracker.state("aux") == AssistReleaseTracker.REQUESTED
    assert tracker.can_request("aux", 1) is False
    # 真实冷却保留：qte-R 仍在重试冷却内
    assert tracker.can_request("qte-R", 3) is False


def test_missed_counts_only_allowed_windows():
    tracker, _ = make_tracker()
    tracker.note_missed(window_allowed=False)
    assert tracker.stats["missed"] == 0
    tracker.note_missed(window_allowed=True)
    assert tracker.stats["missed"] == 1
