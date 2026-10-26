package com.aliothmoon.maafw.devsession

import com.aliothmoon.maafw.domain.ControllerDefinition
import com.aliothmoon.maafw.domain.ResourceDefinition
import com.aliothmoon.maafw.domain.RunConfigurationId
import com.aliothmoon.maafw.runner.RunPlan
import com.aliothmoon.maafw.runner.RunPlanPayload
import com.aliothmoon.maafw.runner.RuntimeTask
import com.aliothmoon.maafw.runner.runPlanWireJson
import com.aliothmoon.maafw.runner.runtimePayload
import com.aliothmoon.maafw.runner.withAuditLease
import org.junit.Assert.*
import org.junit.Test
import java.io.File

class DevAuditEnvironmentTest {
    private val session = "a".repeat(32)
    private val lease = DevSessionLease(session, "b".repeat(32), auditLogging = true)
    private val spoofed = mapOf(
        "PI_CLIENT_NAME" to "keep", DevAuditEnvironment.LOG_MODE to "file",
        DevAuditEnvironment.SESSION_ID to "old", DevAuditEnvironment.PREFIX + "UNKNOWN" to "discard",
    )
    private fun plan() = RunPlan(
        "demo", "1", ControllerDefinition(), ResourceDefinition("base", listOf("resource/base")),
        RunConfigurationId("config"), listOf(RuntimeTask("task", "entry", emptyList())), piEnv = spoofed,
    )

    @Test fun `serialized runtime payload binds only an explicitly enabled lease`() {
        val payload = plan().runtimePayload(File("pi"), 1920, 1080, 1, "apk", "lib", lease)
        val decoded = runPlanWireJson.decodeFromString(RunPlanPayload.serializer(),
            runPlanWireJson.encodeToString(RunPlanPayload.serializer(), payload))
        assertEquals(mapOf("PI_CLIENT_NAME" to "keep", DevAuditEnvironment.LOG_MODE to "stdout",
            DevAuditEnvironment.SESSION_ID to session), decoded.piEnv)
        assertEquals(mapOf("PI_CLIENT_NAME" to "keep"), plan().runtimePayload(File("pi"), 1, 1, 1, "", "").piEnv)
        assertEquals(mapOf("PI_CLIENT_NAME" to "keep"), payload.withAuditLease(null).piEnv)
        assertEquals(mapOf("PI_CLIENT_NAME" to "keep"), payload.withAuditLease(lease.copy(auditLogging = false)).piEnv)
        val next = lease.copy(sessionId = "c".repeat(32))
        assertEquals(next.sessionId, payload.withAuditLease(next).piEnv[DevAuditEnvironment.SESSION_ID])
    }

    @Test fun `spawn clears inherited and descriptor settings then installs authoritative payload last`() {
        val environment = spoofed.toMutableMap()
        DevAuditEnvironment.installInProcess(environment, DevAuditEnvironment.forLease(emptyMap(), lease))
        assertEquals(mapOf("PI_CLIENT_NAME" to "keep", DevAuditEnvironment.LOG_MODE to "stdout",
            DevAuditEnvironment.SESSION_ID to session), environment)
        DevAuditEnvironment.installInProcess(environment, emptyMap())
        assertEquals(mapOf("PI_CLIENT_NAME" to "keep"), environment)
    }

    @Test fun `invalid partial or unknown audit payload fails before process environment mutation`() {
        for (payload in listOf(
            mapOf(DevAuditEnvironment.LOG_MODE to "stdout"),
            mapOf(DevAuditEnvironment.SESSION_ID to session),
            mapOf(DevAuditEnvironment.LOG_MODE to "file", DevAuditEnvironment.SESSION_ID to session),
            mapOf(DevAuditEnvironment.LOG_MODE to "stdout", DevAuditEnvironment.SESSION_ID to "../escape"),
            mapOf(DevAuditEnvironment.LOG_MODE to "stdout", DevAuditEnvironment.SESSION_ID to session,
                DevAuditEnvironment.PREFIX + "UNKNOWN" to "1"),
        )) {
            val environment = spoofed.toMutableMap()
            try {
                DevAuditEnvironment.installInProcess(environment, payload)
                fail("Invalid audit environment accepted")
            } catch (expected: IllegalArgumentException) {
                assertEquals("invalid_audit_process_environment", expected.message)
            }
            assertEquals(spoofed, environment)
        }
    }
}
