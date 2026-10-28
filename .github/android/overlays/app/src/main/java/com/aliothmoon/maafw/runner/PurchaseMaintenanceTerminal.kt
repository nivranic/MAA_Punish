package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.devsession.DevAuditRunBinding
import com.aliothmoon.maafw.maa.MaaStatus
import com.aliothmoon.maafw.maa.PurchaseMaintenanceNative
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/** Produces a post-return observation on Runner's ordered log lane, before ExecutionFinished. */
object PurchaseMaintenanceTerminal {
    const val PREFIX = "purchase_maintenance_terminal "

    fun render(
        executionId: String,
        binding: DevAuditRunBinding?,
        plannedTasks: List<Pair<String, String>>,
        results: List<TaskResult>,
        outcome: Int,
        stopped: Boolean,
    ): String? {
        return runCatching {
        if (binding == null || binding.executionId != executionId || !binding.lease.auditLogging ||
            stopped || outcome != RunOutcome.COMPLETED ||
            plannedTasks != listOf("Screencap" to PurchaseMaintenanceNative.ENTRY)
        ) return@runCatching null
        val result = results.singleOrNull()?.takeIf { it.taskName == "Screencap" && it.success } ?: return@runCatching null
        val message = result.message ?: return@runCatching null
        if (message.indexOf(PurchaseMaintenanceNative.PREFIX) != message.lastIndexOf(PurchaseMaintenanceNative.PREFIX)) return@runCatching null
        val offset = message.indexOf(PurchaseMaintenanceNative.PREFIX)
        if (offset < 0) return@runCatching null
        val native = Json.parseToJsonElement(message.substring(offset + PurchaseMaintenanceNative.PREFIX.length)) as? JsonObject ?: return@runCatching null
        if (native.keys != setOf("entry", "task_id", "wait_status", "native_status", "node_count", "completed_false_count", "single_entry_completed")) return@runCatching null
        fun number(key: String) = (native[key] as? JsonPrimitive)?.takeUnless { it.isString }?.longOrNull
        if (native["entry"] != JsonPrimitive(PurchaseMaintenanceNative.ENTRY) || number("task_id")?.let { it > 0 } != true ||
            number("wait_status") != MaaStatus.SUCCEEDED.toLong() || number("native_status") != MaaStatus.SUCCEEDED.toLong() ||
            number("node_count") != 1L || number("completed_false_count") != 0L ||
            (native["single_entry_completed"] as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull != true
        ) return@runCatching null
        PREFIX + buildJsonObject {
            put("version", 1)
            put("maintenance_identity", JsonObject(binding.identity()))
            native.forEach { (key, value) -> put(key, value) }
            put("effective_success", true)
            put("outcome", "COMPLETED")
        }
        }.getOrNull()
    }
}
