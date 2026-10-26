package com.aliothmoon.maafw.maa

import com.sun.jna.Memory
import com.sun.jna.Pointer
import com.sun.jna.ptr.ByteByReference
import com.sun.jna.ptr.IntByReference
import com.sun.jna.ptr.LongByReference

/** Reads this task's retained native history; asynchronous sink delivery is not a dispatch gate. */
object TaskRecoveryHistory {
    private const val RECOVERY_NODE = "返回主菜单_err"
    private const val MAX_NODES = 65_536L
    private const val QUERY_NANOS = 2_000_000_000L

    data class Result(
        val complete: Boolean,
        val recoverySeen: Boolean,
        val reason: String,
        val nativeStatus: Int? = null,
        val nodeCount: Long? = null,
        val completedFalseCount: Long = 0,
    ) {
        val mustAbort: Boolean get() = !complete || recoverySeen
    }

    /**
     * Signatures and widths follow MaaFramework v5.14.2 MaaDef.h / Instance/MaaTasker.h.
     * MaaSize is uint64_t on BOTH Android ABIs; node IDs occupy eight bytes each.
     * The logical deadline cannot cancel a synchronous native call.
     */
    fun inspect(
        lib: MaaFrameworkLibrary,
        tasker: Pointer?,
        taskId: Long,
        expectedEntry: String,
        waitStatus: Int,
        ownsRuntime: () -> Boolean,
        nanoTime: () -> Long = System::nanoTime,
    ): Result {
        val started = nanoTime()
        var recoverySeen = false
        var observedStatus: Int? = null
        var observedCount: Long? = null
        var completedFalseCount = 0L
        fun unknown(reason: String) = Result(false, recoverySeen, reason, observedStatus, observedCount, completedFalseCount)
        fun active(): Boolean = try {
            ownsRuntime() && nanoTime() - started in 0 until QUERY_NANOS
        } catch (_: Throwable) {
            false
        }
        if (tasker == null || Pointer.nativeValue(tasker) == 0L || taskId <= 0 || expectedEntry.isBlank()) {
            return unknown("invalid_task_identity")
        }
        if (!active()) return unknown("runtime_owner_or_deadline")
        var entryBuffer: Pointer? = null
        var nameBuffer: Pointer? = null
        fun readHistory(): Result = try {
            entryBuffer = lib.MaaStringBufferCreate()?.takeIf { Pointer.nativeValue(it) != 0L }
                ?: return unknown("entry_buffer_unavailable")
            if (!active()) return unknown("runtime_owner_or_deadline")
            val count = LongByReference(0)
            val status = IntByReference(MaaStatus.INVALID)
            if (lib.MaaTaskerGetTaskDetail(tasker, taskId, entryBuffer, null, count, status).toInt() != 1) {
                return unknown("task_detail_unavailable")
            }
            observedCount = count.value
            observedStatus = status.value
            val capacity = count.value
            val firstEntry = lib.MaaStringBufferGet(entryBuffer)
            if (!active()) return unknown("runtime_owner_or_deadline")
            if (firstEntry != expectedEntry || capacity !in 1..MAX_NODES) {
                return unknown("task_entry_or_count_invalid")
            }
            if (!MaaStatus.isDone(status.value)) return unknown("task_not_terminal")
            val firstStatus = status.value
            Memory(Math.multiplyExact(capacity, 8L)).use { idsBuffer ->
                count.value = capacity
                if (lib.MaaTaskerGetTaskDetail(tasker, taskId, entryBuffer, idsBuffer, count, status).toInt() != 1) {
                    return unknown("task_detail_second_read_failed")
                }
                if (!active()) return unknown("runtime_owner_or_deadline")
                if (count.value != capacity || status.value != firstStatus ||
                    lib.MaaStringBufferGet(entryBuffer) != expectedEntry
                ) return unknown("task_history_changed")
                val ids = idsBuffer.getLongArray(0, capacity.toInt())
                if (ids.any { it <= 0 } || ids.toSet().size != ids.size) return unknown("node_ids_invalid")
                nameBuffer = lib.MaaStringBufferCreate()?.takeIf { Pointer.nativeValue(it) != 0L }
                    ?: return unknown("node_buffer_unavailable")
                for (nodeId in ids) {
                    if (!active()) return unknown("runtime_owner_or_deadline")
                    val recoId = LongByReference(0)
                    val actionId = LongByReference(0)
                    val completed = ByteByReference(0)
                    if (lib.MaaTaskerGetNodeDetail(tasker, nodeId, nameBuffer, recoId, actionId, completed).toInt() != 1) {
                        return unknown("node_detail_unavailable")
                    }
                    val name = lib.MaaStringBufferGet(nameBuffer)
                    if (name.isNullOrBlank() || completed.value.toInt() !in 0..1) return unknown("node_detail_invalid")
                    if (name == RECOVERY_NODE) recoverySeen = true
                    // Native completed=false also describes an executed action failure. A named
                    // node in a stable terminal history is not a missing/unfinished query result.
                    if (completed.value.toInt() == 0) completedFalseCount++
                    if (!active()) return unknown("runtime_owner_or_deadline")
                }
                // Re-read the complete list, not a prefix, after every node query has finished.
                count.value = capacity
                if (lib.MaaTaskerGetTaskDetail(tasker, taskId, entryBuffer, idsBuffer, count, status).toInt() != 1) {
                    return unknown("task_detail_final_read_failed")
                }
                if (!active()) return unknown("runtime_owner_or_deadline")
                if (count.value != capacity || status.value != firstStatus ||
                    lib.MaaStringBufferGet(entryBuffer) != expectedEntry ||
                    !ids.contentEquals(idsBuffer.getLongArray(0, capacity.toInt()))
                ) return unknown("task_history_changed")
                if (!active()) return unknown("runtime_owner_or_deadline")
                if (recoverySeen) return Result(true, true, "shared_recovery_visited", firstStatus, capacity, completedFalseCount)
                if (waitStatus != firstStatus || !MaaStatus.isDone(waitStatus)) {
                    return unknown("task_completion_unconfirmed")
                }
                return Result(true, false, "no_shared_recovery", firstStatus, capacity, completedFalseCount)
            }
        } catch (_: Throwable) {
            return unknown("native_history_query_failed")
        }
        var releaseFailed = false
        val result = try {
            readHistory()
        } finally {
            // Every allocated native string buffer has exactly one matching destroy.
            for (buffer in listOfNotNull(nameBuffer, entryBuffer)) {
                try {
                    lib.MaaStringBufferDestroy(buffer)
                } catch (_: Throwable) {
                    releaseFailed = true
                }
            }
        }
        return if (releaseFailed) unknown("history_buffer_destroy_failed") else result
    }
}

