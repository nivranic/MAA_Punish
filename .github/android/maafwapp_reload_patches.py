"""Safe source reload additions for the fixed MaaFwApp bc55dac baseline."""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"

NATIVE_MEMBERS = '''
    private var devPreviousController: Pointer? = null
    private var devAgentGeneration = 0L
    private var devLastPayload: RunPlanPayload? = null
    private val devMaintenance = com.aliothmoon.maafw.devsession.DevNativeMaintenance(
        lock = lifecycleLock,
        worker = worker,
        nowMs = { System.nanoTime() / 1_000_000 },
        requestStop = { stop() },
        releaseStopped = ::devReleaseStopped,
        prepare = ::devPrepare,
        evidence = {
            com.aliothmoon.maafw.devsession.DevNativeStatus(
                agent_generation = devAgentGeneration,
                display_id = boundDisplayId ?: -1,
                controller_reused = controller != null && controller == devPreviousController,
                prepared_payload = devLastPayload?.let { runPlanWireJson.encodeToString(RunPlanPayload.serializer(), it.copy(tasks = emptyList())) }.orEmpty(),
            )
        },
    )

    fun isDevMaintaining(): Boolean = devMaintenance.isBusy()

    fun devReload(request: com.aliothmoon.maafw.devsession.DevNativeRequest): com.aliothmoon.maafw.devsession.DevNativeStatus =
        devMaintenance.handle(request)

    private fun devReleaseStopped(deadline: Long, cancelled: () -> Boolean): Boolean {
        val lib = MaaFrameworkLoader.library ?: return resource == null && tasker == null && agents.isEmpty()
        val handle = synchronized(lifecycleLock) { tasker }
        if (handle != null) {
            lib.MaaTaskerPostStop(handle)
            while (lib.MaaTaskerRunning(handle).toInt() != 0) {
                if (cancelled() || System.nanoTime() / 1_000_000 >= deadline) return false
                Thread.sleep(TASKER_STOP_POLL_MILLIS)
            }
        }
        if (cancelled()) return false
        // Do not take/destroy handles until the native Running barrier is proven.
        devPreviousController = controller
        releaseResource(lib)
        return !cancelled()
    }

    private fun devPrepare(json: String): String? {
        val lib = MaaFrameworkLoader.library ?: return "framework_not_loaded"
        val payload = runPlanWireJson.decodeFromString<RunPlanPayload>(json)
        if (payload.tasks.isNotEmpty()) return "maintenance_cannot_run_tasks"
        val old = devLastPayload ?: return "display_not_previously_prepared"
        if (controller == null || lib.MaaControllerConnected(controller).toInt() == 0 ||
            old.displayMode != payload.displayMode || old.screenWidth != payload.screenWidth ||
            old.screenHeight != payload.screenHeight || old.apkPath != payload.apkPath ||
            old.nativeLibraryDir != payload.nativeLibraryDir
        ) return "controller_environment_changed"
        val display = if (payload.displayMode == DisplayMode.PRIMARY) PrimaryDisplayManager.DISPLAY_ID
            else VirtualDisplayManager.getDisplayId()
        if (display != boundDisplayId) return "display_environment_changed"
        val root = projectRoot?.let { java.io.File(it) }?.canonicalFile ?: return "project_root_missing"
        if (payload.resourcePaths.any { !java.io.File(it).canonicalFile.toPath().startsWith(root.toPath()) }) {
            return "resource_path_outside_pi"
        }
        val failed = prepare(lib, payload)
        return if (failed == null) null else "resource_or_agent_prepare_failed"
    }
'''

LAUNCHER_MAINTENANCE = '''
    private val devActiveContext = AtomicReference<RunContext?>(null)

    /** Holds the real launch gate and completes every ordinary Release before source writes. */
    suspend fun <T> withDevMaintenance(block: suspend () -> T): T {
        check(gate.tryLock()) { "launch_in_progress" }
        try {
            devActiveContext.get()?.devMaintenanceRequested?.set(true)
            runnerPort.stop()
            try {
                withTimeout(15_000) { settling.get()?.join() }
            } catch (failure: TimeoutCancellationException) {
                throw com.aliothmoon.maafw.devsession.DevMaintenanceBlockedException(failure)
            }
            return block()
        } finally {
            gate.unlock()
        }
    }
'''

