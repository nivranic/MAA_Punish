package com.aliothmoon.maafw.runner

import java.io.File
import com.aliothmoon.maafw.devsession.DevAuditEnvironment
import com.aliothmoon.maafw.devsession.DevAuditRunBinding
import com.aliothmoon.maafw.devsession.DevSessionLease
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Pure shared serialization. Preparing resources never starts displays or executes tasks. */
fun RunPlan.runtimePayload(
    piRoot: File,
    width: Int,
    height: Int,
    mode: Int,
    apkPath: String,
    nativeLibraryDir: String,
    auditLease: DevSessionLease? = null,
    auditRun: DevAuditRunBinding? = null,
): RunPlanPayload = RunPlanPayload(
    resourcePaths = resourceBundlePaths().map { File(piRoot, it).absolutePath },
    screenWidth = width,
    screenHeight = height,
    displayMode = mode,
    tasks = tasks.map { task ->
        // Append after user/option patches so stale or forged attach cannot win.
        val auditOverride = buildJsonObject {
            for (node in listOf("购买", "购买_宿舍", "点击购买_宿舍", "审计购买对账")) {
                put(node, buildJsonObject {
                    put("attach", buildJsonObject {
                        put("audit_run", auditRun?.attach() ?: buildJsonObject { put("enabled", false) })
                    })
                })
            }
        }
        RuntimeTaskPayload(task.taskName, task.entry, task.pipelineOverrides + auditOverride)
    },
    agents = agents.map { AgentPayload(it.childExec, it.childArgs) },
    apkPath = apkPath,
    nativeLibraryDir = nativeLibraryDir,
    piEnv = DevAuditEnvironment.forLease(piEnv, auditLease),
)

/** Cached rollback payloads must use today's lease, never the old process's mode. */
fun RunPlanPayload.withAuditLease(lease: DevSessionLease?): RunPlanPayload =
    copy(piEnv = DevAuditEnvironment.forLease(piEnv, lease))
