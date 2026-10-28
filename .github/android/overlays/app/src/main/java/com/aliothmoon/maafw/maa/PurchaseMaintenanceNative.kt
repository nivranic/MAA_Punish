package com.aliothmoon.maafw.maa

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Native observations carried by the existing onTaskFinished callback, never Agent stdout. */
object PurchaseMaintenanceNative {
    const val ENTRY = "审计购买对账"
    const val PREFIX = "; purchase_maintenance_native="

    fun suffix(
        singleScreencapPlan: Boolean,
        entry: String,
        taskId: Long,
        waitStatus: Int,
        history: TaskRecoveryHistory.Result,
        effectiveSuccess: Boolean,
        stopped: Boolean,
    ): String? {
        if (!singleScreencapPlan || entry != ENTRY || taskId <= 0 || stopped || !effectiveSuccess ||
            waitStatus != MaaStatus.SUCCEEDED || history.nativeStatus != MaaStatus.SUCCEEDED ||
            !history.complete || history.recoverySeen || history.nodeCount != 1L ||
            history.completedFalseCount != 0L || !history.singleEntryCompleted
        ) return null
        return PREFIX + buildJsonObject {
            put("entry", ENTRY)
            put("task_id", taskId)
            put("wait_status", waitStatus)
            put("native_status", history.nativeStatus)
            put("node_count", history.nodeCount)
            put("completed_false_count", history.completedFalseCount)
            put("single_entry_completed", true)
        }
    }
}
