package com.aliothmoon.maafw.devsession

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class DevMaintenanceBlockedException(cause: Throwable) : IllegalStateException("maintenance_settling_blocked", cause)

/** Process-local UI/configuration guard. Native handles have a separate worker token. */
object DevReloadGuard {
    private val mutableBusy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = mutableBusy.asStateFlow()
    val recovering = AtomicBoolean(false)
    private val sourceGate = Mutex()
    fun enter() { mutableBusy.value = true }
    fun leave() { mutableBusy.value = false }
    fun requireIdle() = check(!mutableBusy.value) { "source_maintenance_in_progress" }
    fun mustKeepBlocked(recoveryCheck: () -> Boolean): Boolean = runCatching(recoveryCheck).getOrDefault(true)

    /** Recovery is waiting work, not an unpack failure (whose retry would wipe the PI). */
    suspend fun awaitReadable(root: File) {
        while (true) {
            val readable = runCatching { requireReadable(root) }.isSuccess
            if (!mutableBusy.value && readable) return
            if (!readable) mutableBusy.value = true
            busy.first { !it }
        }
    }

    /** Full source operations, not just their initial checks, share this mutex. */
    suspend fun <T> withSourceGate(block: suspend () -> T): T = sourceGate.withLock { block() }

    suspend fun <T> withInstallGate(root: File, block: suspend () -> T): T {
        while (true) {
            // Recovery itself uses the raw installer while holding sourceGate.
            // Waiting here must never own that gate.
            awaitReadable(root)
            sourceGate.lock()
            try {
                if (!mutableBusy.value && runCatching { requireReadable(root) }.isSuccess) return block()
            } finally {
                sourceGate.unlock()
            }
        }
    }

    /** UI edits and the three-store backup import must finish before a maintenance snapshot. */
    suspend fun <T : Any> withConfigurationMutation(block: suspend () -> T): T? {
        if (mutableBusy.value || !sourceGate.tryLock()) return null
        try {
            if (mutableBusy.value) return null
            return block()
        } finally {
            sourceGate.unlock()
        }
    }

    /** A killed app cannot publish or run a half-promoted project on its next launch. */
    fun requireReadable(root: File) {
        check(!recovering.get()) { "source_recovery_in_progress" }
        val sessionRoot = File(root.parentFile, ".dev-session/reload")
        check(!sessionRoot.exists() || sessionRoot.isDirectory) { "invalid_recovery_directory" }
        check(!sessionRoot.walkTopDown().maxDepth(4).onFail { _, failure -> throw failure }
            .any { it.name == "journal.json" }) { "source_recovery_required" }
    }
}