RELOAD_PATCHES = (
    (
        "app/src/test/java/com/aliothmoon/maafw/config/backup/ConfigBackupServiceTest.kt",
        "    @Test\n    fun `运行中不许导入，什么都不写`() = runTest {",
        '''    @Test
    fun `源码维护中即使RunnerIdle也不许导入`() = runTest {
        com.aliothmoon.maafw.devsession.DevReloadGuard.enter()
        try {
            assertEquals(ConfigApplyResult.Locked, service.apply(restoreImport()))
            assertEquals(initial, store.current)
            coVerify(exactly = 0) { scheduleStore.replaceAll(any()) }
            coVerify(exactly = 0) { appSettings.importPortableSettings(any()) }
        } finally {
            com.aliothmoon.maafw.devsession.DevReloadGuard.leave()
        }
    }

    @Test
    fun `运行中不许导入，什么都不写`() = runTest {''',
    ),
    (
        "app/src/test/java/com/aliothmoon/maafw/privileged/FakePrivilegedService.kt",
        "    override fun asBinder(): IBinder? = null\n",
        '    override fun asBinder(): IBinder? = null\n    override fun devReload(requestJson: String?): String = """{"operation_id":"","phase":"rejected","reason":"maintenance_not_supported_by_fake"}"""\n',
    ),
    (
        KOTLIN + "privileged/RemoteServiceManager.kt",
        "    override fun unbind() {\n        synchronized(lock) {",
        "    override fun unbind() {\n        if (com.aliothmoon.maafw.devsession.DevReloadGuard.busy.value) return\n        synchronized(lock) {",
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "    private val destroyed = AtomicBoolean(false)\n",
        '''    private val destroyed = AtomicBoolean(false)
    private val devServiceLock = Any()
    private inline fun <T> devDisplayMutation(rejected: T, block: () -> T): T = synchronized(devServiceLock) {
        if (destroyed.get() || runner.isDevMaintaining()) rejected else block()
    }
''',
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "    override fun destroy() {\n        if (!destroyed.compareAndSet(false, true)) return",
        "    override fun destroy() = destroyRemote(force = false)\n\n    private fun destroyRemote(force: Boolean) {\n        synchronized(devServiceLock) {\n            if (!force && runner.isDevMaintaining()) return\n            if (!destroyed.compareAndSet(false, true)) return\n        }",
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        '                    Ln.w("$TAG: app process (pid=$pid) gone, destroying remote service")\n                    destroy()',
        '                    Ln.w("$TAG: app process (pid=$pid) gone, destroying remote service")\n                    destroyRemote(force = true)',
    ),
    (
        KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
        '''        val payload = RunPlanPayload(
            resourcePaths = plan.resourceBundlePaths().map { File(piRoot, it).absolutePath },
            screenWidth = width,
            screenHeight = height,
            displayMode = mode.displayMode,
            tasks = plan.tasks.map {
                RuntimeTaskPayload(
                    taskName = it.taskName,
                    entry = it.entry,
                    pipelineOverrides = it.pipelineOverrides,
                )
            },
            agents = plan.agents.map { AgentPayload(it.childExec, it.childArgs) },
            apkPath = apkPath,
            nativeLibraryDir = nativeLibraryDir,
            piEnv = plan.piEnv,
        )''',
        '''        val payload = plan.runtimePayload(piRoot, width, height, mode.displayMode, apkPath, nativeLibraryDir, auditBinding?.lease, auditBinding?.run)''',
    ),
    (
        KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
        "    private val textInputSink: () -> ITextInputSink,\n",
        "    private val textInputSink: () -> ITextInputSink,\n    private val devSessionLease: StateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?> = MutableStateFlow(null),\n    private val devAuditEvidence: com.aliothmoon.maafw.devsession.DevAuditEvidence? = null,\n",
    ),
    (
        KOTLIN + "di/RunnerModule.kt",
        "            textInputSink = { get<AccessibilityTextInputSink>() },\n",
        "            textInputSink = { get<AccessibilityTextInputSink>() },\n            devSessionLease = get<com.aliothmoon.maafw.devsession.DevSessionCoordinator>().lease,\n            devAuditEvidence = get<com.aliothmoon.maafw.devsession.DevSessionCoordinator>().auditEvidence,\n",
    ),
    (
        KOTLIN + "remote/ExecAgentHost.kt",
        '        Ln.i("ExecAgentHost: launching $command (cwd=${request.workingDir})")',
        '        com.aliothmoon.maafw.devsession.DevAuditEnvironment.installInProcess(builder.environment(), request.piEnv)\n\n        Ln.i("ExecAgentHost: launching $command (cwd=${request.workingDir})")',
    ),
    (
        "app/src/test/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPortTest.kt",
        "        debugMode: () -> Boolean = { false },\n",
        "        debugMode: () -> Boolean = { false },\n        devSessionLease: kotlinx.coroutines.flow.StateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?> = kotlinx.coroutines.flow.MutableStateFlow(null),\n        devAuditEvidence: com.aliothmoon.maafw.devsession.DevAuditEvidence? = auditEvidence(devSessionLease),\n",
    ),
    (
        "app/src/test/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPortTest.kt",
        "            textInputSink = { textInputSink },\n",
        "            textInputSink = { textInputSink },\n            devSessionLease = devSessionLease,\n            devAuditEvidence = devAuditEvidence,\n",
    ),
    (
        "app/src/test/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPortTest.kt",
        "    @Test\n    fun `saveOnError is read per run and pushed to the service`() = runTest(dispatcher) {",
        '''    @Test
    fun `actual start payload reads audit lease each run and clears stale reserved keys`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(null)
        val (runner, _) = port(this, service, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        val prefix = com.aliothmoon.maafw.devsession.DevAuditEnvironment.PREFIX
        val mode = com.aliothmoon.maafw.devsession.DevAuditEnvironment.LOG_MODE
        val sessionKey = com.aliothmoon.maafw.devsession.DevAuditEnvironment.SESSION_ID
        val runPlan = plan().copy(piEnv = mapOf("PI_CLIENT_NAME" to "keep", mode to "file", sessionKey to "stale", prefix + "EXTRA" to "discard"))
        for ((index, selected) in listOf(
            com.aliothmoon.maafw.devsession.DevSessionLease("a".repeat(32), "b".repeat(32), true),
            com.aliothmoon.maafw.devsession.DevSessionLease("c".repeat(32), "b".repeat(32), false),
            null,
        ).withIndex()) {
            lease.value = selected
            assertEquals(RunnerCommandResult.Accepted, runner.start(runPlan, "audit-$index"))
            advanceUntilIdle()
            val payload = runPlanWireJson.decodeFromString(RunPlanPayload.serializer(), service.lastRunPlanJson!!)
            val expected = if (selected?.auditLogging == true)
                mapOf("PI_CLIENT_NAME" to "keep", mode to "stdout", sessionKey to selected.sessionId)
                else mapOf("PI_CLIENT_NAME" to "keep")
            assertEquals(expected, payload.piEnv)
            callbacks.last().onFinished(RunOutcome.COMPLETED, null)
        }
    }

    @Test
    fun `saveOnError is read per run and pushed to the service`() = runTest(dispatcher) {''',
    ),
    (
        "app/src/main/aidl/com/aliothmoon/maafw/RemoteService.aidl",
        "    boolean setPackageNetworkingEnabled(String packageName, boolean enabled) = 84;\n}",
        "    boolean setPackageNetworkingEnabled(String packageName, boolean enabled) = 84;\n\n    /** Fixed private maintenance protocol; never executes tasks or destroys the display. */\n    String devReload(String requestJson) = 86;\n}",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "    private val callbackRef = AtomicReference<IMaaRunnerCallback?>()\n",
        "    private val callbackRef = AtomicReference<IMaaRunnerCallback?>()\n" + NATIVE_MEMBERS,
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "            if (running) {\n                Ln.w(\"MaaRunner: already running\")",
        "            if (running || devMaintenance.isBusy()) {\n                Ln.w(\"MaaRunner: already running or maintaining source\")",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "            return registerAgentSinks()?.let { PrepareFailure(it) }",
        "            val sinkFailure = registerAgentSinks()?.let { PrepareFailure(it) }\n            if (sinkFailure == null) devLastPayload = payload\n            return sinkFailure",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "        agents = started\n        loadedAgents = payload.agents",
        "        agents = started\n        devAgentGeneration++\n        loadedAgents = payload.agents\n        loadedAuditEnvironment = auditEnvironment",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "    private var loadedAgents: List<AgentPayload> = emptyList()\n",
        '''    private var loadedAgents: List<AgentPayload> = emptyList()
    /** Python reads audit settings once at process start; only a matching environment may reuse it. */
    @Volatile
    private var loadedAuditEnvironment: Map<String, String>? = null
''',
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "        val reusable = loadedAgents == payload.agents &&\n",
        '''        val auditEnvironment = mutableMapOf<String, String>()
        try {
            // Use the exact validated process-start projection; unrelated PI_* changes do not restart agents.
            com.aliothmoon.maafw.devsession.DevAuditEnvironment.installInProcess(auditEnvironment, payload.piEnv)
        } catch (failure: IllegalArgumentException) {
            return agentLaunchFailed(failure.message.orEmpty())
        }
        val reusable = loadedAuditEnvironment == auditEnvironment && loadedAgents == payload.agents &&
''',
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "    fun onAgentExited(exit: AgentExit) {\n        notify { onAgentExited",
        "    fun onAgentExited(exit: AgentExit) {\n        loadedAuditEnvironment = null\n        notify { onAgentExited",
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        '''    private fun agentLaunchFailed(detail: String, index: Int? = null) =
        PrepareFailure(detail, RunOutcome.AGENT_LAUNCH_FAILED, index?.toString().orEmpty())''',
        '''    private fun agentLaunchFailed(detail: String, index: Int? = null): PrepareFailure {
        loadedAuditEnvironment = null
        return PrepareFailure(detail, RunOutcome.AGENT_LAUNCH_FAILED, index?.toString().orEmpty())
    }''',
    ),
    (
        KOTLIN + "remote/MaaRunner.kt",
        "    private fun releaseAgents() {\n        agents.forEach { releaseAgent(it) }",
        "    private fun releaseAgents() {\n        loadedAuditEnvironment = null\n        agents.forEach { releaseAgent(it) }",
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "    override fun isRunning(): Boolean = runner.isRunning()\n",
        '''    override fun isRunning(): Boolean = runner.isRunning()

    override fun devReload(requestJson: String?): String {
        return try {
            require(!requestJson.isNullOrBlank() && requestJson.length <= 1_048_576)
            val request = com.aliothmoon.maafw.devsession.devSessionJson.decodeFromString<com.aliothmoon.maafw.devsession.DevNativeRequest>(requestJson)
            if (request.operation == "begin") stopDisplayWatchers()
            val status = synchronized(devServiceLock) {
                check(!destroyed.get()) { "remote_service_destroying" }
                runner.devReload(request)
            }
            com.aliothmoon.maafw.devsession.devSessionJson.encodeToString(com.aliothmoon.maafw.devsession.DevNativeStatus.serializer(), status)
        } catch (failure: Exception) {
            com.aliothmoon.maafw.devsession.devSessionJson.encodeToString(com.aliothmoon.maafw.devsession.DevNativeStatus.serializer(),
                com.aliothmoon.maafw.devsession.DevNativeStatus(phase = "rejected", reason = "invalid_or_conflicting_maintenance"))
        }
    }
''',
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        '''    override fun setVirtualDisplayMode(mode: Int): Boolean = when (mode) {
        DisplayMode.PRIMARY -> {
            GameFpsMonitor.stop()
            VirtualDisplayManager.stop()
            virtualDisplayMode.set(mode)
            true
        }

        DisplayMode.BACKGROUND -> {
            PrimaryDisplayManager.stop()
            virtualDisplayMode.set(mode)
            true
        }

        else -> false
    }''',
        '''    override fun setVirtualDisplayMode(mode: Int): Boolean = devDisplayMutation(false) {
        when (mode) {
            DisplayMode.PRIMARY -> {
                GameFpsMonitor.stop()
                VirtualDisplayManager.stop()
                virtualDisplayMode.set(mode)
                true
            }
            DisplayMode.BACKGROUND -> {
                PrimaryDisplayManager.stop()
                virtualDisplayMode.set(mode)
                true
            }
            else -> false
        }
    }''',
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "    override fun setVirtualDisplayResolution(width: Int, height: Int, dpi: Int) {\n        VirtualDisplayManager.setResolution(width, height, dpi)\n    }",
        "    override fun setVirtualDisplayResolution(width: Int, height: Int, dpi: Int) = devDisplayMutation(Unit) {\n        VirtualDisplayManager.setResolution(width, height, dpi)\n    }",
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "    override fun stopVirtualDisplay() {\n        stopDisplayWatchers()",
        "    override fun stopVirtualDisplay() = devDisplayMutation(Unit) {\n        stopDisplayWatchers()",
    ),
    (
        KOTLIN + "runner/RunContext.kt",
        "    val stoppedAtLimit = AtomicBoolean(false)\n",
        "    val stoppedAtLimit = AtomicBoolean(false)\n    /** This execution was explicitly taken over for source maintenance. */\n    val devMaintenanceRequested = AtomicBoolean(false)\n",
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        "    private val gate = Mutex()\n",
        "    private val gate = Mutex()\n" + LAUNCHER_MAINTENANCE,
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        "        if (!gate.tryLock()) {\n            return RunLaunchResult.Blocked",
        "        if (com.aliothmoon.maafw.devsession.DevReloadGuard.busy.value || !gate.tryLock()) {\n            return RunLaunchResult.Blocked",
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        "                RunnerCommandResult.Accepted -> Unit\n",
        "                RunnerCommandResult.Accepted -> devActiveContext.set(ctx)\n",
    ),
    (
        KOTLIN + "runner/EnvironmentHooks.kt",
        "        return EngageResult.Engaged(Release { reason ->\n            // 投递被拒或用户手动停时把人家的应用关掉",
        "        return EngageResult.Engaged(Release { reason ->\n            if (ctx.devMaintenanceRequested.get()) return@Release\n            // 投递被拒或用户手动停时把人家的应用关掉",
    ),
    (
        KOTLIN + "runner/EnvironmentHooks.kt",
        "        return EngageResult.Engaged(Release { reason ->\n            when {\n                reason !is RunEndReason.Ran",
        "        return EngageResult.Engaged(Release { reason ->\n            if (ctx.devMaintenanceRequested.get()) return@Release\n            when {\n                reason !is RunEndReason.Ran",
    ),
    (
        KOTLIN + "project/ProjectRepository.kt",
        "    suspend fun reload()\n",
        '''    suspend fun reload()
    suspend fun <T> withSourceMaintenance(block: suspend () -> T): T = block()
    suspend fun publishMaintenance(result: ProjectLoadResult.Ready) { error("maintenance_not_supported") }
''',
    ),
    (
        KOTLIN + "project/ProjectRepository.kt",
        "    private val reloadMutex = Mutex()\n",
        "    private val reloadMutex = Mutex()\n    private val sourceMutex = Mutex()\n\n    override suspend fun <T> withSourceMaintenance(block: suspend () -> T): T = sourceMutex.withLock { block() }\n\n    override suspend fun publishMaintenance(result: ProjectLoadResult.Ready) {\n        reloadMutex.withLock {\n            ++reloadGeneration\n            _state.value = ProjectState.Ready(result.definition, result.diagnostics)\n        }\n    }\n",
    ),
    (
        KOTLIN + "project/ProjectRepository.kt",
        '''    override suspend fun reload() {
        val generation = reloadMutex.withLock { ++reloadGeneration }
        _state.value = ProjectState.Loading
        // 段名与 StartupBenchmark.PROJECT_LOAD_SECTION 是一对，改一处要改两处
        val next = withContext(MaaDispatchers.IO) {
            trace(PROJECT_LOAD_TRACE) {
                when (val result = loader.load()) {
                    is ProjectLoadResult.Ready -> ProjectState.Ready(result.definition, result.diagnostics)
                    is ProjectLoadResult.Failure -> ProjectState.Error(result.diagnostics)
                }
            }
        }
        // 仅最新一次 reload 写回，避免慢请求覆盖新结果
        reloadMutex.withLock {
            if (generation == reloadGeneration) {
                _state.value = next
            }
        }
    }''',
        '''    override suspend fun reload() = com.aliothmoon.maafw.devsession.DevReloadGuard.withSourceGate {
        // Keep the entire loader read mutually exclusive with source replacement.
        sourceMutex.withLock {
            com.aliothmoon.maafw.devsession.DevReloadGuard.requireIdle()
            val generation = reloadMutex.withLock { ++reloadGeneration }
            _state.value = ProjectState.Loading
            // 段名与 StartupBenchmark.PROJECT_LOAD_SECTION 是一对，改一处要改两处
            val next = withContext(MaaDispatchers.IO) {
                trace(PROJECT_LOAD_TRACE) {
                    when (val result = loader.load()) {
                        is ProjectLoadResult.Ready -> ProjectState.Ready(result.definition, result.diagnostics)
                        is ProjectLoadResult.Failure -> ProjectState.Error(result.diagnostics)
                    }
                }
            }
            // 仅最新一次 reload 写回，避免慢请求覆盖新结果
            reloadMutex.withLock {
                if (generation == reloadGeneration) {
                    _state.value = next
                }
            }
        }
    }''',
    ),
    (
        KOTLIN + "project/ProjectSource.kt",
        "    override fun list(path: String): List<String> = delegate.list(path)\n\n    override fun read(path: String): String = delegate.read(path)\n\n    override fun tryReadText(path: String): Result<String?> {",
        '''    override fun list(path: String): List<String> {
        com.aliothmoon.maafw.devsession.DevReloadGuard.requireReadable(installer.installedDir())
        return delegate.list(path)
    }

    override fun read(path: String): String {
        com.aliothmoon.maafw.devsession.DevReloadGuard.requireReadable(installer.installedDir())
        return delegate.read(path)
    }

    override fun tryReadText(path: String): Result<String?> {
        val root = catchProjectReadFailure { installer.installedDir() }
            .getOrElse { return Result.failure(it) }
        com.aliothmoon.maafw.devsession.DevReloadGuard.requireReadable(root)''',
    ),
    (
        KOTLIN + "project/PiInstallCoordinator.kt",
        "            withContext(MaaDispatchers.IO) {\n                // 逐条目写状态",
        "            withContext(MaaDispatchers.IO) {\n                com.aliothmoon.maafw.devsession.DevReloadGuard.withInstallGate(java.io.File(com.aliothmoon.maafw.constant.AppPaths.ROOT, \"pi\")) {\n                // 逐条目写状态",
    ),
    (
        KOTLIN + "project/PiInstallCoordinator.kt",
        "                    if (force) installer.reinstall(onProgress) else installer.ensureInstalled(onProgress)\n                }\n            }",
        "                    if (force) installer.reinstall(onProgress) else installer.ensureInstalled(onProgress)\n                }\n                }\n            }",
    ),
    (
        KOTLIN + "project/PiInstallCoordinator.kt",
        "    suspend fun reinstall(): Boolean = install(force = true)",
        "    suspend fun reinstall(): Boolean {\n        com.aliothmoon.maafw.devsession.DevReloadGuard.requireIdle()\n        return install(force = true)\n    }",
    ),
    (
        KOTLIN + "session/SessionContracts.kt",
        "    val remoteAccessGranting: Boolean = false,",
        "    val remoteAccessGranting: Boolean = false,\n    val sourceMaintenance: Boolean = false,",
    ),
    (
        KOTLIN + "session/SessionContracts.kt",
        "        get() = runner.phase.isBusy\n",
        "        get() = runner.phase.isBusy || sourceMaintenance\n",
    ),
    (
        KOTLIN + "session/SessionViewModel.kt",
        "        .combine(piInstall.state) { base, install -> base.copy(piInstallState = install) }",
        "        .combine(piInstall.state) { base, install -> base.copy(piInstallState = install) }\n        .combine(com.aliothmoon.maafw.devsession.DevReloadGuard.busy) { base, busy -> base.copy(sourceMaintenance = busy) }",
    ),
    (
        KOTLIN + "session/SessionViewModel.kt",
        "        block()\n    }\n\n    private fun locked(): Boolean",
        "        if (com.aliothmoon.maafw.devsession.DevReloadGuard.withConfigurationMutation { block(); true } != true) {\n            emitEffect(SessionEffect.ShowMessage(uiTextOf(R.string.msg_locked_while_running)))\n        }\n    }\n\n    private suspend fun guardedInstall(block: suspend () -> Unit) {\n        if (locked()) {\n            emitEffect(SessionEffect.ShowMessage(uiTextOf(R.string.msg_locked_while_running)))\n            return\n        }\n        block()\n    }\n\n    private fun locked(): Boolean",
    ),
    (
        KOTLIN + "session/SessionViewModel.kt",
        "SessionIntent.ReloadProject -> guarded {",
        "SessionIntent.ReloadProject -> guardedInstall {",
    ),
    (
        KOTLIN + "session/SessionViewModel.kt",
        "SessionIntent.ReinstallPi -> guarded {",
        "SessionIntent.ReinstallPi -> guardedInstall {",
    ),
    (
        KOTLIN + "session/SessionViewModel.kt",
        "    private fun locked(): Boolean = runnerPort.state.value.phase.isBusy",
        "    private fun locked(): Boolean = runnerPort.state.value.phase.isBusy || com.aliothmoon.maafw.devsession.DevReloadGuard.busy.value",
    ),
    (
        KOTLIN + "config/backup/ConfigBackupService.kt",
        "        if (runnerPort.state.value.phase.isBusy) return@withContext ConfigApplyResult.Locked\n        val definition = definition() ?: return@withContext ConfigApplyResult.ProjectNotReady",
        "        com.aliothmoon.maafw.devsession.DevReloadGuard.withConfigurationMutation {\n        if (runnerPort.state.value.phase.isBusy) return@withConfigurationMutation ConfigApplyResult.Locked\n        val definition = definition() ?: return@withConfigurationMutation ConfigApplyResult.ProjectNotReady",
    ),
    (
        KOTLIN + "config/backup/ConfigBackupService.kt",
        "        }.onFailure { Timber.e(it, \"Failed to import configuration\") }.getOrDefault(ConfigApplyResult.Failed)\n    }",
        "        }.onFailure { Timber.e(it, \"Failed to import configuration\") }.getOrDefault(ConfigApplyResult.Failed)\n        } ?: ConfigApplyResult.Locked\n    }",
    ),
)