/** This latch belongs to one run; it never converts a recovery into a user cancellation. */
class RecoveryDispatchGate {
    var aborted: Boolean = false
        private set

    fun success(waitStatus: Int, history: TaskRecoveryHistory.Result): Boolean {
        if (history.mustAbort) aborted = true
        return !aborted && waitStatus == MaaStatus.SUCCEEDED
    }
}

/** Native Running is a lifecycle barrier, not proof that every physical touch has cleared. */
object RecoveryNativeStopBarrier {
    data class Result(val stopped: Boolean, val interrupted: Boolean)

    fun awaitStopped(
        ownsRuntime: () -> Boolean,
        nativeRunning: () -> Byte,
        pause: (Long) -> Unit = Thread::sleep,
    ): Result {
        var interrupted = false
        while (try { ownsRuntime() } catch (_: Throwable) { false }) {
            val running = try {
                nativeRunning().toInt()
            } catch (_: Throwable) {
                // No terminal notification or handle release is permitted without a real observation.
                return Result(false, interrupted)
            }
            if (running == 0) return Result(true, interrupted)
            if (running != 1) return Result(false, interrupted)
            try {
                pause(20L)
            } catch (_: InterruptedException) {
                // Cancellation of the caller's wait cannot certify the original native thread stopped.
                interrupted = true
            } catch (_: Throwable) {
                return Result(false, interrupted)
            }
        }
        return Result(false, interrupted)
    }
}
