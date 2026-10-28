"""Drain native test queues before owned MaaFramework handles are destroyed."""

import time


def stop_and_wait_idle(tasker, timeout=5.0):
    # A callback can enqueue its own post_stop concurrently with this outer
    # stop. Joining only our Job does not prove the later stop queue is empty.
    tasker.post_stop().wait()
    deadline = time.monotonic() + timeout
    while tasker.running or tasker.stopping:
        if time.monotonic() >= deadline:
            raise TimeoutError("native test Tasker did not drain after stop")
        time.sleep(0.01)
