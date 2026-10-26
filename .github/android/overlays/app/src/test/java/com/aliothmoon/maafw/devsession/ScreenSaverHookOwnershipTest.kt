package com.aliothmoon.maafw.devsession

import com.aliothmoon.maafw.domain.ControllerDefinition
import com.aliothmoon.maafw.domain.ResourceDefinition
import com.aliothmoon.maafw.domain.RunConfigurationId
import com.aliothmoon.maafw.domain.RunMode
import com.aliothmoon.maafw.runner.DiscardingRunJournal
import com.aliothmoon.maafw.runner.EngageResult
import com.aliothmoon.maafw.runner.ExecutionResult
import com.aliothmoon.maafw.runner.RunContext
import com.aliothmoon.maafw.runner.RunEndReason
import com.aliothmoon.maafw.runner.RunPlan
import com.aliothmoon.maafw.runner.RunScreenSaver
import com.aliothmoon.maafw.runner.RunTrigger
import com.aliothmoon.maafw.runner.ScreenSaverHook
import com.aliothmoon.maafw.settings.FakeAppSettingsGateway
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenSaverHookOwnershipTest {
    private val plan = RunPlan("demo", "1", ControllerDefinition(),
        ResourceDefinition("official", listOf("./base")), RunConfigurationId("config"), emptyList())

    private fun context(id: String) = RunContext(RunTrigger.Manual, RunMode.BACKGROUND,
        plan, journal = DiscardingRunJournal, executionId = id)

    @Test fun `hook teardown releases only its execution even when window already exists`() = runTest {
        val ownership = ScreenSaverOwnership()
        ownership.acquire(ScreenSaverOwner.Manual)
        ownership.setUsbSession("usb")
        ownership.acquire(ScreenSaverOwner.Usb("usb"))
        val requested = mutableListOf<String>()
        val released = mutableListOf<String>()
        val saver = object : RunScreenSaver {
            override suspend fun show(executionId: String): Boolean {
                requested += executionId
                return ownership.acquire(ScreenSaverOwner.Run(executionId))
            }
            override suspend fun hide(executionId: String) {
                released += executionId
                ownership.release(ScreenSaverOwner.Run(executionId))
            }
        }
        val settings = FakeAppSettingsGateway().apply { screenSaverEnabled.value = true }
        val hook = ScreenSaverHook(settings, saver)
        val first = hook.engage(context("first")) as EngageResult.Engaged
        val second = hook.engage(context("second")) as EngageResult.Engaged
        first.release(RunEndReason.Ran(ExecutionResult.Completed(emptyList())))
        ownership.setUsbSession(null)
        ownership.release(ScreenSaverOwner.Manual)
        assertTrue(ownership.shouldShow)
        second.release(RunEndReason.Ran(ExecutionResult.Completed(emptyList())))
        assertFalse(ownership.shouldShow)
        assertEquals(listOf("first", "second"), requested)
        assertEquals(listOf("first", "second"), released)
    }
}
