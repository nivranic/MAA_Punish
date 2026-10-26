package com.aliothmoon.maafw.devsession

import android.app.ForegroundServiceStartNotAllowedException

/** Only the documented platform rejections are recoverable. Unexpected
 * programming failures and VM errors retain their original diagnostics. */
fun startForegroundWithDenialHandling(
    sdkInt: Int,
    start: () -> Unit,
    onDenied: (RuntimeException) -> Unit,
): Boolean {
    try {
        start()
        return true
    } catch (error: RuntimeException) {
        val denied = error is SecurityException ||
            (sdkInt >= 31 && error is ForegroundServiceStartNotAllowedException)
        if (!denied) throw error
        onDenied(error)
        return false
    }
}