# Audit cancellation is bound to one lease and one actual runner execution.
RELOAD_PATCHES += (
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''import kotlinx.coroutines.CancellationException
''',
        '''import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.withLock
import com.aliothmoon.maafw.devsession.DevAuditRunGuard
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''    private val _state = MutableStateFlow(RunnerState())
''',
        '''    private val _state = MutableStateFlow(RunnerState())

    // The launcher gate still owns orchestration. This gate only serializes final native
    // dispatch and expected-execution stop, including public stop's IO dispatcher hop.
    private val devRunCommandGate = kotlinx.coroutines.sync.Mutex()
    @Volatile internal var devLastAuditRun: DevAuditRunGuard? = null
        private set

    private fun watchDevAuditLease(binding: DevAuditRunGuard) {
        binding.attach(scope.launch(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) {
            devSessionLease.first { it != binding.lease }
            stopExecution(binding.executionId, binding)
            if (kotlinx.coroutines.withTimeoutOrNull(15_000) { binding.ended.await(); true } != true) {
                binding.timeout()
            }
        })
    }

    private suspend fun stopAfterFailedAuditStart(binding: DevAuditRunGuard?) {
        if (binding == null || !binding.dispatched || binding.ended.isCompleted) return
        withContext(kotlinx.coroutines.NonCancellable) { stopExecution(binding.executionId, binding) }
    }

    internal suspend fun stopDevAuditRun(binding: DevAuditRunGuard): RunnerCommandResult =
        stopExecution(binding.executionId, binding)

    private suspend fun stopExecution(
        executionId: String,
        auditBinding: DevAuditRunGuard? = null,
    ): RunnerCommandResult = withContext(MaaDispatchers.IO) {
        devRunCommandGate.withLock {
            val current = _state.value
            if (!current.phase.isBusy || current.activeExecution?.executionId != executionId ||
                (auditBinding != null && devLastAuditRun !== auditBinding)
            ) return@withLock RunnerCommandResult.Accepted
            auditBinding?.revoke()
            if (auditBinding != null && !auditBinding.claimStop()) return@withLock RunnerCommandResult.Accepted
            _state.update { state ->
                if (state.activeExecution?.executionId == executionId) state.copy(phase = RunnerPhase.Stopping) else state
            }
            devLastAuditRun?.takeIf { it.executionId == executionId }?.revoke()
            auditBinding?.update(DevAuditRunGuard.Phase.StopRequested)
            // The pending start observes the permanent revocation at the same gate.
            if (auditBinding != null && !auditBinding.dispatched) return@withLock RunnerCommandResult.Accepted
            runCatching {
                val accepted = servicePort.serviceOrNull()?.stopRun()
                if (auditBinding != null) check(accepted == true) { "audit_stop_not_accepted" }
            }.fold(
                onSuccess = {
                    auditBinding?.update(DevAuditRunGuard.Phase.StopAccepted)
                    RunnerCommandResult.Accepted
                },
                onFailure = {
                    auditBinding?.update(DevAuditRunGuard.Phase.StopRejected)
                    Timber.w(it, "Failed to stop bound run")
                    RunnerCommandResult.Rejected(uiTextOf(R.string.msg_reject_stop_failed))
                },
            )
        }
    }
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''    override suspend fun start(plan: RunPlan, executionId: String): RunnerCommandResult {
        if (_state.value.phase.isBusy) {
            return RunnerCommandResult.Rejected(uiTextOf(R.string.msg_reject_already_running))
        }
        _state.value = RunnerState(
            phase = RunnerPhase.Preparing,
            activeExecution = ActiveExecution(
                executionId = executionId,
                runConfigurationId = plan.runConfigurationId,
                currentTaskName = null,
                completedTaskCount = 0,
                totalTaskCount = plan.tasks.size,
                taskResults = emptyList(),
                taskLabels = plan.taskLabelMap(),
            ),
            latestResult = null,
        )

        return withContext(MaaDispatchers.IO) {
            try {
                connectFailure()?.let { (reason, error) ->
                    return@withContext failPreparation(reason, executionId, error)
                }
                val rejection = launchOnService(plan, executionId)
                if (rejection != null) return@withContext failPreparation(rejection, executionId)
                // Stop 可能在 Preparing 窗口里已经把 phase 打成 Stopping，甚至 onFinished 已收回 Idle
                // 无条件写成 Running 会把停止意图丢掉，任务继续跑到结束
                val accepted = _state.updateAndGet { current ->
                    if (current.phase == RunnerPhase.Preparing) {
                        current.copy(phase = RunnerPhase.Running)
                    } else {
                        current
                    }
                }
                if (accepted.phase == RunnerPhase.Idle) {
                    RunnerCommandResult.Rejected(
                        accepted.latestResult.let { result ->
                            (result as? ExecutionResult.Failed)?.reason
                                ?: uiTextOf(R.string.msg_fail_default)
                        },
                    )
                } else {
                    RunnerCommandResult.Accepted
                }
            } catch (cancellation: CancellationException) {
                failPreparation(uiTextOf(R.string.msg_fail_default), executionId)
                throw cancellation
            } catch (throwable: Throwable) {
                Timber.e(throwable, "Failed to start run")
                failPreparation(
                    throwable.remoteAccessMessageOrNull()
                        ?: uiTextFromFramework(throwable.message ?: throwable.javaClass.simpleName),
                    executionId,
                    error = throwable,
                )
            }
        }
    }

''',
        '''    override suspend fun start(plan: RunPlan, executionId: String): RunnerCommandResult {
        val auditBinding = devRunCommandGate.withLock {
            if (_state.value.phase.isBusy) {
                return RunnerCommandResult.Rejected(uiTextOf(R.string.msg_reject_already_running))
            }
            val binding = devSessionLease.value?.takeIf { it.auditLogging }?.let {
                val evidence = devAuditEvidence
                    ?: return RunnerCommandResult.Rejected(uiTextOf(R.string.msg_reject_service_rejected))
                val run = evidence.binding(it, executionId, com.aliothmoon.maafw.devsession.DevAuditEvidence.newNonce())
                    ?: return RunnerCommandResult.Rejected(uiTextOf(R.string.msg_reject_service_rejected))
                DevAuditRunGuard(it, executionId, run, evidence)
            }
            devLastAuditRun = binding
            _state.value = RunnerState(
                phase = RunnerPhase.Preparing,
                activeExecution = ActiveExecution(
                    executionId = executionId,
                    runConfigurationId = plan.runConfigurationId,
                    currentTaskName = null,
                    completedTaskCount = 0,
                    totalTaskCount = plan.tasks.size,
                    taskResults = emptyList(),
                    taskLabels = plan.taskLabelMap(),
                ),
                latestResult = null,
            )
            binding
        }
        auditBinding?.let(::watchDevAuditLease)

        // Catch outside withContext: cancellation can happen before IO is entered.
        return try {
            withContext(MaaDispatchers.IO) {
                connectFailure()?.let { (reason, error) ->
                    return@withContext failPreparation(reason, executionId, error, auditBinding)
                }
                val rejection = launchOnService(plan, executionId, auditBinding)
                if (rejection != null) return@withContext failPreparation(rejection, executionId, auditBinding = auditBinding)
                val accepted = _state.updateAndGet { current ->
                    if (current.phase == RunnerPhase.Preparing && current.activeExecution?.executionId == executionId) {
                        current.copy(phase = RunnerPhase.Running)
                    } else {
                        current
                    }
                }
                if (accepted.phase == RunnerPhase.Idle) {
                    RunnerCommandResult.Rejected(
                        accepted.latestResult.let { result ->
                            (result as? ExecutionResult.Failed)?.reason ?: uiTextOf(R.string.msg_fail_default)
                        },
                    )
                } else {
                    RunnerCommandResult.Accepted
                }
            }
        } catch (cancellation: CancellationException) {
            stopAfterFailedAuditStart(auditBinding)
            failPreparation(uiTextOf(R.string.msg_fail_default), executionId, auditBinding = auditBinding)
            throw cancellation
        } catch (throwable: Throwable) {
            stopAfterFailedAuditStart(auditBinding)
            Timber.e(throwable, "Failed to start run")
            failPreparation(
                throwable.remoteAccessMessageOrNull()
                    ?: uiTextFromFramework(throwable.message ?: throwable.javaClass.simpleName),
                executionId,
                error = throwable,
                auditBinding = auditBinding,
            )
        }
    }

''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''    override suspend fun stop(): RunnerCommandResult {
        val phase = _state.value.phase
        if (!phase.isBusy) {
            // 幂等：没在跑也算受理，UI 不必先查状态
            return RunnerCommandResult.Accepted
        }
        _state.update { it.copy(phase = RunnerPhase.Stopping) }
        return withContext(MaaDispatchers.IO) {
            runCatching { servicePort.serviceOrNull()?.stopRun() }
                .fold(
                    onSuccess = { RunnerCommandResult.Accepted },
                    onFailure = {
                        Timber.w(it, "Failed to stop run")
                        RunnerCommandResult.Rejected(if (it.message.isNullOrBlank()) uiTextOf(R.string.msg_reject_stop_failed) else uiTextFromFramework(it.message))
                    },
                )
        }
    }

''',
        '''    override suspend fun stop(): RunnerCommandResult {
        val executionId = _state.value.activeExecution?.executionId ?: return RunnerCommandResult.Accepted
        return stopExecution(executionId)
    }

''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '    private suspend fun launchOnService(plan: RunPlan, executionId: String): UiText? {',
        '    private suspend fun launchOnService(plan: RunPlan, executionId: String, auditBinding: DevAuditRunGuard?): UiText? {',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        'prepareAndStart(plan, piRoot, service, executionId)',
        'prepareAndStart(plan, piRoot, service, executionId, auditBinding)',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''    private fun prepareAndStart(
''',
        '''    private suspend fun prepareAndStart(
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''        service: RemoteService,
        executionId: String,
    ): UiText? {''',
        '''        service: RemoteService,
        executionId: String,
        auditBinding: DevAuditRunGuard?,
    ): UiText? {''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''        bindRunnerCallback(service, ExecutionCallback(executionId, plan.taskLabelMap()))
''',
        '''        return devRunCommandGate.withLock {
        if (_state.value.activeExecution?.executionId != executionId ||
            (auditBinding != null && (auditBinding.revoked || devSessionLease.value != auditBinding.lease))
        ) {
            auditBinding?.revoke()
            return@withLock uiTextOf(R.string.msg_reject_service_rejected)
        }
        bindRunnerCallback(service, ExecutionCallback(executionId, plan.taskLabelMap(), auditBinding))
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''        if (!service.startRun(runPlanWireJson.encodeToString(payload))) {
            return uiTextOf(R.string.msg_reject_service_rejected)
        }''',
        '''        if (auditBinding != null && !auditBinding.register()) {
            auditBinding.revoke()
            return@withLock uiTextOf(R.string.msg_reject_service_rejected)
        }
        auditBinding?.dispatched()
        if (!service.startRun(runPlanWireJson.encodeToString(payload))) {
            auditBinding?.finish(DevAuditRunGuard.Phase.NotDispatched)
            return@withLock uiTextOf(R.string.msg_reject_service_rejected)
        }''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''        return null
    }

    private fun shouldRetryStop''',
        '''        null
        }
    }

    private fun shouldRetryStop''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''        private val taskLabels: Map<String, String>,
    ) {''',
        '''        private val taskLabels: Map<String, String>,
        private val auditBinding: DevAuditRunGuard? = null,
    ) {''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''        fun onFinished(outcome: Int, reason: String?) {
''',
        '''        fun onFinished(outcome: Int, reason: String?) {
            auditBinding?.finish(DevAuditRunGuard.Phase.Finished)
            if (!ownsAuditRun()) return
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''            previous.activeExecution?.let {
                val result''',
        '''            previous.activeExecution?.let {
                devLastAuditRun?.takeIf { binding -> binding.executionId == it.executionId }
                    ?.finish(DevAuditRunGuard.Phase.Unconfirmed)
                val result''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''    private fun failPreparation(reason: UiText, executionId: String, error: Throwable? = null): RunnerCommandResult {
''',
        '''    private fun failPreparation(reason: UiText, executionId: String, error: Throwable? = null, auditBinding: DevAuditRunGuard? = null): RunnerCommandResult {
        if (devLastAuditRun !== auditBinding) return RunnerCommandResult.Rejected(reason, error)
        val audit = auditBinding
        val auditMayStillRun = audit?.dispatched == true && audit.status.value.phase != DevAuditRunGuard.Phase.NotDispatched
        audit?.finish(if (auditMayStillRun) DevAuditRunGuard.Phase.Unconfirmed else DevAuditRunGuard.Phase.NotDispatched)
        // A failed/cancelled Binder return after dispatch is not native termination.
        if (auditMayStillRun && _state.value.activeExecution?.executionId == executionId) {
            return RunnerCommandResult.Rejected(reason, error)
        }
''',
    ),
    (
        'app/src/main/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPort.kt',
        '''            if (current.phase == RunnerPhase.Preparing) {
                RunnerState(phase = RunnerPhase.Idle, latestResult = ExecutionResult.Failed(reason))''',
        '''            if (current.phase == RunnerPhase.Preparing && current.activeExecution?.executionId == executionId) {
                RunnerState(phase = RunnerPhase.Idle, latestResult = ExecutionResult.Failed(reason))''',
    ),
    (
        'app/src/test/java/com/aliothmoon/maafw/runner/MaaFrameworkRunnerPortTest.kt',
        '''    @Test
    fun `stop during prepare is retried after start returns`() = runTest(dispatcher) {''',
        '''    @Test
    fun `cancellation before start enters IO still releases its audit watcher`() = runTest(dispatcher) {
        every { MaaDispatchers.IO } returns StandardTestDispatcher(testScheduler, name = "separate-io")
        val service = FakePrivilegedService()
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        val started = async(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) { runner.start(plan(), "audit") }
        val binding = checkNotNull(runner.devLastAuditRun)
        started.cancel()
        testScheduler.runCurrent()
        assertTrue(binding.ended.isCompleted)
        assertFalse(binding.hasWatcher())
        assertEquals(RunnerPhase.Idle, runner.state.value.phase)
        assertEquals(null, service.lastRunPlanJson)
    }

    @Test
    fun `cancellation after native dispatch requests bound stop without inventing terminal state`() = runTest(dispatcher) {
        every { MaaDispatchers.IO } returns StandardTestDispatcher(testScheduler, name = "separate-io")
        var cancelStart: () -> Unit = {}
        val service = object : FakePrivilegedService() {
            override fun startRun(runPlanJson: String?): Boolean {
                val result = super.startRun(runPlanJson)
                cancelStart()
                return result
            }
        }
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        val started = async { runner.start(plan(), "audit") }
        cancelStart = { started.cancel() }
        testScheduler.runCurrent()
        val binding = checkNotNull(runner.devLastAuditRun)
        assertEquals(1, service.stopRunCount)
        assertEquals(RunnerPhase.Stopping, runner.state.value.phase)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.Unconfirmed, binding.status.value.phase)
        assertFalse(binding.hasWatcher())
        callbacks.single().onFinished(RunOutcome.CANCELLED, null)
        assertEquals(RunnerPhase.Idle, runner.state.value.phase)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.Finished, binding.status.value.phase)
    }

    @Test
    fun `native dispatch observes registered run and final authoritative attach without execution environment`() = runTest(dispatcher) {
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val evidence = auditEvidence(lease)
        var observedRunning = false
        val service = object : FakePrivilegedService() {
            override fun startRun(runPlanJson: String?): Boolean {
                observedRunning = evidence.publication().state.contains("\\\"phase\\\":\\\"running\\\"")
                return super.startRun(runPlanJson)
            }
        }
        val (runner, _) = port(this, service, devSessionLease = lease, devAuditEvidence = evidence)
        val (callbacks, _) = recordCallbacks(runner)
        val forged = kotlinx.serialization.json.Json.parseToJsonElement("""{"购买":{"attach":{"audit_run":{"enabled":true,"execution_id":"forged"}}}}""") as kotlinx.serialization.json.JsonObject
        val task = plan().tasks.single().copy(pipelineOverrides = listOf(forged))
        assertEquals(RunnerCommandResult.Accepted, runner.start(plan().copy(tasks = listOf(task)), "first"))
        assertTrue(observedRunning)
        val payload = runPlanWireJson.decodeFromString<RunPlanPayload>(checkNotNull(service.lastRunPlanJson))
        val finalPatch = payload.tasks.single().pipelineOverrides.last().toString()
        val old = checkNotNull(runner.devLastAuditRun)
        assertTrue(finalPatch.contains(old.run.runNonce))
        assertTrue(finalPatch.contains("\\\"execution_id\\\":\\\"first\\\""))
        assertFalse(payload.piEnv.values.contains("first"))
        callbacks.single().onFinished(RunOutcome.COMPLETED, null)
        assertTrue(evidence.publication().state.contains("\\\"phase\\\":\\\"idle\\\""))
        runner.start(plan(), "first")
        val current = checkNotNull(runner.devLastAuditRun)
        assertFalse(current.run.runNonce == old.run.runNonce)
        callbacks.first().onTaskStarted("stale-task", 5, 9)
        callbacks.first().onFinished(RunOutcome.CANCELLED, null)
        assertEquals(null, runner.state.value.activeExecution?.currentTaskName)
        assertEquals(RunnerPhase.Running, runner.state.value.phase)
        assertTrue(evidence.publication().state.contains(current.run.runNonce))
        assertTrue(evidence.publication().state.contains("\\\"phase\\\":\\\"running\\\""))
        runner.stop()
        assertTrue(evidence.publication().state.contains("\\\"phase\\\":\\\"idle\\\""))
        callbacks.last().onFinished(RunOutcome.CANCELLED, null)
        lease.value = null
        runner.start(plan().copy(tasks = listOf(task)), "ordinary")
        val ordinary = runPlanWireJson.decodeFromString<RunPlanPayload>(checkNotNull(service.lastRunPlanJson))
        assertTrue(ordinary.tasks.single().pipelineOverrides.last().toString().contains("\\\"enabled\\\":false"))
        callbacks.last().onFinished(RunOutcome.COMPLETED, null)
    }

    @Test
    fun `rejected native dispatch revokes registered authority`() = runTest(dispatcher) {
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val evidence = auditEvidence(lease)
        val service = FakePrivilegedService().apply { startRunResult = false }
        val (runner, _) = port(this, service, devSessionLease = lease, devAuditEvidence = evidence)
        assertTrue(runner.start(plan(), "rejected") is RunnerCommandResult.Rejected)
        assertTrue(evidence.publication().state.contains("\\\"phase\\\":\\\"idle\\\""))
        assertTrue(evidence.publication().permit.contains("\\\"phase\\\":\\\"revoked\\\""))
    }

    @Test
    fun `missing audit authority fails closed before dispatch`() = runTest(dispatcher) {
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (unbound, _) = port(this, FakePrivilegedService(), devSessionLease = lease, devAuditEvidence = null)
        assertTrue(unbound.start(plan(), "unbound") is RunnerCommandResult.Rejected)
        assertEquals(null, unbound.devLastAuditRun)
    }

    private fun auditEvidence(lease: kotlinx.coroutines.flow.StateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>) =
        com.aliothmoon.maafw.devsession.DevAuditEvidence("pkg", File(AppPaths.ROOT, ".dev-session"), { 1_000L },
            { com.aliothmoon.maafw.devsession.DevSessionHello(1, "pkg", "d".repeat(32), "e".repeat(32), 16_384) },
            { lease.value })

    private fun auditLease(session: String = "a") = com.aliothmoon.maafw.devsession.DevSessionLease(
        session.repeat(32), "b".repeat(32), auditLogging = true,
    )

    @Test
    fun `audit lease loss stops its active run once and waits for its finish`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        runner.start(plan(), "audit")
        val binding = checkNotNull(runner.devLastAuditRun)
        lease.value = auditLease()
        testScheduler.runCurrent()
        assertEquals(0, service.stopRunCount)
        lease.value = null
        testScheduler.runCurrent()
        assertEquals(1, service.stopRunCount)
        assertEquals(RunnerPhase.Stopping, runner.state.value.phase)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.StopAccepted, binding.status.value.phase)
        assertFalse(binding.ended.isCompleted)
        lease.value = auditLease("c")
        testScheduler.runCurrent()
        runner.stopDevAuditRun(binding)
        assertEquals(1, service.stopRunCount)
        callbacks.single().onFinished(RunOutcome.CANCELLED, null)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.Finished, binding.status.value.phase)
        assertFalse(binding.hasWatcher())
    }

    @Test
    fun `late bound stop and old finish cannot stop the next ordinary run`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        runner.start(plan(), "audit")
        val old = checkNotNull(runner.devLastAuditRun)
        val oldCallback = callbacks.single()
        oldCallback.onFinished(RunOutcome.COMPLETED, null)
        lease.value = null
        runner.start(plan(), "ordinary")
        old.revoke()
        runner.stopDevAuditRun(old)
        oldCallback.onFinished(RunOutcome.CANCELLED, null)
        testScheduler.runCurrent()
        assertEquals(0, service.stopRunCount)
        assertEquals("ordinary", runner.state.value.activeExecution?.executionId)
        assertEquals(RunnerPhase.Running, runner.state.value.phase)
        assertFalse(old.hasWatcher())
    }

    @Test
    fun `preparing audit revocation is permanent and never dispatches after a new lease`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val hold = CompletableDeferred<Unit>()
        val servicePort = FakePrivilegedServicePort(service).apply { holdUseService = hold }
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, servicePort, devSessionLease = lease)
        val started = async { runner.start(plan(), "audit") }
        testScheduler.runCurrent()
        val binding = checkNotNull(runner.devLastAuditRun)
        assertEquals(RunnerPhase.Preparing, runner.state.value.phase)
        lease.value = null
        testScheduler.runCurrent()
        lease.value = auditLease("c")
        hold.complete(Unit)
        testScheduler.runCurrent()
        assertTrue(started.await() is RunnerCommandResult.Rejected)
        assertEquals(null, service.lastRunPlanJson)
        assertEquals(0, service.stopRunCount)
        assertTrue(binding.revoked)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.NotDispatched, binding.status.value.phase)
        assertFalse(binding.hasWatcher())
    }

    @Test
    fun `ordinary lease loss does not install an audit watcher or stop a run`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease().copy(auditLogging = false))
        val (runner, _) = port(this, service, devSessionLease = lease)
        runner.start(plan(), "ordinary")
        lease.value = null
        testScheduler.runCurrent()
        assertEquals(null, runner.devLastAuditRun)
        assertEquals(0, service.stopRunCount)
        assertEquals(RunnerPhase.Running, runner.state.value.phase)
    }

    @Test
    fun `final dispatch rechecks audit lease even before its watcher runs`() = runTest(dispatcher) {
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val service = object : FakePrivilegedService() {
            override fun startVirtualDisplay(): Int {
                lease.value = null
                return 1
            }
        }
        val (runner, _) = port(this, service, devSessionLease = lease)
        assertTrue(runner.start(plan(), "audit") is RunnerCommandResult.Rejected)
        assertEquals(null, service.lastRunPlanJson)
        assertEquals(0, service.stopRunCount)
        assertFalse(checkNotNull(runner.devLastAuditRun).hasWatcher())
    }

    @Test
    fun `lease opened during ordinary preparation cannot change that run payload`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val hold = CompletableDeferred<Unit>()
        val servicePort = FakePrivilegedServicePort(service).apply { holdUseService = hold }
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(null)
        val (runner, _) = port(this, service, servicePort, devSessionLease = lease)
        val started = async { runner.start(plan(), "ordinary") }
        testScheduler.runCurrent()
        lease.value = auditLease()
        hold.complete(Unit)
        testScheduler.runCurrent()
        assertEquals(RunnerCommandResult.Accepted, started.await())
        val payload = runPlanWireJson.decodeFromString(RunPlanPayload.serializer(), service.lastRunPlanJson!!)
        assertFalse(payload.piEnv.keys.any { it.startsWith(com.aliothmoon.maafw.devsession.DevAuditEnvironment.PREFIX) })
        assertEquals(null, runner.devLastAuditRun)
    }

    @Test
    fun `audit stop accepted times out unconfirmed and a later own finish retains timeout evidence`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        runner.start(plan(), "audit")
        val binding = checkNotNull(runner.devLastAuditRun)
        lease.value = null
        testScheduler.runCurrent()
        testScheduler.advanceTimeBy(15_001)
        testScheduler.runCurrent()
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.TimedOut, binding.status.value.phase)
        assertFalse(binding.ended.isCompleted)
        assertFalse(binding.hasWatcher())
        assertEquals(RunnerPhase.Stopping, runner.state.value.phase)
        callbacks.single().onFinished(RunOutcome.CANCELLED, null)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.Finished, binding.status.value.phase)
        assertTrue(binding.status.value.waitTimedOut)
    }

    @Test
    fun `preparation cancellation releases audit watcher and later lease loss does nothing`() = runTest(dispatcher) {
        val service = FakePrivilegedService()
        val hold = CompletableDeferred<Unit>()
        val servicePort = FakePrivilegedServicePort(service).apply { holdUseService = hold }
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, servicePort, devSessionLease = lease)
        val started = async { runner.start(plan(), "audit") }
        testScheduler.runCurrent()
        val binding = checkNotNull(runner.devLastAuditRun)
        started.cancel()
        testScheduler.runCurrent()
        assertTrue(binding.ended.isCompleted)
        assertFalse(binding.hasWatcher())
        lease.value = null
        testScheduler.runCurrent()
        assertEquals(0, service.stopRunCount)
        assertEquals(null, service.lastRunPlanJson)
    }

    @Test
    fun `finish racing inside audit stop is terminal and cannot be overwritten by acceptance`() = runTest(dispatcher) {
        var finish: () -> Unit = {}
        val service = object : FakePrivilegedService() {
            override fun stopRun(): Boolean {
                finish()
                return super.stopRun()
            }
        }
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        runner.start(plan(), "audit")
        val binding = checkNotNull(runner.devLastAuditRun)
        finish = { callbacks.single().onFinished(RunOutcome.CANCELLED, null) }
        lease.value = null
        testScheduler.runCurrent()
        assertEquals(1, service.stopRunCount)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.Finished, binding.status.value.phase)
        assertEquals(RunnerPhase.Idle, runner.state.value.phase)
        assertFalse(binding.hasWatcher())
    }

    @Test
    fun `public stop rechecks execution after its IO hop before touching a newer run`() = runTest(dispatcher) {
        every { MaaDispatchers.IO } returns StandardTestDispatcher(testScheduler, name = "separate-io")
        val service = FakePrivilegedService()
        val (runner, _) = port(this, service)
        val (callbacks, _) = recordCallbacks(runner)
        runner.start(plan(), "old")
        val stopped = async(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) { runner.stop() }
        callbacks.single().onFinished(RunOutcome.COMPLETED, null)
        runner.start(plan(), "new")
        stopped.await()
        assertEquals(0, service.stopRunCount)
        assertEquals("new", runner.state.value.activeExecution?.executionId)
        assertEquals(RunnerPhase.Running, runner.state.value.phase)
    }

    @Test
    fun `previous audit finish cannot confirm a newer audit binding`() = runTest(dispatcher) {
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, devSessionLease = lease)
        val (callbacks, _) = recordCallbacks(runner)
        runner.start(plan(), "old")
        val oldCallback = callbacks.single()
        oldCallback.onFinished(RunOutcome.COMPLETED, null)
        lease.value = auditLease("c")
        runner.start(plan(), "new")
        val binding = checkNotNull(runner.devLastAuditRun)
        oldCallback.onFinished(RunOutcome.CANCELLED, null)
        assertFalse(binding.ended.isCompleted)
        assertTrue(binding.hasWatcher())
        assertEquals("new", runner.state.value.activeExecution?.executionId)
        callbacks.last().onFinished(RunOutcome.COMPLETED, null)
    }

    @Test
    fun `native start rejection releases its audit watcher`() = runTest(dispatcher) {
        val service = FakePrivilegedService().apply { startRunResult = false }
        val lease = kotlinx.coroutines.flow.MutableStateFlow<com.aliothmoon.maafw.devsession.DevSessionLease?>(auditLease())
        val (runner, _) = port(this, service, devSessionLease = lease)
        assertTrue(runner.start(plan(), "audit") is RunnerCommandResult.Rejected)
        val binding = checkNotNull(runner.devLastAuditRun)
        assertEquals(com.aliothmoon.maafw.devsession.DevAuditRunGuard.Phase.NotDispatched, binding.status.value.phase)
        assertFalse(binding.hasWatcher())
        lease.value = null
        testScheduler.runCurrent()
        assertEquals(0, service.stopRunCount)
    }

    @Test
    fun `stop during prepare is retried after start returns`() = runTest(dispatcher) {''',
    ),
)

# Retain actual native execution ownership when start returns uncertain.
RELOAD_PATCHES += (
    (
        KOTLIN + "runner/RunLauncher.kt",
        '''    private val handled = ArrayDeque<RunRequestId>()
''',
        '''    private val handled = ArrayDeque<RunRequestId>()

    private val devDeferredExecution = AtomicReference<String?>(null)

    /** A failed dispatch can still own native work. Keep its Releases and barrier. */
    private fun deferDevSettlement(ctx: RunContext?, engaged: ArrayDeque<Release>, label: String): Boolean {
        if (ctx == null) return false
        val current = runnerPort.state.value
        if (!current.phase.isBusy || current.activeExecution?.executionId != ctx.executionId) return false
        val pending = engaged.toList()
        engaged.clear()
        devActiveContext.set(ctx)
        devDeferredExecution.set(ctx.executionId)
        settling.set(scope.launch {
            try {
                awaitSettledThenFinalize(pending, label)
            } finally {
                devDeferredExecution.compareAndSet(ctx.executionId, null)
            }
        })
        return true
    }

    private suspend fun awaitDevDeferredSettlement(): Boolean {
        if (devDeferredExecution.get() == null) return true
        if (runnerPort.state.value.phase.isBusy) return false
        return try {
            withTimeout(15_000) { settling.get()?.join() }
            true
        } catch (_: TimeoutCancellationException) {
            false
        }
    }
''',
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        '''        val engaged = ArrayDeque<Release>()
        try {
''',
        '''        val engaged = ArrayDeque<Release>()
        var devDispatchContext: RunContext? = null
        try {
            if (!awaitDevDeferredSettlement()) return RunLaunchResult.Blocked(uiTextOf(R.string.msg_launch_in_progress))
''',
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        '            when (val command = runnerPort.start(plan, ctx.executionId)) {',
        '''            devDispatchContext = ctx
            when (val command = runnerPort.start(plan, ctx.executionId)) {''',
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        '''                    finalize(engaged, RunEndReason.NotRun(NotRunCause.Rejected, command.reason, command.error))
''',
        '''                    if (!deferDevSettlement(ctx, engaged, logLabel(trigger, configurationId))) {
                        finalize(engaged, RunEndReason.NotRun(NotRunCause.Rejected, command.reason, command.error))
                    }
''',
    ),
    (
        KOTLIN + "runner/RunLauncher.kt",
        '''            finalize(engaged, RunEndReason.NotRun(NotRunCause.Cancelled))
            throw cancellation''',
        '''            if (!deferDevSettlement(devDispatchContext, engaged, logLabel(trigger, configurationId))) {
                finalize(engaged, RunEndReason.NotRun(NotRunCause.Cancelled))
            }
            throw cancellation''',
    ),
)

RELOAD_PATCHES += (
    ("app/src/test/java/com/aliothmoon/maafw/runner/RunLauncherTest.kt", "import kotlinx.coroutines.CoroutineScope\n", "import kotlinx.coroutines.CoroutineScope\nimport kotlinx.coroutines.launch\n"),
    ("app/src/test/java/com/aliothmoon/maafw/runner/RunLauncherTest.kt", '    private class RecordingTree : Timber.Tree() {',
        '''    private class UnconfirmedDispatchRunner(
        val delegate: RecordingEventRunnerPort = RecordingEventRunnerPort(),
    ) : RunnerPort by delegate {
        var reject = true
        var cancel = false
        var dispatch = true
        override suspend fun start(plan: RunPlan, executionId: String): RunnerCommandResult {
            delegate.start(plan, executionId)
            if (dispatch) delegate.setState(RunnerState(
                phase = if (reject || cancel) RunnerPhase.Stopping else RunnerPhase.Running,
                activeExecution = ActiveExecution(executionId, plan.runConfigurationId, null, 0, plan.tasks.size, emptyList(), plan.taskLabelMap()),
            ))
            if (cancel) throw kotlinx.coroutines.CancellationException("dispatch cancelled")
            return if (reject) RunnerCommandResult.Rejected(uiTextOf(R.string.msg_fail_default)) else RunnerCommandResult.Accepted
        }
        fun finish() = delegate.setState(RunnerState(phase = RunnerPhase.Idle, latestResult = ExecutionResult.Cancelled(emptyList())))
    }

    @Test
    fun `unconfirmed rejected dispatch keeps release and maintenance barrier until own finish`() = runTest(testDispatcher) {
        val runner = UnconfirmedDispatchRunner()
        val hook = RecordingHook("env", Anchor.BeforeDispatch)
        val launcher = launcher(backgroundScope, runner, hooks = listOf(hook))
        assertTrue(launcher.launch(RunTrigger.Manual) is RunLaunchResult.Rejected)
        assertNull(hook.releaseReason)
        assertEquals(RunnerPhase.Stopping, runner.state.value.phase)
        var maintained = false
        val maintenance = backgroundScope.launch { launcher.withDevMaintenance { maintained = true } }
        testScheduler.runCurrent()
        assertTrue(!maintained)
        assertNull(hook.releaseReason)
        runner.finish()
        testScheduler.runCurrent()
        maintenance.join()
        assertTrue(maintained)
        assertTrue(hook.releaseReason is RunEndReason.Ran)
        assertEquals(1, hook.log.count { it == "release:env" })
        runner.reject = false
        assertEquals(RunLaunchResult.Started, launcher.launch(RunTrigger.Manual))
        runner.finish()
        testScheduler.runCurrent()
        assertEquals(2, hook.log.count { it == "release:env" })
    }

    @Test
    fun `cancelled dispatched launch retains cleanup ownership until its run finishes`() = runTest(testDispatcher) {
        val runner = UnconfirmedDispatchRunner().apply { cancel = true }
        val hook = RecordingHook("env", Anchor.BeforeDispatch)
        val launcher = launcher(backgroundScope, runner, hooks = listOf(hook))
        val launched = backgroundScope.launch { launcher.launch(RunTrigger.Manual) }
        testScheduler.runCurrent()
        assertTrue(launched.isCancelled)
        assertNull(hook.releaseReason)
        assertTrue(launcher.launch(RunTrigger.Manual) is RunLaunchResult.Blocked)
        assertEquals(1, runner.delegate.startedExecutionIds.size)
        runner.finish()
        testScheduler.runCurrent()
        assertTrue(hook.releaseReason is RunEndReason.Ran)
        assertEquals(1, hook.log.count { it == "release:env" })
    }

    @Test
    fun `cancelled undispatched launch releases immediately without leaving settlement ownership`() = runTest(testDispatcher) {
        val runner = UnconfirmedDispatchRunner().apply { cancel = true; dispatch = false }
        val hook = RecordingHook("env", Anchor.BeforeDispatch)
        val launcher = launcher(backgroundScope, runner, hooks = listOf(hook))
        backgroundScope.launch { launcher.launch(RunTrigger.Manual) }
        testScheduler.runCurrent()
        assertEquals(RunEndReason.NotRun(NotRunCause.Cancelled), hook.releaseReason)
        assertEquals(1, hook.log.count { it == "release:env" })
        var maintained = false
        launcher.withDevMaintenance { maintained = true }
        assertTrue(maintained)
    }

    @Test
    fun `next ordinary launch waits for deferred release before reusing the environment`() = runTest(testDispatcher) {
        val runner = UnconfirmedDispatchRunner()
        val releaseGate = kotlinx.coroutines.CompletableDeferred<Unit>()
        var releases = 0
        val hook = object : RunEnvHook {
            override val id = "held-release"
            override val anchor = Anchor.BeforeDispatch
            override val order = 0
            override val gating = false
            override suspend fun engage(ctx: RunContext) = EngageResult.Engaged(Release {
                releaseGate.await()
                releases++
            })
        }
        val launcher = launcher(backgroundScope, runner, hooks = listOf(hook))
        launcher.launch(RunTrigger.Manual)
        runner.finish()
        testScheduler.runCurrent()
        runner.reject = false
        var next: RunLaunchResult? = null
        backgroundScope.launch { next = launcher.launch(RunTrigger.Manual) }
        testScheduler.runCurrent()
        assertNull(next)
        assertEquals(1, runner.delegate.startedExecutionIds.size)
        releaseGate.complete(Unit)
        testScheduler.runCurrent()
        assertEquals(1, releases)
        assertEquals(RunLaunchResult.Started, next)
        runner.finish()
        testScheduler.runCurrent()
        assertEquals(2, releases)
    }

    @Test
    fun `maintenance timeout cannot bypass an unconfirmed dispatch or leak the launcher gate`() = runTest(testDispatcher) {
        val runner = UnconfirmedDispatchRunner()
        val hook = RecordingHook("env", Anchor.BeforeDispatch)
        val launcher = launcher(backgroundScope, runner, hooks = listOf(hook))
        launcher.launch(RunTrigger.Manual)
        var blocked = false
        var entered = false
        backgroundScope.launch {
            try {
                launcher.withDevMaintenance { entered = true }
            } catch (_: com.aliothmoon.maafw.devsession.DevMaintenanceBlockedException) {
                blocked = true
            }
        }
        advanceTimeBy(15_001)
        testScheduler.runCurrent()
        assertTrue(blocked)
        assertTrue(!entered)
        assertNull(hook.releaseReason)
        runner.finish()
        testScheduler.runCurrent()
        runner.reject = false
        assertEquals(RunLaunchResult.Started, launcher.launch(RunTrigger.Manual))
        runner.finish()
    }

    private class RecordingTree : Timber.Tree() {''',
    ),
)

# Callback ownership also includes the immutable guard, even when execution IDs repeat.
RELOAD_PATCHES += (
    (KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
     "        private inline fun updateOwn(crossinline transform: (ActiveExecution) -> ActiveExecution) {",
     "        private fun ownsAuditRun(): Boolean = devLastAuditRun === auditBinding\n\n        private inline fun updateOwn(crossinline transform: (ActiveExecution) -> ActiveExecution) {\n            if (!ownsAuditRun()) return"),
    (KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
     "                if (current.activeExecution?.executionId != executionId) return@update current",
     "                if (current.activeExecution?.executionId != executionId || !ownsAuditRun()) return@update current"),
    (KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
     "                if (execution?.executionId != executionId) return@update current",
     "                if (execution?.executionId != executionId || !ownsAuditRun()) return@update current"),
    (KOTLIN + "runner/MaaFrameworkRunnerPort.kt",
     "        private fun emit(event: RunnerEvent) {",
     "        private fun emit(event: RunnerEvent) {\n            if (!ownsAuditRun()) return"),
)
