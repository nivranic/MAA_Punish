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
    private var registeredEpoch: Long? = null
    val revoked: Boolean get() = invalidated.get()
    @Volatile var dispatched: Boolean = false
        private set
    private var watcher: Job? = null

    @Synchronized fun revoke(): Boolean {
        registeredEpoch?.let { evidence.finish(run, it) }
        return invalidated.compareAndSet(false, true)
    }

    /** Register at the command gate, then publish outside it before the final dispatch gate. */
    @Synchronized fun register(): Boolean {
        if (revoked || registeredEpoch != null) return false
        registeredEpoch = evidence.registerForDispatch(run)
        return registeredEpoch != null
    }

    @Synchronized fun publicationCurrent(publication: DevAuditEvidence.Publication): Boolean =
        !revoked && registeredEpoch != null && registeredEpoch == publication.registrationEpoch &&
            evidence.isPublishedCurrent(run, publication)
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
