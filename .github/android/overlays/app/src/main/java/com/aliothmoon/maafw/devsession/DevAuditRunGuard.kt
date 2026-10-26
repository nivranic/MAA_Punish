package com.aliothmoon.maafw.devsession

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.atomic.AtomicBoolean

/** One immutable audit lease/execution pair. This class never issues runner commands. */
class DevAuditRunGuard(
    val lease: DevSessionLease,
    val executionId: String,
    val run: DevAuditRunBinding,
    private val evidence: DevAuditEvidence,
) {
    enum class Phase {
        Preparing, Running, StopRequested, StopAccepted, StopRejected,
        Finished, NotDispatched, Unconfirmed, TimedOut,
    }

    data class Status(val phase: Phase, val waitTimedOut: Boolean = false)

    private val mutableStatus = MutableStateFlow(Status(Phase.Preparing))
    val status: StateFlow<Status> = mutableStatus.asStateFlow()
    val ended = CompletableDeferred<Unit>()
    private val invalidated = AtomicBoolean(false)
    private val stopClaimed = AtomicBoolean(false)
    val revoked: Boolean get() = invalidated.get()
    @Volatile var dispatched: Boolean = false
        private set
    private var watcher: Job? = null

    @Synchronized fun revoke(): Boolean {
        evidence.finish(run)
        return invalidated.compareAndSet(false, true)
    }

    /** Called only at Runner's final command gate, immediately before native dispatch. */
    @Synchronized fun register(): Boolean = !revoked && evidence.register(run)
    fun claimStop(): Boolean = stopClaimed.compareAndSet(false, true)

    @Synchronized fun attach(job: Job) {
        if (ended.isCompleted) job.cancel() else watcher = job
    }

    @Synchronized fun hasWatcher(): Boolean = watcher?.isActive == true

    @Synchronized fun dispatched() {
        dispatched = true
        update(Phase.Running)
    }

    @Synchronized fun update(phase: Phase) {
        if (!ended.isCompleted) mutableStatus.value = mutableStatus.value.copy(phase = phase)
    }

    @Synchronized fun timeout() {
        if (!ended.isCompleted) mutableStatus.value = Status(Phase.TimedOut, waitTimedOut = true)
    }

    /** Only the matching Runner callback confirms Finished; Accepted is never terminal. */
    @Synchronized fun finish(phase: Phase) {
        revoke()
        if (ended.isCompleted) {
            // A later callback for this same immutable binding can resolve an
            // earlier uncertain dispatch/abort. It cannot affect another run.
            if (phase == Phase.Finished && mutableStatus.value.phase == Phase.Unconfirmed) {
                mutableStatus.value = mutableStatus.value.copy(phase = phase)
            }
            return
        }
        mutableStatus.value = mutableStatus.value.copy(phase = phase)
        ended.complete(Unit)
        watcher?.cancel()
        watcher = null
    }
}
