package com.aliothmoon.maafw.devsession

import android.app.ForegroundServiceStartNotAllowedException
import io.mockk.mockk
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ForegroundServiceStartTest {
    @Test fun `successful foreground start does not release the lease`() {
        var started = false
        var cleaned = false
        assertTrue(startForegroundWithDenialHandling(36, { started = true }, { cleaned = true }))
        assertTrue(started)
        assertFalse(cleaned)
    }

    @Test fun `security refusal performs cleanup exactly once`() {
        val refusal = SecurityException("specialUse denied")
        val cleaned = mutableListOf<RuntimeException>()
        assertFalse(startForegroundWithDenialHandling(26, { throw refusal }, { cleaned += it }))
        assertEquals(listOf(refusal), cleaned)
    }

    @Test fun `Android 12 background start refusal performs the same cleanup`() {
        // Avoid invoking the Android SDK stub constructor in a local JVM test.
        val refusal = mockk<ForegroundServiceStartNotAllowedException>()
        val cleaned = mutableListOf<RuntimeException>()
        assertFalse(startForegroundWithDenialHandling(31, { throw refusal }, { cleaned += it }))
        assertEquals(1, cleaned.size)
        assertSame(refusal, cleaned.single())
    }

    @Test fun `unexpected runtime failure is rethrown without pretending a denial`() {
        val failure = IllegalStateException("invalid notification")
        var cleaned = false
        val actual = assertThrows(IllegalStateException::class.java) {
            startForegroundWithDenialHandling(36, { throw failure }, { cleaned = true })
        }
        assertSame(failure, actual)
        assertFalse(cleaned)
    }

    @Test fun `VM error is not swallowed or converted to a recoverable refusal`() {
        val failure = OutOfMemoryError("test only")
        var cleaned = false
        val actual = assertThrows(OutOfMemoryError::class.java) {
            startForegroundWithDenialHandling(36, { throw failure }, { cleaned = true })
        }
        assertSame(failure, actual)
        assertFalse(cleaned)
    }
}
