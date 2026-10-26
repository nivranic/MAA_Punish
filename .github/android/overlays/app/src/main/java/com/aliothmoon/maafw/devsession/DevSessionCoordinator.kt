package com.aliothmoon.maafw.devsession

import android.os.SystemClock
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import com.aliothmoon.maafw.BuildConfig
import com.aliothmoon.maafw.constant.AppPaths
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import java.util.concurrent.atomic.AtomicReference
import java.io.File
import kotlin.coroutines.cancellation.CancellationException
import timber.log.Timber

/** Opens no port/receiver. Foreground lifecycle merely starts the mailbox;
 * only an authorized USB host's valid handshake creates the temporary lease.
 * See https://developer.android.com/tools/adb#directingcommands
 */
class DevSessionCoordinator(
    private val appScope: CoroutineScope,
    private val externalRoot: File = AppPaths.ROOT,
    private val packageName: String = BuildConfig.APPLICATION_ID,
    private val nowMs: () -> Long = SystemClock::elapsedRealtime,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : DefaultLifecycleObserver {
    private val mutableLease = MutableStateFlow<DevSessionLease?>(null)
    val lease: StateFlow<DevSessionLease?> = mutableLease.asStateFlow()
    private val mutableStatus = MutableStateFlow<DevSessionStatus?>(null)
    val status: StateFlow<DevSessionStatus?> = mutableStatus.asStateFlow()
    private val mutableBinding = MutableStateFlow<DevSessionHello?>(null)
    val binding: StateFlow<DevSessionHello?> = mutableBinding.asStateFlow()
    val auditEvidence = DevAuditEvidence(packageName, File(externalRoot, ".dev-session"), nowMs,
        { mutableBinding.value }, { mutableLease.value })
    private var lastAuditRequest: String? = null
    private val closeRequest = AtomicReference<Pair<String, String>?>(null)
    private val screenSaverFeedback = AtomicReference<Pair<String, String>?>(null)
    private var registered = false
    private var pollJob: Job? = null
    private val lifetime = DevSessionLifetime()

    fun closeCurrent(sessionId: String, reason: String) {
        require(reason.length <= 128)
        closeRequest.set(sessionId to reason)
    }

    fun setScreenSaverStatus(sessionId: String, code: String) {
        require(code.length <= 128)
        if (mutableLease.value?.sessionId == sessionId) screenSaverFeedback.set(sessionId to code)
    }

    fun setup() {
        if (registered) return
        registered = true
        // Copy the existing PermissionManager lifecycle pattern. Application
        // initialization by a provider/receiver alone must not open a session.
        ProcessLifecycleOwner.get().lifecycle.addObserver(this)
    }

    override fun onResume(owner: LifecycleOwner) {
        lifetime.enterForeground()
        startPolling()
    }

    override fun onStop(owner: LifecycleOwner) {
        lifetime.enterBackground()
    }

    private fun startPolling() {
        if (pollJob?.isActive == true || !appScope.isActive) return
        pollJob = appScope.launch(ioDispatcher) {
            try {
                pollMailbox()
            } finally {
                mutableLease.value = null
                auditEvidence.invalidate()
                mutableBinding.value = null
                withContext(NonCancellable + Dispatchers.Main.immediate) {
                    pollJob = null
                    if (lifetime.canAcceptOpen) startPolling()
                }
            }
        }
    }

    private suspend fun pollMailbox() {
        val mailbox = DevSessionMailbox(externalRoot)
        val protocol = DevSessionProtocol(packageName, nowMs)
        val lastContents = mutableMapOf<String, String>()
        var publishedHello = ""
        var initialized = false
        var saverStatus = "inactive"
        var previousLease: DevSessionLease? = null
        try {
            while (currentCoroutineContext().isActive && lifetime.shouldPoll(protocol.lease)) {
                try {
                    var changed = protocol.tick()
                    closeRequest.getAndSet(null)?.let { (sessionId, reason) ->
                        if (protocol.lease?.sessionId == sessionId) {
                            protocol.end(why = reason)
                            changed = true
                        }
                    }
                    if (!initialized) { mailbox.prepare(); initialized = true; changed = true }
                    for (name in listOf("request.json", "close.json")) {
                        val request = mailbox.readRequest(name)
                        if (request != null && request != lastContents[name]) {
                            lastContents[name] = request
                            runCatching {
                                devSessionJson.decodeFromString<DevSessionRequest>(request).also {
                                    require((it.operation == "close") == (name == "close.json"))
                                    require(it.operation != "open" || lifetime.canAcceptOpen)
                                }
                            }.fold(onSuccess = { protocol.receive(it) }, onFailure = { protocol.rejectInvalid() })
                            changed = true
                        }
                    }
                    val hello = devSessionJson.encodeToString(protocol.hello())
                    if (hello != publishedHello) {
                        mutableBinding.value = protocol.hello()
                        mailbox.publish("hello.json", hello)
                        publishedHello = hello
                    }
                    if (previousLease != protocol.lease) {
                        previousLease = protocol.lease
                        saverStatus = "inactive"
                        changed = true
                    }
                    screenSaverFeedback.getAndSet(null)?.let { (sessionId, code) ->
                        if (protocol.lease?.sessionId == sessionId && saverStatus != code) {
                            saverStatus = code
                            changed = true
                        }
                    }
                    if (changed) {
                        val snapshot = protocol.status().copy(screen_saver_status = saverStatus)
                        mailbox.publish("state.json", devSessionJson.encodeToString(snapshot))
                        mutableStatus.value = snapshot
                    }
                    mutableLease.value = protocol.lease
                    pollAuditMailbox(mailbox)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    // Storage failure must immediately release, never leave a
                    // live USB lease or crash normal task/application startup.
                    protocol.end(why = "mailbox_unavailable")
                    mutableLease.value = null
                    auditEvidence.invalidate()
                    runCatching { publishAudit(mailbox) }
                    mutableStatus.value = protocol.status()
                    initialized = false
                    publishedHello = ""
                    Timber.w(error, "USB debug mailbox unavailable")
                    delay(2_000)
                }
                delay(250)
            }
        } finally {
            mutableLease.value = null
            auditEvidence.invalidate()
            runCatching { publishAudit(mailbox) }
        }
    }

    /** Shares the existing 250 ms IO poll; there is no extra scheduler or stdout reader. */
    internal fun pollAuditMailbox(mailbox: DevSessionMailbox) {
        try {
            val request = mailbox.readRequest("audit-request.json")
            if (request != null && request != lastAuditRequest) {
                lastAuditRequest = request
                auditEvidence.receive(request)
            }
        } catch (_: Exception) {
            auditEvidence.rejectInvalid()
        }
        publishAudit(mailbox)
    }

    private fun publishAudit(mailbox: DevSessionMailbox) {
        // Disk IO stays outside the Runner command gate. If a lifecycle transition
        // races this snapshot, replace it immediately before completing this poll.
        do {
            val publication = auditEvidence.publication()
            mailbox.publish("audit-permit.json", publication.permit)
            mailbox.publish("audit-state.json", publication.state)
        } while (!auditEvidence.unchanged(publication))
    }
}
