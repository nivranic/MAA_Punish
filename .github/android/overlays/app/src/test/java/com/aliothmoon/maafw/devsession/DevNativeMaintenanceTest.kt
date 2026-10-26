package com.aliothmoon.maafw.devsession

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.Executor

class DevNativeMaintenanceTest {
    private class Queue : Executor {
        val tasks = ArrayDeque<Runnable>()
        override fun execute(command: Runnable) { tasks.add(command) }
        fun runNext() = tasks.removeFirst().run()
    }
    private val id = "a".repeat(32)
    private val revision = "b".repeat(64)

    @Test fun expiredQueuedBeginNeverReleasesOrBecomesSafe() {
        val queue = Queue()
        var now = 0L
        var released = 0
        val target = DevNativeMaintenance(Any(), queue, { now }, {}, { _, _ -> released++; true }, { null }, { DevNativeStatus() })
        assertEquals("stopping", target.handle(DevNativeRequest("begin", id, 1_000)).phase)
        now = 1_001
        target.handle(DevNativeRequest("poll", id))
        queue.runNext()
        val status = target.handle(DevNativeRequest("poll", id))
        assertEquals("blocked", status.phase)
        assertFalse(status.safe_stopped)
        assertEquals(0, released)
        assertTrue(target.isBusy())
    }

    @Test fun stopFailurePreservesTokenAndRejectsPrepare() {
        val queue = Queue()
        var prepared = 0
        val target = DevNativeMaintenance(Any(), queue, { 0L }, {}, { _, _ -> false }, { prepared++; null }, { DevNativeStatus() })
        target.handle(DevNativeRequest("begin", id))
        queue.runNext()
        assertEquals("blocked", target.handle(DevNativeRequest("poll", id)).phase)
        assertThrows(IllegalArgumentException::class.java) { target.handle(DevNativeRequest("prepare", id, revision = revision, payload = "{}")) }
        assertEquals(0, prepared)
        assertTrue(target.isBusy())
    }

    @Test fun prepareReleaseAndConflictingOperationsAreSerial() {
        val queue = Queue()
        val order = mutableListOf<String>()
        val target = DevNativeMaintenance(Any(), queue, { 0L }, { order += "stop" }, { _, _ -> order += "release-old"; true }, { order += "prepare-only"; null }, { DevNativeStatus(agent_generation = 2, display_id = 7, controller_reused = true) })
        target.handle(DevNativeRequest("begin", id))
        assertThrows(IllegalArgumentException::class.java) { target.handle(DevNativeRequest("begin", "c".repeat(32))) }
        queue.runNext()
        assertTrue(target.handle(DevNativeRequest("poll", id)).safe_stopped)
        target.handle(DevNativeRequest("prepare", id, revision = revision, payload = "{}"))
        assertThrows(IllegalArgumentException::class.java) { target.handle(DevNativeRequest("release", id)) }
        queue.runNext()
        val ready = target.handle(DevNativeRequest("poll", id))
        assertEquals("prepared", ready.phase)
        assertEquals(1L, ready.native_generation)
        assertEquals(2L, ready.agent_generation)
        assertEquals(listOf("stop", "release-old", "prepare-only"), order)
        target.handle(DevNativeRequest("release", id))
        assertFalse(target.isBusy())
    }

    @Test fun timedOutPrepareNeverReportsLoadedAfterItReturns() {
        val queue = Queue()
        var now = 0L
        val target = DevNativeMaintenance(Any(), queue, { now }, {}, { _, _ -> true }, { now = 2_000; null }, { DevNativeStatus() })
        target.handle(DevNativeRequest("begin", id))
        queue.runNext()
        target.handle(DevNativeRequest("prepare", id, 1_000, revision, "{}"))
        queue.runNext()
        assertEquals("blocked", target.handle(DevNativeRequest("poll", id)).phase)
        assertEquals(0L, target.handle(DevNativeRequest("poll", id)).native_generation)
        assertTrue(target.isBusy())
    }
}
