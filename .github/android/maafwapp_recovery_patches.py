"""Synchronous recovery-history consumption for the fixed MaaFwApp baseline."""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"

RECOVERY_PATCHES = (
    (
        "app/proguard-rules.pro",
        "# libjnidispatch 按字面名 FindClass 的那些都在顶层包（Pointer / Memory / Function /\n# CallbackReference …），子包（ptr / internal / win32）没有 native 反查，交给 R8 裁\n-keep class com.sun.jna.* { *; }\n",
        """# libjnidispatch 按字面名 FindClass 的那些都在顶层包（Pointer / Memory / Function /
# CallbackReference …）。ptr 参数另经 JNA NativeMappedConverter / Klass 反射构造，
# 保留实际历史查询使用的三个类型及 public 无参构造，其余子包仍由 R8 分析。
-keep class com.sun.jna.* { *; }
-keep class com.sun.jna.ptr.IntByReference { public <init>(); }
-keep class com.sun.jna.ptr.ByteByReference { public <init>(); }
-keep class com.sun.jna.ptr.LongByReference { public <init>(); }
""",
    ),
    (
        "app/build.gradle.kts",
        "    testImplementation(libs.junit)\n",
        "    testImplementation(libs.junit)\n    // Host unit tests exercise real JNA Memory; the production AAR carries Android natives only.\n    testRuntimeOnly(libs.jna) { artifact { type = \"jar\" } }\n",
    ),
    (
        KOTLIN + "maa/MaaFrameworkLibrary.kt",
        "import com.sun.jna.Pointer\n",
        "import com.sun.jna.Pointer\nimport com.sun.jna.ptr.ByteByReference\nimport com.sun.jna.ptr.IntByReference\nimport com.sun.jna.ptr.LongByReference\n",
    ),
    (
        KOTLIN + "maa/MaaFrameworkLibrary.kt",
        "    fun MaaTaskerWait(tasker: Pointer?, id: Long): Int\n",
        """    fun MaaTaskerWait(tasker: Pointer?, id: Long): Int

    // v5.14.2: MaaSize is uint64_t, including armeabi-v7a; MaaBool is uint8_t.
    fun MaaTaskerGetTaskDetail(
        tasker: Pointer?, taskId: Long, entry: Pointer?, nodeIdList: Pointer?,
        nodeIdListSize: LongByReference, status: IntByReference,
    ): Byte

    fun MaaTaskerGetNodeDetail(
        tasker: Pointer?, nodeId: Long, nodeName: Pointer?, recoId: LongByReference,
        actionId: LongByReference, completed: ByteByReference,
    ): Byte
""",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "    private var stopRequested = false\n",
        "    private var stopRequested = false\n    private var recoveryHistoryOwner: Any? = null\n",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "    private fun runPlan(payload: RunPlanPayload) {\n        var outcome = RunOutcome.FAILED\n        var reason = \"\"\n",
        """    private fun runPlan(payload: RunPlanPayload) {
        val historyOwner = Any()
        synchronized(lifecycleLock) { recoveryHistoryOwner = historyOwner }
        var outcome = RunOutcome.FAILED
        var reason = ""
        var recoveryAbortRuntime: Pair<MaaFrameworkLibrary, Pointer>? = null
""",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "            var anyFailed = false\n            var cancelled = false\n            payload.tasks.forEachIndexed { index, task ->\n                if (cancelled) return@forEachIndexed\n",
        """            var anyFailed = false
            var cancelled = false
            val recoveryGate = com.aliothmoon.maafw.maa.RecoveryDispatchGate()
            payload.tasks.forEachIndexed { index, task ->
                if (cancelled || recoveryGate.aborted) return@forEachIndexed
""",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "                val success = status == MaaStatus.SUCCEEDED\n",
        """                // Wait can report success after Python PostStop. Inspect this task before
                // counting success, notifying the owner, or dispatching another task.
                val history = com.aliothmoon.maafw.maa.TaskRecoveryHistory.inspect(
                    lib, currentTasker, taskId, task.entry, status,
                    ownsRuntime = {
                        synchronized(lifecycleLock) {
                            running && recoveryHistoryOwner === historyOwner &&
                                tasker == currentTasker && !stopRequested
                        }
                    },
                )
                val success = recoveryGate.success(status, history)
""",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "                notify { onTaskFinished(task.taskName, success, statusText(status), stopped) }\n",
        """                val taskMessage = "${statusText(status)}; task_id=$taskId; wait_status=$status; " +
                    "recovery_history=${history.reason}; native_status=${history.nativeStatus}" +
                    com.aliothmoon.maafw.maa.PurchaseMaintenanceNative.suffix(
                        payload.tasks.size == 1 && task.taskName == "Screencap", task.entry,
                        taskId, status, history, success, stopped,
                    ).orEmpty()
                notify { onTaskFinished(task.taskName, success, taskMessage, stopped) }
                if (history.mustAbort) {
                    reason = "recovery_history:${history.reason}; task_id=$taskId"
                    cancelled = stopped
                    if (currentTasker != null) recoveryAbortRuntime = lib to currentTasker
                    return@forEachIndexed
                }
""",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        """        } finally {
            synchronized(lifecycleLock) {
                running = false
                stopRequested = false
            }
            // agent 输出按窗口攒批，晚于终局到 app 就进不了这一轮的日志。比如 go-service 在任务开始时
""",
        """        } finally {
            // A returned Wait is not the Running barrier. Keep the original handles and
            // busy owner until this native tasker actually stops; interruption is not cleanup.
            val recoveryCleanup = recoveryAbortRuntime?.let { (lib, handle) ->
                com.aliothmoon.maafw.maa.RecoveryNativeStopBarrier.awaitStopped(
                    ownsRuntime = {
                        synchronized(lifecycleLock) {
                            running && recoveryHistoryOwner === historyOwner && tasker == handle
                        }
                    },
                    nativeRunning = { lib.MaaTaskerRunning(handle) },
                )
            }
            if (recoveryCleanup?.stopped == false) {
                Ln.w("MaaRunner: recovery cleanup unconfirmed; retaining runtime and handles")
                if (recoveryCleanup.interrupted) Thread.currentThread().interrupt()
                return
            }
            synchronized(lifecycleLock) {
                if (recoveryHistoryOwner !== historyOwner) return
                if (recoveryCleanup?.stopped == true) {
                    // The barrier already observed native Running == 0; this is the later
                    // acknowledgement time under the final owner lock, not another query.
                    reason += "; native_running_observed=false; native_idle_observed_at_ns=${System.nanoTime()}"
                }
                running = false
                if (recoveryCleanup?.stopped == true) {
                    reason += "; runner_running_cleared_at_ns=${System.nanoTime()}"
                }
                stopRequested = false
                recoveryHistoryOwner = null
            }
            // agent 输出按窗口攒批，晚于终局到 app 就进不了这一轮的日志。比如 go-service 在任务开始时
""",
    ),
    (
        KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
        """            emit(RunnerEvent.ExecutionFinished(result))
            _state.update { current ->
""",
        """            if (reason?.startsWith("recovery_history:") == true) {
                // The remote summary carries an observation made before its running flag
                // cleared. Preserve it on this execution's ordered log lane before the footer.
                emit(RunnerEvent.Log("recovery_result execution_id=$executionId; $reason"))
            }
            PurchaseMaintenanceTerminal.render(
                executionId, auditBinding?.run, plannedTasks, results, outcome, maintenanceStopped,
            )?.let { emit(RunnerEvent.Log(it)) }
            emit(RunnerEvent.ExecutionFinished(result))
            _state.update { current ->
""",
    ),
    (
        KOTLIN + "runner/RunLogRecorder.kt",
        """        if (event is RunnerEvent.ExecutionFinished) {
            session?.drained?.complete(Unit)
            return
        }
""",
        """        if (event is RunnerEvent.ExecutionFinished) {
            // Preserve the producer's result before the footer, even without debug details.
            // The envelope owns the execution; current runner state may already be another run.
            event.result.taskResults.forEachIndexed { index, result ->
                record(envelope.copy(event = RunnerEvent.Log(
                    "task_result execution_id=${envelope.executionId} result_index=$index task=${result.taskName} " +
                        "effective_success=${result.success}; ${result.message.orEmpty()}",
                )))
            }
            session?.drained?.complete(Unit)
            return
        }
""",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "            notify { onFinished(outcome, reason) }\n",
        "            notify { onFinished(outcome, reason) }\n            if (recoveryCleanup?.interrupted == true) Thread.currentThread().interrupt()\n",
    ),
)
