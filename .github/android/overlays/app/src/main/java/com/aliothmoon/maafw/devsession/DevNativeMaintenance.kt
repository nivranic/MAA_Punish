package com.aliothmoon.maafw.devsession

import java.util.concurrent.Executor

/** Serial native maintenance. Only callbacks on the original runner worker touch handles. */
class DevNativeMaintenance(
    private val lock: Any,
    private val worker: Executor,
    private val nowMs: () -> Long,
    private val requestStop: () -> Unit,
    private val releaseStopped: (deadline: Long, cancelled: () -> Boolean) -> Boolean,
    private val prepare: (String) -> String?,
    private val evidence: () -> DevNativeStatus,
) {
    private var current = DevNativeStatus()
    private var cancelled = false
    private var deadline = 0L
    private var busy = false
    private var workerActive = false
    private var generation = 0L

    fun isBusy(): Boolean = synchronized(lock) { busy }

    fun handle(request: DevNativeRequest): DevNativeStatus = synchronized(lock) {
        require(devToken.matches(request.operation_id)) { "invalid_maintenance_id" }
        when (request.operation) {
            "poll" -> {
                require(current.operation_id == request.operation_id) { "maintenance_token_mismatch" }
                if (workerActive && nowMs() >= deadline) cancelled = true
            }
            "begin" -> {
                require(!busy || current.operation_id == request.operation_id) { "maintenance_busy" }
                if (workerActive) return@synchronized current
                if (busy && current.phase == "stopped") return@synchronized current
                busy = true
                workerActive = true
                cancelled = false
                deadline = nowMs() + request.timeout_ms.coerceIn(1_000, 30_000)
                current = DevNativeStatus(operation_id = request.operation_id, phase = "stopping")
                requestStop()
                worker.execute { stopOnWorker(request.operation_id) }
            }
            "prepare" -> {
                require(busy && !workerActive && current.operation_id == request.operation_id && current.phase == "stopped") { "native_not_safely_stopped" }
                require(devHash.matches(request.revision) && request.payload.isNotEmpty() && request.payload.length <= 1_048_576) { "invalid_prepare_payload" }
                workerActive = true
                cancelled = false
                deadline = nowMs() + request.timeout_ms.coerceIn(1_000, 120_000)
                current = current.copy(phase = "preparing", safe_stopped = false, revision = request.revision)
                worker.execute { prepareOnWorker(request) }
            }
            "release" -> {
                require(busy && !workerActive && current.operation_id == request.operation_id && current.phase in setOf("prepared", "stopped")) { "maintenance_not_releasable" }
                busy = false
                current = current.copy(phase = "released")
            }
            "cancel" -> {
                require(current.operation_id == request.operation_id) { "maintenance_token_mismatch" }
                cancelled = true
                // Never clear a token while its worker can still be inside native code.
                if (!workerActive && current.phase == "stopped") {
                    busy = false
                    current = current.copy(phase = "released")
                }
            }
            else -> error("unknown_native_maintenance_operation")
        }
        current
    }

    private fun shouldCancel(): Boolean = synchronized(lock) { cancelled || nowMs() >= deadline }

    private fun stopOnWorker(id: String) {
        val safe = runCatching {
            if (shouldCancel()) false else releaseStopped(deadline, ::shouldCancel)
        }
        synchronized(lock) {
            workerActive = false
            // A request which timed out while queued can never become an actionable STOPPED.
            current = if (safe.getOrDefault(false) && !shouldCancel()) {
                evidence().copy(operation_id = id, phase = "stopped", safe_stopped = true)
            } else {
                current.copy(phase = "blocked", safe_stopped = false, reason = if (shouldCancel()) "stop_timeout_or_cancelled" else "native_stop_failed")
            }
        }
    }

    private fun prepareOnWorker(request: DevNativeRequest) {
        val failure = runCatching { if (shouldCancel()) "prepare_cancelled" else prepare(request.payload) }
            .fold(onSuccess = { it }, onFailure = { "native_prepare_failed" })
        synchronized(lock) {
            workerActive = false
            val failed = failure ?: if (shouldCancel()) "prepare_timeout_or_cancelled" else null
            if (failed == null) generation++
            current = evidence().copy(
                operation_id = request.operation_id,
                phase = if (failed == null) "prepared" else "blocked",
                safe_stopped = false,
                revision = request.revision,
                reason = failed.orEmpty(),
                native_generation = generation,
            )
        }
    }
}
