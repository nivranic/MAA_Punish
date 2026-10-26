package com.aliothmoon.maafw.devsession

import org.junit.Assert.*
import org.junit.Test

class DevReloadGuardTest {
    @Test fun recoveryInspectionFailureCannotReleaseConfigurationGuard() {
        assertTrue(DevReloadGuard.mustKeepBlocked { throw java.io.IOException("storage unavailable") })
        assertTrue(DevReloadGuard.mustKeepBlocked { true })
        assertFalse(DevReloadGuard.mustKeepBlocked { false })
    }
}
