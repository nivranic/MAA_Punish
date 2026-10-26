package com.aliothmoon.maafw.remote

import com.aliothmoon.maafw.IMaaRunnerCallback
import com.aliothmoon.maafw.bridge.NativeBridgeLib
import com.aliothmoon.maafw.maa.MaaFrameworkLibrary
import com.aliothmoon.maafw.maa.MaaFrameworkLoader
import com.aliothmoon.maafw.maa.MaaStatus
import com.aliothmoon.maafw.remote.internal.VirtualDisplayManager
import com.aliothmoon.maafw.runner.RunOutcome
import com.aliothmoon.maafw.runner.RunPlanPayload
import com.aliothmoon.maafw.runner.RuntimeTaskPayload
import com.aliothmoon.maafw.third.Ln
import com.sun.jna.Pointer
import com.sun.jna.ptr.ByteByReference
import com.sun.jna.ptr.IntByReference
import com.sun.jna.ptr.LongByReference
import io.mockk.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Executes the actual patched task loop, dispatch, callback, and cleanup ordering on a host JVM. */
class MaaRunnerRecoveryHistoryTest {
    private val lib = mockk<MaaFrameworkLibrary>(relaxed = true)
    private val callback = mockk<IMaaRunnerCallback>(relaxed = true)
    private lateinit var runner: MaaRunner
    private val tasker = Pointer(33)
    private var bridgeLoaded = false
    private var posts = 0
    private var buffers = 100L
    private val strings = mutableMapOf<Long, String>()
    private val taskResults = mutableListOf<Pair<Boolean, Boolean>>()
    private val taskMessages = mutableListOf<String>()
    private val outcomes = mutableListOf<Int>()
    private val finishReasons = mutableListOf<String>()
    private var recover = false
    private var unknown = false
    private var waitStatus = MaaStatus.SUCCEEDED
    private var nodeCompleted: Byte = 1

    @Before fun setUp() {
        mockkStatic(Ln::class)
        every { Ln.i(any()) } just Runs
        every { Ln.w(any<String>()) } just Runs
        every { Ln.e(any<String>()) } just Runs
        every { Ln.e(any<String>(), any<Throwable>()) } just Runs
        bridgeLoaded = NativeBridgeLib.LOADED
        NativeBridgeLib.LOADED = true
        mockkObject(MaaFrameworkLoader)
        every { MaaFrameworkLoader.library } returns lib
        mockkObject(VirtualDisplayManager)
        every { VirtualDisplayManager.getDisplayId() } returns 42
        every { lib.MaaControllerConnected(any()) } returns 1
        every { lib.MaaTaskerPostTask(tasker, any(), any()) } answers { posts++; 1000L + posts }
        every { lib.MaaTaskerWait(tasker, any()) } answers { waitStatus }
        every { lib.MaaStringBufferCreate() } answers { Pointer(++buffers) }
        every { lib.MaaStringBufferGet(any()) } answers { strings[Pointer.nativeValue(firstArg())] }
        every { lib.MaaTaskerGetTaskDetail(tasker, any(), any(), any(), any(), any()) } answers {
            if (unknown) 0.toByte() else {
                val id = secondArg<Long>()
                val entryBuffer = arg<Pointer>(2)
                val ids = arg<Pointer?>(3)
                strings[Pointer.nativeValue(entryBuffer)] = if (id == 1001L) "First" else "Second"
                if (ids != null) ids.setLong(0, id * 10)
                arg<LongByReference>(4).value = 1
                arg<IntByReference>(5).value = waitStatus
                1.toByte()
            }
        }
        every { lib.MaaTaskerGetNodeDetail(tasker, any(), any(), any(), any(), any()) } answers {
            strings[Pointer.nativeValue(arg(2))] = if (recover) "返回主菜单_err" else "Normal"
            arg<ByteByReference>(5).value = nodeCompleted
            1.toByte()
        }
        every { lib.MaaTaskerRunning(tasker) } returns 0
        every { callback.onTaskFinished(any(), any(), any(), any()) } answers {
            taskResults += secondArg<Boolean>() to arg<Boolean>(3)
            taskMessages += arg<String>(2)
        }
        every { callback.onFinished(any(), any()) } answers {
            outcomes += firstArg<Int>()
            finishReasons += secondArg<String>()
        }
        runner = MaaRunner(mockk<AgentHost>())
        runner.setCallback(callback)
        setField("resource", Pointer(11))
        setField("controller", Pointer(22))
        setField("tasker", tasker)
        setField("boundDisplayId", 42)
        setField("running", true)
    }

