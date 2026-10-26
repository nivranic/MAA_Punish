package com.aliothmoon.maafw.maa

import com.sun.jna.Pointer
import com.sun.jna.ptr.ByteByReference
import com.sun.jna.ptr.IntByReference
import com.sun.jna.ptr.LongByReference
import org.junit.Assert.*
import org.junit.Test
import java.lang.reflect.Proxy

class TaskRecoveryHistoryTest {
    private data class Node(val id: Long, val name: String, val completed: Byte = 1)

    /** Scripted native endpoints; JNA reference widths and Memory reads remain real JVM operations. */
    private class NativeFixture {
        var nodes = listOf(Node(4_294_967_297L, "指挥局"), Node(4_294_967_298L, "检查主界面2"))
        var entry = "指挥局"
        var status = MaaStatus.SUCCEEDED
        var owner = true
        var clock = 0L
        var taskReads = 0
        var nodeReads = 0
        var creates = 0
        val destroyed = mutableListOf<Long>()
        val queriedNodes = mutableListOf<Long>()
        val suppliedTaskIds = mutableListOf<Long>()
        val strings = mutableMapOf<Long, String>()
        var failCreateAt = -1
        var failTaskAt = -1
        var failNodeAt = -1
        var throwTaskAt = -1
        var throwDestroyAt = -1
        var beforeTask: (Int) -> Unit = {}
        var afterNode: (Int) -> Unit = {}
        val tasker = Pointer.createConstant(99)

        val lib: MaaFrameworkLibrary = Proxy.newProxyInstance(
            MaaFrameworkLibrary::class.java.classLoader,
            arrayOf(MaaFrameworkLibrary::class.java),
        ) { proxy, method, supplied ->
            val args = supplied.orEmpty()
            when (method.name) {
                "equals" -> proxy === args[0]
                "hashCode" -> System.identityHashCode(proxy)
                "toString" -> "ScriptedMaaFrameworkLibrary"
                "MaaStringBufferCreate" -> {
                    creates++
                    if (creates == failCreateAt) null else Pointer.createConstant(1000L + creates)
                }
                "MaaStringBufferGet" -> strings[Pointer.nativeValue(args[0] as Pointer?)]
                "MaaStringBufferDestroy" -> {
                    destroyed += Pointer.nativeValue(args[0] as Pointer?)
                    if (destroyed.size == throwDestroyAt) error("destroy failure")
                    null
                }
                "MaaTaskerGetTaskDetail" -> {
                    assertEquals(tasker, args[0])
                    suppliedTaskIds += args[1] as Long
                    taskReads++
                    beforeTask(taskReads)
                    if (taskReads == throwTaskAt) error("query failure")
                    if (taskReads == failTaskAt) 0.toByte() else {
                        val count = args[4] as LongByReference
                        val capacity = count.value
                        strings[Pointer.nativeValue(args[2] as Pointer)] = entry
                        (args[5] as IntByReference).value = status
                        val ids = args[3] as Pointer?
                        if (ids != null) {
                            nodes.take(capacity.toInt()).forEachIndexed { index, node ->
                                ids.setLong(index * 8L, node.id)
                            }
                        }
                        count.value = nodes.size.toLong()
                        1.toByte()
                    }
                }
                "MaaTaskerGetNodeDetail" -> {
                    assertEquals(tasker, args[0])
                    val id = args[1] as Long
                    queriedNodes += id
                    nodeReads++
                    if (nodeReads == failNodeAt) 0.toByte() else {
                        val node = nodes.first { it.id == id }
                        strings[Pointer.nativeValue(args[2] as Pointer)] = node.name
                        (args[3] as LongByReference).value = 200
                        (args[4] as LongByReference).value = 300
                        (args[5] as ByteByReference).value = node.completed
                        afterNode(nodeReads)
                        1.toByte()
                    }
                }
                else -> error("Unexpected native operation ${method.name}")
            }
        } as MaaFrameworkLibrary

        fun inspect(waitStatus: Int = status, expected: String = "指挥局") = TaskRecoveryHistory.inspect(
            lib, tasker, 1_099_511_628_034L, expected, waitStatus,
            ownsRuntime = { owner }, nanoTime = { clock },
        )
    }

    @Test fun `normal complete history permits next task and preserves 64 bit IDs`() {
        val f = NativeFixture()
        val result = f.inspect()
        val gate = RecoveryDispatchGate()
        assertTrue(result.complete)
        assertFalse(result.recoverySeen)
        assertTrue(gate.success(MaaStatus.SUCCEEDED, result))
        assertFalse(gate.aborted)
        assertEquals(f.nodes.map { it.id }, f.queriedNodes)
        assertEquals(listOf(1_099_511_628_034L), f.suppliedTaskIds.distinct())
        assertEquals(3, f.taskReads)
        assertEquals(2, f.destroyed.size)
    }

