package com.aliothmoon.maafw.devsession

/** Pure policy shared by lifecycle callbacks and the mailbox polling job.
 * Background work may finish/expire an existing lease but cannot open a new one.
 */
class DevSessionLifetime {
    @Volatile
    var canAcceptOpen: Boolean = false
        private set

    fun enterForeground() { canAcceptOpen = true }
    fun enterBackground() { canAcceptOpen = false }
    fun shouldPoll(lease: DevSessionLease?): Boolean = canAcceptOpen || lease != null
}
