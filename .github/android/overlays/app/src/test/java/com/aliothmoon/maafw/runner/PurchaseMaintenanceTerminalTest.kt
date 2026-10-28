package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.devsession.DevAuditRunBinding
import com.aliothmoon.maafw.devsession.DevSessionLease
import com.aliothmoon.maafw.maa.MaaStatus
import com.aliothmoon.maafw.maa.PurchaseMaintenanceNative
import com.aliothmoon.maafw.maa.TaskRecoveryHistory
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class PurchaseMaintenanceTerminalTest {
    private val binding = DevAuditRunBinding("pkg", "d".repeat(32), DevSessionLease("a".repeat(32), "b".repeat(32), auditLogging = true), "execution", "f".repeat(32), "/files/.dev-session")
    private val tasks = listOf("Screencap" to PurchaseMaintenanceNative.ENTRY)
    private val history = TaskRecoveryHistory.Result(true, false, "no_shared_recovery", MaaStatus.SUCCEEDED, 1, 0, true)

    private fun suffix(h: TaskRecoveryHistory.Result = history, stopped: Boolean = false) =
        PurchaseMaintenanceNative.suffix(true, PurchaseMaintenanceNative.ENTRY, 77, MaaStatus.SUCCEEDED, h, true, stopped)
    private fun results(message: String? = "SUCCESS" + suffix()) = listOf(TaskResult("Screencap", true, message))
    private fun render() = PurchaseMaintenanceTerminal.render("execution", binding, tasks, results(), RunOutcome.COMPLETED, false)

    @Test fun `terminal uses authoritative identity and exact native singleton evidence`() {
        val text = checkNotNull(render())
        assertTrue(text.startsWith(PurchaseMaintenanceTerminal.PREFIX))
        val json = Json.parseToJsonElement(text.removePrefix(PurchaseMaintenanceTerminal.PREFIX)) as JsonObject
        assertEquals(JsonObject(binding.identity()), json["maintenance_identity"])
        assertEquals(JsonPrimitive(77), json["task_id"])
        assertEquals(JsonPrimitive(true), json["single_entry_completed"])
        assertEquals(JsonPrimitive("COMPLETED"), json["outcome"])
        assertEquals(11, json.size)
    }

    @Test fun `native suffix rejects incomplete failed stopped ambiguous or foreign evidence`() {
        for (bad in listOf(
            history.copy(complete = false), history.copy(recoverySeen = true),
            history.copy(nativeStatus = MaaStatus.FAILED), history.copy(nodeCount = null),
            history.copy(nodeCount = 2), history.copy(completedFalseCount = 1),
            history.copy(singleEntryCompleted = false),
        )) assertNull(suffix(bad))
        assertNull(suffix(stopped = true))
        assertNull(PurchaseMaintenanceNative.suffix(false, PurchaseMaintenanceNative.ENTRY, 77, MaaStatus.SUCCEEDED, history, true, false))
        assertNull(PurchaseMaintenanceNative.suffix(true, "other", 77, MaaStatus.SUCCEEDED, history, true, false))
        assertNull(PurchaseMaintenanceNative.suffix(true, PurchaseMaintenanceNative.ENTRY, 0, MaaStatus.SUCCEEDED, history, true, false))
        assertNull(PurchaseMaintenanceNative.suffix(true, PurchaseMaintenanceNative.ENTRY, 77, MaaStatus.FAILED, history, true, false))
        assertNull(PurchaseMaintenanceNative.suffix(true, PurchaseMaintenanceNative.ENTRY, 77, MaaStatus.SUCCEEDED, history, false, false))
    }

    @Test fun `app requires matching final execution one planned task and successful result`() {
        assertNull(PurchaseMaintenanceTerminal.render("other", binding, tasks, results(), RunOutcome.COMPLETED, false))
        assertNull(PurchaseMaintenanceTerminal.render("execution", null, tasks, results(), RunOutcome.COMPLETED, false))
        assertNull(PurchaseMaintenanceTerminal.render("execution", binding, tasks + tasks, results(), RunOutcome.COMPLETED, false))
        assertNull(PurchaseMaintenanceTerminal.render("execution", binding, listOf("Screencap" to "other"), results(), RunOutcome.COMPLETED, false))
        assertNull(PurchaseMaintenanceTerminal.render("execution", binding, tasks, results() + results(), RunOutcome.COMPLETED, false))
        assertNull(PurchaseMaintenanceTerminal.render("execution", binding, tasks, listOf(results().single().copy(success = false)), RunOutcome.COMPLETED, false))
        assertNull(PurchaseMaintenanceTerminal.render("execution", binding, tasks, results(), RunOutcome.COMPLETED, true))
        for (outcome in listOf(RunOutcome.FAILED, RunOutcome.CANCELLED, RunOutcome.COMPLETED_WITH_FAILURES)) {
            assertNull(PurchaseMaintenanceTerminal.render("execution", binding, tasks, results(), outcome, false))
        }
    }

    @Test fun `app rejects malformed duplicate native message and typed value forgery`() {
        for (message in listOf(null, "SUCCESS", "SUCCESS" + suffix() + suffix(), "SUCCESS" + suffix() + "extra",
            ("SUCCESS" + suffix()).replace("\"single_entry_completed\":true", "\"single_entry_completed\":\"true\""),
            ("SUCCESS" + suffix()).replace("\"task_id\":77", "\"task_id\":\"77\""))) {
            assertNull(PurchaseMaintenanceTerminal.render("execution", binding, tasks, results(message), RunOutcome.COMPLETED, false))
        }
    }
}