    @Test fun `native succeeded with recovery is failed before next dispatch regardless of events`() {
        val f = NativeFixture().apply { nodes = nodes + Node(4_294_967_299L, "返回主菜单_err", 0) }
        val result = f.inspect()
        val gate = RecoveryDispatchGate()
        var nextClicks = 0
        assertFalse(gate.success(MaaStatus.SUCCEEDED, result))
        if (!gate.aborted) nextClicks++
        assertTrue(result.recoverySeen)
        assertEquals(0, nextClicks)
        // No sink/event endpoint exists in this fixture: late notification cannot open the latch.
        assertFalse(gate.success(MaaStatus.SUCCEEDED, TaskRecoveryHistory.Result(true, false, "normal")))
        assertTrue(gate.aborted)
    }

    @Test fun `ordinary failed task without recovery keeps existing failure consumption`() {
        val f = NativeFixture().apply {
            status = MaaStatus.FAILED
            nodes = listOf(Node(1, "NormalFailedAction", 0))
        }
        val gate = RecoveryDispatchGate()
        val result = f.inspect()
        assertTrue(result.complete)
        assertEquals(1L, result.completedFalseCount)
        assertFalse(gate.success(MaaStatus.FAILED, result))
        assertFalse(gate.aborted)
    }

    @Test fun `early wait zero cannot be promoted by a later succeeded status`() {
        val f = NativeFixture()
        assertEquals("task_completion_unconfirmed", f.inspect(MaaStatus.INVALID).reason)
    }

    @Test fun `not terminal native record is unconfirmed`() {
        val f = NativeFixture().apply { status = MaaStatus.RUNNING }
        assertFalse(f.inspect().complete)
        assertEquals(1, f.taskReads)
        assertEquals(1, f.destroyed.size)
    }

    @Test fun `wrong entry never reads any node`() {
        val f = NativeFixture().apply { entry = "其他任务" }
        assertTrue(f.inspect().mustAbort)
        assertEquals(0, f.nodeReads)
    }

    @Test fun `empty history is unknown rather than proof no recovery occurred`() {
        val f = NativeFixture().apply { nodes = emptyList() }
        assertEquals("task_entry_or_count_invalid", f.inspect().reason)
    }

    @Test fun `oversized history is rejected without truncation`() {
        val f = NativeFixture().apply { nodes = (1..65_537).map { Node(it.toLong(), "normal") } }
        assertEquals("task_entry_or_count_invalid", f.inspect().reason)
        assertEquals(0, f.nodeReads)
    }

    @Test fun `node count change after capacity query aborts`() {
        val f = NativeFixture()
        f.beforeTask = { if (it == 2) f.nodes = f.nodes + Node(9, "new") }
        assertEquals("task_history_changed", f.inspect().reason)
        assertEquals(0, f.nodeReads)
    }

    @Test fun `final same length node ID change aborts`() {
        val f = NativeFixture()
        f.beforeTask = { if (it == 3) f.nodes = f.nodes.reversed() }
        assertEquals("task_history_changed", f.inspect().reason)
    }

    @Test fun `final status or entry change aborts`() {
        for (changeEntry in listOf(false, true)) {
            val f = NativeFixture()
            f.beforeTask = { if (it == 3) { if (changeEntry) f.entry = "different" else f.status = MaaStatus.FAILED } }
            assertEquals("task_history_changed", f.inspect().reason)
        }
    }

    @Test fun `duplicate or invalid IDs abort before node queries`() {
        for (ids in listOf(listOf(1L, 1L), listOf(0L, 2L), listOf(-1L, 2L))) {
            val f = NativeFixture().apply { nodes = ids.map { Node(it, "normal") } }
            assertEquals("node_ids_invalid", f.inspect().reason)
            assertEquals(0, f.nodeReads)
        }
    }

    @Test fun `placeholder blank node cannot be classified as normal`() {
        val f = NativeFixture().apply { nodes = listOf(Node(1, "", 0)) }
        assertEquals("node_detail_invalid", f.inspect().reason)
    }

    @Test fun `named false bit is preserved in stable terminal history without inventing missing data`() {
        val f = NativeFixture().apply { nodes = listOf(Node(1, "normal", 0)) }
        val result = f.inspect()
        assertTrue(result.complete)
        assertFalse(result.mustAbort)
        assertEquals(1L, result.completedFalseCount)
    }