    @After fun tearDown() {
        if (::runner.isInitialized) (field("worker") as ExecutorService).shutdownNow()
        NativeBridgeLib.LOADED = bridgeLoaded
        unmockkAll()
        Thread.interrupted()
    }

    private fun field(name: String) = MaaRunner::class.java.getDeclaredField(name).apply { isAccessible = true }.get(runner)
    private fun setField(name: String, value: Any?) = MaaRunner::class.java.getDeclaredField(name).apply { isAccessible = true }.set(runner, value)
    private fun runPlan() {
        val payload = RunPlanPayload(emptyList(), 1280, 720, tasks = listOf(
            RuntimeTaskPayload("first", "First", emptyList()), RuntimeTaskPayload("second", "Second", emptyList()),
        ))
        MaaRunner::class.java.getDeclaredMethod("runPlan", RunPlanPayload::class.java)
            .apply { isAccessible = true }.invoke(runner, payload)
    }

    @Test fun `normal complete first task dispatches second task`() {
        runPlan()
        assertEquals(2, posts)
        assertEquals(listOf(true to false, true to false), taskResults)
        assertEquals(listOf(RunOutcome.COMPLETED), outcomes)
        assertEquals(listOf(
            "succeeded; task_id=1001; wait_status=3000; recovery_history=no_shared_recovery; native_status=3000",
            "succeeded; task_id=1002; wait_status=3000; recovery_history=no_shared_recovery; native_status=3000",
        ), taskMessages)
        assertFalse(runner.isRunning())
        assertEquals(listOf(""), finishReasons)
    }

    @Test fun `native succeeded recovery stops round as failure even after stopping flag resets`() {
        recover = true
        runPlan()
        assertEquals(1, posts)
        assertEquals(listOf(false to false), taskResults)
        assertEquals(listOf(RunOutcome.COMPLETED_WITH_FAILURES), outcomes)
        assertEquals(listOf(
            "succeeded; task_id=1001; wait_status=3000; recovery_history=shared_recovery_visited; native_status=3000",
        ), taskMessages)
        verify(exactly = 0) { lib.MaaTaskerDestroy(any()) }
        verify(exactly = 0) { lib.MaaControllerDestroy(any()) }
    }

    @Test fun `recovery summary observes native idle before clearing remote running and finishing`() {
        recover = true
        var queries = 0
        val events = mutableListOf<String>()
        every { lib.MaaTaskerRunning(tasker) } answers {
            queries++
            assertTrue(runner.isRunning())
            assertTrue(finishReasons.isEmpty())
            if (queries < 3) {
                events += "native_busy"
                1.toByte()
            } else {
                events += "native_idle"
                0.toByte()
            }
        }
        every { callback.onFinished(any(), any()) } answers {
            assertFalse(runner.isRunning())
            finishReasons += secondArg<String>()
            events += "finished"
        }
        val before = System.nanoTime()
        runPlan()
        val after = System.nanoTime()
        assertEquals(3, queries)
        assertEquals(listOf("native_busy", "native_busy", "native_idle", "finished"), events)
        val reason = finishReasons.single()
        assertTrue(reason.startsWith("recovery_history:shared_recovery_visited; task_id=1001; native_running_observed=false; "))
        val observed = reason.substringAfter("native_idle_observed_at_ns=").substringBefore(';').toLong()
        val cleared = reason.substringAfter("runner_running_cleared_at_ns=").toLong()
        assertTrue(before <= observed && observed <= cleared && cleared <= after)
        assertEquals(1, posts)
    }

    @Test fun `unavailable history blocks second task with failure outcome`() {
        unknown = true
        runPlan()
        assertEquals(1, posts)
        assertEquals(listOf(false to false), taskResults)
        assertEquals(listOf(RunOutcome.COMPLETED_WITH_FAILURES), outcomes)
        assertEquals(listOf(
            "succeeded; task_id=1001; wait_status=3000; recovery_history=task_detail_unavailable; native_status=null",
        ), taskMessages)
    }

