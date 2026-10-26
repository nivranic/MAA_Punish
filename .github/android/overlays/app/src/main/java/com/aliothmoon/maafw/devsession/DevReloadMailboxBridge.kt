package com.aliothmoon.maafw.devsession

import com.aliothmoon.maafw.constant.AppPaths
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import kotlin.coroutines.cancellation.CancellationException
import timber.log.Timber

/** Fixed maintenance mailbox, independent of heartbeat and close sequence spaces.
 * submit starts its own maintenance job; this bridge never awaits native work.
 * Rejections use an ack file so they cannot overwrite another operation's state.
 */
class DevReloadMailboxBridge(
    private val session: DevSessionCoordinator,
    private val reload: DevReloadCoordinator,
    private val appScope: CoroutineScope,
) {
    private var initialized = false

    fun setup() {
        if (initialized) return
        initialized = true
        appScope.launch(Dispatchers.IO) {
            val mailbox = DevSessionMailbox(AppPaths.ROOT)
            reload.state.collect { status ->
                if (status.phase != "idle") {
                    try {
                        mailbox.publish("reload-state.json", devSessionJson.encodeToString(status))
                    } catch (failure: Exception) {
                        if (failure is CancellationException) throw failure
                        Timber.w(failure, "Cannot publish source reload progress")
                    }
                }
            }
        }
        appScope.launch(Dispatchers.IO) {
            val mailbox = DevSessionMailbox(AppPaths.ROOT)
            session.binding.collectLatest { binding ->
                if (binding == null) return@collectLatest
                var previous: String? = null
                while (currentCoroutineContext().isActive) {
                    try {
                        val contents = mailbox.readRequest("reload-request.json")
                        if (contents != null && contents != previous) {
                            previous = contents
                            val request = devSessionJson.decodeFromString<DevReloadRequest>(contents)
                            val lease = session.lease.value
                            val valid = lease != null && request.version == binding.version &&
                                request.`package` == binding.`package` && request.app_instance == binding.app_instance &&
                                request.challenge == binding.challenge && request.host_id == lease.hostId &&
                                request.session_id == lease.sessionId && devToken.matches(request.request_id) &&
                                devToken.matches(request.operation_id) && request.operation in setOf("apply", "status", "cancel")
                            val result = if (valid) reload.submit(request, checkNotNull(lease)) else DevReloadStatus(
                                session_id = request.session_id, operation_id = request.operation_id,
                                request_id = request.request_id, phase = "rejected", reason = "reload_binding_invalid",
                            )
                            mailbox.publish("reload-ack.json", devSessionJson.encodeToString(result))
                        }
                    } catch (failure: Exception) {
                        if (failure is CancellationException) throw failure
                        // Invalid/stale/oversized requests never alter PI or the
                        // valid USB lease. Retry storage at a bounded log rate.
                        Timber.w(failure, "USB source request rejected or mailbox unavailable")
                        delay(2_000)
                    }
                    delay(250)
                }
            }
        }
    }
}
