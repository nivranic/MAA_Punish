"""Shared screen-saver ownership and USB-only idle foreground lifetime.

Anchors target MaaFwApp bc55dac. Runtime source remains in the fixed upstream
checkout; the reusable policy and JVM regressions are tracked overlays.
"""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"

PATCHES = (
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        "import android.os.Build\n",
        "import android.os.Build\nimport android.provider.Settings\nimport com.aliothmoon.maafw.devsession.DevSessionCoordinator\nimport com.aliothmoon.maafw.devsession.ScreenSaverOwner\nimport com.aliothmoon.maafw.devsession.ScreenSaverOwnership\nimport com.aliothmoon.maafw.service.RunForegroundService\n",
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        "import com.aliothmoon.maafw.runner.RunnerPhase\nimport com.aliothmoon.maafw.runner.RunnerPort\nimport com.aliothmoon.maafw.runner.isBusy\n",
        "import kotlinx.coroutines.flow.combine\n",
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        "    private val runnerPort: RunnerPort,\n",
        "    private val devSession: DevSessionCoordinator,\n",
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        "    private var phaseJob: Job? = null\n",
        "    private val ownership = ScreenSaverOwnership()\n    private var setupDone = false\n",
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        '''    fun setup() {
        scope.launch {
            appSettings.runMode.collect { mode ->
                when (mode) {
                    RunMode.BACKGROUND -> observePhase()
                    RunMode.FOREGROUND -> {
                        stopObservingPhase()
                        hide()
                    }
                }
            }
        }
    }

    // ── 执行态 ──

    private fun observePhase() {
        if (phaseJob != null) return
        phaseJob = scope.launch {
            var previous: RunnerPhase = runnerPort.state.value.phase
            runnerPort.state.collect { state ->
                val current = state.phase
                when {
                    // 开关只管「自动盖」；手动盖上的那次不受它影响
                    !previous.isBusy && current.isBusy ->
                        if (appSettings.screenSaverEnabled.value) show()

                    // 结束就撤，别让用户回来面对一块黑屏还得先滑一下
                    previous.isBusy && !current.isBusy -> hide()
                }
                previous = current
            }
        }
    }

    private fun stopObservingPhase() {
        phaseJob?.cancel()
        phaseJob = null
    }
''',
        '''    fun setup() {
        if (setupDone) return
        setupDone = true
        scope.launch {
            var previousUsbSession: String? = null
            // Lease identity is stable across heartbeats. Neither opening the
            // app again nor renewing a lease reopens a dismissed screen saver.
            combine(appSettings.runMode, devSession.lease) { mode, lease ->
                mode to lease?.sessionId
            }.collect { (mode, sessionId) ->
                ownership.setUsbSession(sessionId)
                val newSession = sessionId != null && previousUsbSession != sessionId
                previousUsbSession = sessionId
                if (newSession && !RunForegroundService.startForDevSession(context)) {
                    devSession.closeCurrent(sessionId!!, "foreground_service_denied")
                    ownership.setUsbSession(null)
                    reconcileWindow()
                    return@collect
                }
                if (mode != RunMode.BACKGROUND) {
                    ownership.clearForUnsupportedMode()
                    hideWindow()
                    sessionId?.let { devSession.setScreenSaverStatus(it, "foreground_mode_unsupported") }
                } else if (sessionId != null) {
                    val status = when {
                        ownership.usbSuppressed -> "suppressed"
                        !Settings.canDrawOverlays(context) -> "permission_missing"
                        acquire(ScreenSaverOwner.Usb(sessionId)) -> "active"
                        else -> "window_failed"
                    }
                    devSession.setScreenSaverStatus(sessionId, status)
                    reconcileWindow()
                } else {
                    reconcileWindow()
                }
            }
        }
    }

    // Task ownership is produced only by ScreenSaverHook, keyed by executionId.
    // A second phase observer would reopen user dismissals and race hook release.
    suspend fun acquireRun(executionId: String): Boolean = acquire(ScreenSaverOwner.Run(executionId))

    suspend fun releaseRun(executionId: String) = withContext(Dispatchers.Main.immediate) {
        ownership.release(ScreenSaverOwner.Run(executionId))
        reconcileWindow()
    }

    private suspend fun acquire(owner: ScreenSaverOwner): Boolean = withContext(Dispatchers.Main.immediate) {
        if (appSettings.runMode.value != RunMode.BACKGROUND) return@withContext false
        if (!ownership.acquire(owner)) return@withContext false
        if (showWindow()) return@withContext true
        ownership.release(owner)
        false
    }

    private suspend fun reconcileWindow() {
        if (!ownership.shouldShow) hideWindow()
    }

    // The USB status describes its automatic trigger, not every shared owner:
    // a later explicit manual/run request can display the window while USB
    // automatic reopening remains suppressed for this lease.
    private suspend fun dismissByUser() = withContext(Dispatchers.Main.immediate) {
        ownership.dismissByUser()
        hideWindow()
        devSession.lease.value?.let { devSession.setScreenSaverStatus(it.sessionId, "suppressed") }
    }
''',
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        '''    /** 返回是否真的盖上了；没有悬浮窗权限时 addView 会抛，这里吞掉并如实回 false */
    suspend fun show(): Boolean = withContext(Dispatchers.Main.immediate) {''',
        '''    /** Existing UI entry: a manual owner survives task completion/unplug. */
    suspend fun show(): Boolean = acquire(ScreenSaverOwner.Manual)

    /** Window result only; ownership is acquired separately before this call. */
    private suspend fun showWindow(): Boolean = withContext(Dispatchers.Main.immediate) {''',
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        "    suspend fun hide() = withContext(Dispatchers.Main.immediate) {\n",
        "    private suspend fun hideWindow() = withContext(Dispatchers.Main.immediate) {\n",
    ),
    (
        KOTLIN + "overlay/screensaver/ScreenSaverOverlayManager.kt",
        "                    onUnlock = { scope.launch { hide() } },\n",
        "                    onUnlock = { scope.launch { dismissByUser() } },\n",
    ),
    (
        KOTLIN + "di/OverlayModule.kt",
        '''        ScreenSaverOverlayManager(
            context = androidContext(),
            runnerPort = get(),''',
        '''        ScreenSaverOverlayManager(
            context = androidContext(),
            devSession = get(),''',
    ),
    (
        KOTLIN + "runner/EnvironmentHooks.kt",
        '''        // 只有确实是本轮盖上的才登记撤销：用户自己手动盖的那份不归这一轮管
        if (!screenSaver.show()) {''',
        '''        // A successful request owns only this execution, even when a
        // manual/USB owner already has the shared window on screen.
        if (!screenSaver.show(ctx.executionId)) {''',
    ),
    (
        KOTLIN + "runner/EnvironmentHooks.kt",
        "        return EngageResult.Engaged(Release { screenSaver.hide() })\n",
        "        return EngageResult.Engaged(Release { screenSaver.hide(ctx.executionId) })\n",
    ),
    (
        KOTLIN + "runner/EnvironmentHooks.kt",
        '''interface RunScreenSaver {
    /** 返回是否确实由本次调用盖上 */
    suspend fun show(): Boolean
    suspend fun hide()
}''',
        '''interface RunScreenSaver {
    /** True means this execution acquired a use, not that it created the window. */
    suspend fun show(executionId: String): Boolean
    suspend fun hide(executionId: String)
}''',
    ),
    (
        KOTLIN + "di/RunnerModule.kt",
        '''            override suspend fun show(): Boolean = manager.show()
            override suspend fun hide() = manager.hide()''',
        '''            override suspend fun show(executionId: String): Boolean = manager.acquireRun(executionId)
            override suspend fun hide(executionId: String) = manager.releaseRun(executionId)''',
    ),
    (
        "app/src/test/java/com/aliothmoon/maafw/runner/EnvironmentHooksTest.kt",
        "        override suspend fun show(): Boolean {\n",
        "        override suspend fun show(executionId: String): Boolean {\n",
    ),
    (
        "app/src/test/java/com/aliothmoon/maafw/runner/EnvironmentHooksTest.kt",
        "        override suspend fun hide() {\n",
        "        override suspend fun hide(executionId: String) {\n",
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "import android.content.Intent\n",
        "import android.content.Intent\nimport android.graphics.drawable.Icon\nimport android.os.Build\nimport com.aliothmoon.maafw.devsession.DevSessionCoordinator\nimport com.aliothmoon.maafw.devsession.needsRunForegroundService\nimport com.aliothmoon.maafw.devsession.startForegroundWithDenialHandling\n",
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "    private val runnerPort: RunnerPort by inject()\n",
        "    private val runnerPort: RunnerPort by inject()\n    private val devSession: DevSessionCoordinator by inject()\n    private var lifetimeFinished = false\n",
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "        if (!initial.phase.isBusy) {\n",
        "        if (!shouldKeepAlive(initial)) {\n",
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        '''        if (foregroundDenied) return START_NOT_STICKY
        // 系统可能只走 onStartCommand；FGS 提升要在这里再保一次''',
        '''        if (foregroundDenied) return START_NOT_STICKY
        if (intent?.action == ACTION_END_DEV_SESSION) {
            intent.getStringExtra(EXTRA_DEV_SESSION_ID)?.let {
                devSession.closeCurrent(it, "user_closed")
            }
        }
        // 系统可能只走 onStartCommand；FGS 提升要在这里再保一次''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "        if (!snapshot.phase.isBusy) {\n",
        "        if (!shouldKeepAlive(snapshot)) {\n",
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        '''    override fun onDestroy() {
        observeJob = null''',
        '''    override fun onDestroy() {
        // A normal idle stop can race a newly accepted lease/start request.
        // That new session belongs to the next service start, not this teardown.
        if (!lifetimeFinished) devSession.lease.value?.let {
            devSession.closeCurrent(it.sessionId, "foreground_service_stopped")
        }
        observeJob = null''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        '''            islandReady,
        ) { state, status, _, _ -> state to status }.collectLatest { (state, status) ->
            if (!state.phase.isBusy) {''',
        '''            islandReady,
            combine(devSession.lease, devSession.status) { lease, status -> lease to status },
        ) { state, status, _, _, _ -> state to status }.collectLatest { (state, status) ->
            if (!shouldKeepAlive(state)) {''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "            if (!runnerPort.state.value.phase.isBusy) {\n",
        "            if (!shouldKeepAlive(runnerPort.state.value)) {\n",
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "    private fun stopNow() {\n",
        '''    private fun shouldKeepAlive(state: RunnerState): Boolean =
        needsRunForegroundService(state.phase.isBusy, devSession.lease.value != null)

    private fun stopNow() {
        lifetimeFinished = true
''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        '''    private fun startAsForeground(notification: Notification): Boolean {
        try {
            SpecialUseFgsGate.startForeground(this, NOTIFICATION_ID, notification)
            return true
        } catch (e: SecurityException) {
            // 预检放行但系统仍拒（如 appop 为 FOREGROUND）；AOSP 抛出前已清 fgRequired，
            // stopSelf 不会触发 ForegroundServiceDidNotStartInTimeException
            Timber.w(e, "RunForegroundService: startForeground denied, run without FGS")
            foregroundDenied = true
            stopSelf()
            return false
        }
    }''',
        '''    private fun startAsForeground(notification: Notification): Boolean =
        startForegroundWithDenialHandling(
            sdkInt = Build.VERSION.SDK_INT,
            start = { SpecialUseFgsGate.startForeground(this, NOTIFICATION_ID, notification) },
            onDenied = { error ->
                // SecurityException and Android 12+ background-start rejection
                // share cleanup. Other runtime failures keep their diagnostics.
                Timber.w(error, "RunForegroundService: startForeground denied, run without FGS")
                foregroundDenied = true
                devSession.lease.value?.let { devSession.closeCurrent(it.sessionId, "foreground_service_denied") }
                stopSelf()
            },
        )''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        '''    private fun buildNotification(state: RunnerState, statusText: String?): Notification {
        val snapshot''',
        '''    private fun buildNotification(state: RunnerState, statusText: String?): Notification {
        val sessionId = devSession.lease.value?.sessionId
        if (!state.phase.isBusy && sessionId != null) return buildDevSessionNotification(sessionId)
        val snapshot''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "        return notificationFactory.build(content, backend, NOTIFICATION_ID, firstFloat)\n",
        '''        val notification = notificationFactory.build(content, backend, NOTIFICATION_ID, firstFloat)
        return if (sessionId == null) notification else Notification.Builder.recoverBuilder(this, notification)
            .addAction(devSessionExitAction(sessionId)).build()
    }

    private fun buildDevSessionNotification(sessionId: String): Notification {
        releaseIslandGate()
        notificationManager.createNotificationChannel(NotificationChannel(
            DEV_SESSION_CHANNEL_ID,
            getString(R.string.dev_session_notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        ))
        val text = when (devSession.status.value?.screen_saver_status) {
            "active" -> R.string.dev_session_notification_active
            "suppressed" -> R.string.dev_session_notification_suppressed
            "permission_missing" -> R.string.dev_session_notification_permission
            "foreground_mode_unsupported" -> R.string.dev_session_notification_mode
            "window_failed" -> R.string.dev_session_notification_failed
            else -> R.string.dev_session_notification_waiting
        }
        return NotificationCompat.Builder(this, DEV_SESSION_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.dev_session_notification_title))
            .setContentText(getString(text))
            .setContentIntent(contentIntent())
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build().let { notification ->
                Notification.Builder.recoverBuilder(this, notification)
                    .addAction(devSessionExitAction(sessionId)).build()
            }
    }

    private fun devSessionExitAction(sessionId: String): Notification.Action {
        val intent = Intent(this, RunForegroundService::class.java).apply {
            action = ACTION_END_DEV_SESSION
            // PendingIntent identity includes data, so an old notification
            // cannot be updated to close a later lease accidentally.
            data = android.net.Uri.parse("maafw-dev-session://close/$sessionId")
            putExtra(EXTRA_DEV_SESSION_ID, sessionId)
        }
        val pending = PendingIntent.getService(this, 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return Notification.Action.Builder(Icon.createWithResource(this, R.drawable.ic_notification),
            getString(R.string.dev_session_notification_exit), pending).build()
''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        "        private const val NOTIFICATION_ID = 1001\n",
        '''        private const val NOTIFICATION_ID = 1001
        private const val DEV_SESSION_CHANNEL_ID = "usb_debug_session"
        private const val ACTION_END_DEV_SESSION = "com.aliothmoon.maafw.END_DEV_SESSION"
        private const val EXTRA_DEV_SESSION_ID = "dev_session_id"
''',
    ),
    (
        KOTLIN + "service/RunForegroundService.kt",
        '''        fun start(context: Context) {
            if (SpecialUseFgsGate.isDenied(context)) {
                Timber.w("RunForegroundService: specialUse appop denied, skip FGS start")
                return
            }
            runCatching {
                context.startForegroundService(Intent(context, RunForegroundService::class.java))
            }.onFailure { Timber.w(it, "Failed to start foreground service") }
        }''',
        '''        fun start(context: Context) { startForDevSession(context) }

        /** Called while a newly established lease still has foreground UI.
         * A later startForeground failure closes the lease inside the service. */
        fun startForDevSession(context: Context): Boolean {
            if (SpecialUseFgsGate.isDenied(context)) {
                Timber.w("RunForegroundService: specialUse appop denied, skip FGS start")
                return false
            }
            return runCatching {
                context.startForegroundService(Intent(context, RunForegroundService::class.java))
                true
            }.onFailure { Timber.w(it, "Failed to start foreground service") }.getOrDefault(false)
        }''',
    ),
    (
        "app/src/main/AndroidManifest.xml",
        'android:value="Keeps the app process alive while the privileged service drives a MaaFramework run"',
        'android:value="Keeps the app process alive during MaaFramework runs or an explicitly started USB development session"',
    ),
)
