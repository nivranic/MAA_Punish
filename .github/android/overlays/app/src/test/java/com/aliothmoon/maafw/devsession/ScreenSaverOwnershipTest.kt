package com.aliothmoon.maafw.devsession

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenSaverOwnershipTest {
    private val manual = ScreenSaverOwner.Manual
    private val run = ScreenSaverOwner.Run("run-1")
    private val usb = ScreenSaverOwner.Usb("usb-1")

    @Test fun `unplug preserves a manual screen saver`() {
        val policy = ScreenSaverOwnership()
        policy.acquire(manual)
        policy.setUsbSession("usb-1")
        policy.acquire(usb)
        policy.setUsbSession(null)
        assertTrue(policy.shouldShow)
        policy.release(manual)
        assertFalse(policy.shouldShow)
    }

    @Test fun `task completion preserves USB and unplug preserves a task`() {
        val policy = ScreenSaverOwnership()
        policy.setUsbSession("usb-1")
        policy.acquire(usb)
        policy.acquire(run)
        policy.release(run)
        assertTrue(policy.shouldShow)
        policy.acquire(run)
        policy.setUsbSession(null)
        assertTrue(policy.shouldShow)
        policy.release(run)
        assertFalse(policy.shouldShow)
    }

    @Test fun `duplicate requests and stale releases cannot steal another run`() {
        val policy = ScreenSaverOwnership()
        val next = ScreenSaverOwner.Run("run-2")
        policy.acquire(run)
        policy.acquire(run)
        policy.acquire(next)
        policy.release(run)
        policy.release(run)
        assertTrue(policy.shouldShow)
        policy.release(next)
        assertFalse(policy.shouldShow)
    }

    @Test fun `unlock suppresses USB heartbeats but permits explicit requests`() {
        val policy = ScreenSaverOwnership()
        policy.setUsbSession("usb-1")
        policy.acquire(usb)
        policy.acquire(run)
        policy.dismissByUser()
        repeat(3) {
            policy.setUsbSession("usb-1")
            assertFalse(policy.acquire(usb))
            assertFalse(policy.shouldShow)
        }
        assertTrue(policy.usbSuppressed)
        policy.acquire(manual)
        assertTrue(policy.shouldShow)
        policy.release(manual)
        policy.acquire(ScreenSaverOwner.Run("new-run"))
        assertTrue(policy.shouldShow)
    }

    @Test fun `new lease clears suppression and old USB cannot acquire or release it`() {
        val policy = ScreenSaverOwnership()
        policy.setUsbSession("usb-1")
        policy.acquire(usb)
        policy.dismissByUser()
        policy.setUsbSession(null)
        policy.setUsbSession("usb-2")
        assertFalse(policy.acquire(usb))
        assertTrue(policy.acquire(ScreenSaverOwner.Usb("usb-2")))
        policy.release(usb)
        assertTrue(policy.shouldShow)
        policy.setUsbSession(null)
        assertFalse(policy.shouldShow)
    }

    @Test fun `mode boundary clears window reasons without suppressing a lease`() {
        val policy = ScreenSaverOwnership()
        policy.setUsbSession("usb-1")
        policy.acquire(usb)
        policy.acquire(manual)
        policy.clearForUnsupportedMode()
        assertFalse(policy.shouldShow)
        assertFalse(policy.usbSuppressed)
        assertTrue(policy.acquire(usb))
    }

    @Test fun `idle service is retained only for a live USB lease`() {
        assertFalse(needsRunForegroundService(false, false))
        assertTrue(needsRunForegroundService(true, false))
        assertTrue(needsRunForegroundService(false, true))
        assertTrue(needsRunForegroundService(true, true))
    }
}