    @Test fun `rejected post never invents a wait result or native task identity`() {
        every { lib.MaaTaskerPostTask(tasker, any(), any()) } answers { posts++; 0L }
        runPlan()
        assertEquals(2, posts)
        assertEquals(listOf(false to false, false to false), taskResults)
        assertEquals(listOf("PostTask 被拒绝", "PostTask 被拒绝"), taskMessages)
        verify(exactly = 0) { lib.MaaTaskerWait(any(), any()) }
        verify(exactly = 0) { lib.MaaTaskerGetTaskDetail(any(), any(), any(), any(), any(), any()) }
    }

    @Test fun `ordinary failed task keeps dispatching without recovery`() {
        waitStatus = MaaStatus.FAILED
        nodeCompleted = 0
        runPlan()
        assertEquals(2, posts)
        assertEquals(listOf(false to false, false to false), taskResults)
        assertEquals(listOf(RunOutcome.COMPLETED_WITH_FAILURES), outcomes)
    }

    @Test fun `user stop remains cancellation and cannot open the dispatch gate`() {
        every { lib.MaaTaskerWait(tasker, any()) } answers { runner.stop(); MaaStatus.INVALID }
        runPlan()
        assertEquals(1, posts)
        assertEquals(listOf(false to true), taskResults)
        assertEquals(listOf(RunOutcome.CANCELLED), outcomes)
    }

    @Test fun `interrupted cleanup retains busy and handles until actual native stop`() {
        recover = true
        val polled = CountDownLatch(1)
        val stopped = AtomicBoolean(false)
        val interruptRestored = AtomicBoolean(false)
        every { lib.MaaTaskerRunning(tasker) } answers { polled.countDown(); if (stopped.get()) 0 else 1 }
        val thread = Thread { runPlan(); interruptRestored.set(Thread.currentThread().isInterrupted) }
        thread.start()
        try {
            assertTrue(polled.await(5, TimeUnit.SECONDS))
            thread.interrupt()
            assertTrue(runner.isRunning())
            assertTrue(outcomes.isEmpty())
            assertTrue(finishReasons.isEmpty())
            assertEquals(tasker, field("tasker"))
            assertEquals(1, posts)
        } finally {
            stopped.set(true)
            thread.join(5000)
        }
        assertFalse(thread.isAlive)
        assertTrue(interruptRestored.get())
        assertFalse(runner.isRunning())
        assertEquals(listOf(RunOutcome.COMPLETED_WITH_FAILURES), outcomes)
    }

    @Test fun `unknown native running retains original busy owner and emits no terminal`() {
        recover = true
        every { lib.MaaTaskerRunning(tasker) } throws IllegalStateException("unknown")
        runPlan()
        assertEquals(1, posts)
        assertTrue(runner.isRunning())
        assertTrue(outcomes.isEmpty())
        assertEquals(tasker, field("tasker"))
        assertNotNull(field("recoveryHistoryOwner"))
        assertTrue(finishReasons.isEmpty())
    }

    @Test fun `lost runtime ownership never emits native idle confirmation`() {
        recover = true
        every { lib.MaaTaskerRunning(tasker) } answers {
            setField("recoveryHistoryOwner", Any())
            1.toByte()
        }
        runPlan()
        assertTrue(runner.isRunning())
        assertTrue(outcomes.isEmpty())
        assertTrue(finishReasons.isEmpty())
        verify(exactly = 1) { lib.MaaTaskerRunning(tasker) }
        verify(exactly = 0) { lib.MaaTaskerDestroy(any()) }
    }

    @Test fun `owner change after native idle still cannot publish a confirmation`() {
        recover = true
        every { lib.MaaTaskerRunning(tasker) } answers {
            setField("recoveryHistoryOwner", Any())
            0.toByte()
        }
        runPlan()
        assertTrue(runner.isRunning())
        assertTrue(outcomes.isEmpty())
        assertTrue(finishReasons.isEmpty())
    }
}