    @Test fun `invalid completed byte is not coerced to true`() {
        val f = NativeFixture().apply { nodes = listOf(Node(1, "normal", 2)) }
        assertEquals("node_detail_invalid", f.inspect().reason)
    }

    @Test fun `each failed native read aborts and destroys created string buffers`() {
        for (read in 1..3) {
            val f = NativeFixture().apply { failTaskAt = read }
            assertTrue(f.inspect().mustAbort)
            assertEquals(if (read < 3) 1 else 2, f.destroyed.size)
        }
        val f = NativeFixture().apply { failNodeAt = 1 }
        assertTrue(f.inspect().mustAbort)
        assertEquals(2, f.destroyed.size)
    }

    @Test fun `allocation failure and native exception are unconfirmed`() {
        val first = NativeFixture().apply { failCreateAt = 1 }
        assertEquals("entry_buffer_unavailable", first.inspect().reason)
        assertTrue(first.destroyed.isEmpty())
        val second = NativeFixture().apply { failCreateAt = 2 }
        assertEquals("node_buffer_unavailable", second.inspect().reason)
        assertEquals(1, second.destroyed.size)
        val throwing = NativeFixture().apply { throwTaskAt = 1 }
        assertEquals("native_history_query_failed", throwing.inspect().reason)
        assertEquals(1, throwing.destroyed.size)
    }

    @Test fun `destroy failure still attempts both buffers and aborts`() {
        val f = NativeFixture().apply { throwDestroyAt = 1 }
        assertEquals("history_buffer_destroy_failed", f.inspect().reason)
        assertEquals(2, f.destroyed.size)
    }

    @Test fun `old owner or stop before inspection reads no native history`() {
        val f = NativeFixture().apply { owner = false }
        assertEquals("runtime_owner_or_deadline", f.inspect().reason)
        assertEquals(0, f.creates)
        assertEquals(0, f.taskReads)
    }

    @Test fun `owner change during node read prevents further tasker access`() {
        val f = NativeFixture()
        f.afterNode = { f.owner = false }
        assertEquals("runtime_owner_or_deadline", f.inspect().reason)
        assertEquals(1, f.nodeReads)
        assertEquals(2, f.taskReads)
        assertEquals(2, f.destroyed.size)
    }

    @Test fun `synchronous overrun is rejected when it returns without inventing cancellation`() {
        val f = NativeFixture()
        f.afterNode = { f.clock += 2_000_000_000L }
        assertEquals("runtime_owner_or_deadline", f.inspect().reason)
        assertEquals(1, f.nodeReads)
    }

    @Test fun `logical clock moving backwards cannot open the gate`() {
        val f = NativeFixture()
        f.beforeTask = { f.clock = -1 }
        assertEquals("runtime_owner_or_deadline", f.inspect().reason)
    }

    @Test fun `actual JNI method declarations retain fixed uint64 and uint8 references`() {
        val task = MaaFrameworkLibrary::class.java.methods.single { it.name == "MaaTaskerGetTaskDetail" }
        assertEquals(java.lang.Long.TYPE, task.parameterTypes[1])
        assertEquals(LongByReference::class.java, task.parameterTypes[4])
        assertEquals(IntByReference::class.java, task.parameterTypes[5])
        assertEquals(java.lang.Byte.TYPE, task.returnType)
        val node = MaaFrameworkLibrary::class.java.methods.single { it.name == "MaaTaskerGetNodeDetail" }
        assertEquals(ByteByReference::class.java, node.parameterTypes[5])
    }

    @Test fun `cleanup polls actual running and retains interruption until stop observed`() {
        var runningReads = 0
        var pauses = 0
        val result = RecoveryNativeStopBarrier.awaitStopped(
            ownsRuntime = { true },
            nativeRunning = { runningReads++; if (runningReads < 3) 1 else 0 },
            pause = { assertEquals(20L, it); pauses++; if (pauses == 1) throw InterruptedException() },
        )
        assertTrue(result.stopped)
        assertTrue(result.interrupted)
        assertEquals(3, runningReads)
        assertEquals(2, pauses)
    }

    @Test fun `cleanup unknown owner or native observation never reports stopped`() {
        var calls = 0
        assertFalse(RecoveryNativeStopBarrier.awaitStopped({ false }, { calls++; 0 }).stopped)
        assertEquals(0, calls)
        assertFalse(RecoveryNativeStopBarrier.awaitStopped({ true }, { throw IllegalStateException() }).stopped)
        assertFalse(RecoveryNativeStopBarrier.awaitStopped({ true }, { 2 }).stopped)
        var owner = true
        assertFalse(RecoveryNativeStopBarrier.awaitStopped({ owner }, { 1 }, { owner = false }).stopped)
    }
}
