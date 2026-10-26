package com.aliothmoon.maafw.devsession

/** Main-thread policy only. A visible window is shared; releasing one source
 * never releases another source's use of that same window. */
sealed interface ScreenSaverOwner {
    data object Manual : ScreenSaverOwner
    data class Run(val executionId: String) : ScreenSaverOwner
    data class Usb(val sessionId: String) : ScreenSaverOwner
}

class ScreenSaverOwnership {
    private val owners = linkedSetOf<ScreenSaverOwner>()
    private var usbSessionId: String? = null
    private var dismissedUsbSessionId: String? = null

    val shouldShow: Boolean get() = owners.isNotEmpty()
    val usbSuppressed: Boolean get() = usbSessionId != null && usbSessionId == dismissedUsbSessionId

    fun setUsbSession(sessionId: String?) {
        if (usbSessionId == sessionId) return
        usbSessionId?.let { owners.remove(ScreenSaverOwner.Usb(it)) }
        usbSessionId = sessionId
        dismissedUsbSessionId = null
    }

    fun acquire(owner: ScreenSaverOwner): Boolean {
        if (owner is ScreenSaverOwner.Usb &&
            (owner.sessionId != usbSessionId || usbSuppressed)
        ) return false
        owners.add(owner)
        return true
    }

    fun release(owner: ScreenSaverOwner) { owners.remove(owner) }

    /** A deliberate unlock must win over repeated USB heartbeats.
     * New manual requests or a new run may open again; only a new USB lease
     * removes USB suppression. */
    fun dismissByUser() {
        owners.clear()
        dismissedUsbSessionId = usbSessionId
    }

    /** Foreground capture cannot have this overlay. This is a mode boundary,
     * rather than a user unlock, so returning to background may retry USB. */
    fun clearForUnsupportedMode() { owners.clear() }
}

/** Used by every service stop decision, including the check after throttling. */
fun needsRunForegroundService(runnerBusy: Boolean, hasUsbLease: Boolean): Boolean =
    runnerBusy || hasUsbLease
