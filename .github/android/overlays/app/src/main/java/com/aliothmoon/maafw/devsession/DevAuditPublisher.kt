package com.aliothmoon.maafw.devsession

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** One writer for the existing audit mailbox, shared by its poll and native dispatch. */
class DevAuditPublisher(
    private val evidence: DevAuditEvidence,
    private val ioDispatcher: CoroutineDispatcher,
    private val publishFile: suspend (String, String) -> Unit,
) {
    private val writer = Mutex()

    suspend fun publish(): DevAuditEvidence.Publication = withContext(ioDispatcher) {
        writer.withLock { publishLocked() }
    }

    suspend fun publishForDispatch(run: DevAuditRunBinding): DevAuditEvidence.Publication? =
        withContext(ioDispatcher) {
            writer.withLock {
                publishLocked().takeIf { evidence.isPublishedCurrent(run, it) }
            }
        }

    private suspend fun publishLocked(): DevAuditEvidence.Publication {
        // Snapshot inside the writer lock: an older poll cannot overwrite a later dispatch ACK.
        do {
            currentCoroutineContext().ensureActive()
            val publication = evidence.publication()
            publishFile("audit-permit.json", publication.permit)
            publishFile("audit-state.json", publication.state)
            if (evidence.unchanged(publication)) return publication
        } while (true)
    }
}
