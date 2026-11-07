"""Bound native Maa diagnostics to seven days on the pinned Android baseline."""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"
RETENTION = "com.aliothmoon.maafw.log.DebugEvidenceRetention"

EVIDENCE_PATCHES = (
    (
        KOTLIN + "MaaFwApp.kt",
        "        val appScope = koin.get<CoroutineScope>(named<AppCoroutineScope>())\n",
        "        val appScope = koin.get<CoroutineScope>(named<AppCoroutineScope>())\n"
        "        // Diagnostic retention runs while the app lives, including idle periods.\n"
        "        appScope.launch(Dispatchers.IO) {\n"
        "            while (true) {\n"
        f"                runCatching {{ {RETENTION}.prune({RETENTION}.ownedRoots(AppPaths.ROOT)) }}\n"
        '                    .onFailure { android.util.Log.w("EvidenceRetention", "prune failed", it) }\n'
        f"                kotlinx.coroutines.delay({RETENTION}.SWEEP_INTERVAL_MS)\n"
        "            }\n"
        "        }\n",
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "        pruneMaafwBackups()\n",
        "        pruneMaafwBackups()\n        pruneEvidenceIfDue()\n",
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "    private fun startHeartbeatWatchdog() {\n",
        f'''    private var lastEvidenceSweepMs = 0L

    @Synchronized
    private fun pruneEvidenceIfDue() {{
        val now = System.nanoTime() / 1_000_000
        if (lastEvidenceSweepMs != 0L && now - lastEvidenceSweepMs < {RETENTION}.SWEEP_INTERVAL_MS) return
        lastEvidenceSweepMs = now
        // Same app-owned root derivation as RemoteBootTrace, available before setup().
        val appRoot = File(android.os.Environment.getExternalStorageDirectory(),
            "Android/data/${{BuildConfig.APPLICATION_ID}}/files")
        val roots = {RETENTION}.ownedRoots(appRoot).toMutableList()
        logDir?.let(::File)?.takeIf {{ it.name == AppFiles.LOG_DIR }}?.let {{ log ->
            roots += log
            roots += File(log.parentFile, AppFiles.DEBUG_DIR)
        }}
        piRoot?.let {{ roots += File(it, "debug") }}
        runCatching {{ {RETENTION}.prune(roots) }}
            .onFailure {{ Ln.w("$TAG: diagnostic retention failed: ${{it.message}}") }}
    }}

    private fun startHeartbeatWatchdog() {{
''',
    ),
    (
        KOTLIN + "remote/RemoteServiceImpl.kt",
        "                val pid = appPid.get()\n",
        "                pruneEvidenceIfDue()\n                val pid = appPid.get()\n",
    ),
    (
        KOTLIN + "runner/RunSessionLog.kt",
        "System.currentTimeMillis() - keepDays * MS_PER_DAY",
        "System.currentTimeMillis() - keepDays.coerceIn(1, KEEP_DAYS) * MS_PER_DAY",
    ),
    (
        KOTLIN + "runner/RunSessionLog.kt",
        "const val KEEP_DAYS = 30",
        "const val KEEP_DAYS = 7",
    ),
)
