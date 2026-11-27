"""App log rotation and diagnostic cleanup hooks for pinned MaaFwApp."""

KOTLIN = "app/src/main/java/com/aliothmoon/maafw/"
RETENTION = "com.aliothmoon.maafw.log.DebugEvidenceRetention"
APP_WRITER = KOTLIN + "log/AppLogWriter.kt"

EVIDENCE_PATCHES = (
    (
        APP_WRITER,
        "import android.os.Build\n",
        "import android.os.Build\nimport android.os.SystemClock\n",
    ),
    (
        APP_WRITER,
        "import kotlinx.coroutines.launch\nimport java.io.BufferedOutputStream\nimport java.io.File\nimport java.io.FileOutputStream\n",
        "import kotlinx.coroutines.launch\nimport kotlinx.coroutines.yield\nimport java.io.File\n",
    ),
    (
        APP_WRITER,
        '''    private val scope = CoroutineScope(SupervisorJob() + MaaDispatchers.IO.limitedParallelism(1))
    private val channel = Channel<String>(capacity = QUEUE_CAPACITY)

    private val timestampFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")

    private var stream: BufferedOutputStream? = null

    /** -1 表示还没打开过文件，下次写入时从磁盘读实际大小 */
    private var writtenBytes: Long = -1L

    init {
        scope.launch {
            for (entry in channel) {
                append(entry)
                // 逐条 flush 等于每行一次 write 系统调用，下面那个 BufferedOutputStream 就白设了
                while (true) append(channel.tryReceive().getOrNull() ?: break)
                flush()
            }
        }
    }

    fun submit(priority: Int, tag: String?, message: String) {
        channel.trySend(format(priority, tag, message))
    }

    fun setup() {
        channel.trySend(
''',
        '''    private val scope = CoroutineScope(SupervisorJob() + MaaDispatchers.IO.limitedParallelism(1))
    private val channel = Channel<AppLogSegments.Record>(capacity = QUEUE_CAPACITY)
    private val segments = AppLogSegments({ AppPaths.LOG_DIR }, SystemClock::elapsedRealtime)
    private val timestampFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")

    init {
        scope.launch {
            for (entry in channel) {
                append(entry)
                for (i in 1 until MAX_BATCH_SIZE) append(channel.tryReceive().getOrNull() ?: break)
                runCatching { segments.closeBatch() }.onFailure { reportWriteFailure(it) }
                // A permanently nonempty channel must still yield to a pending purge.
                yield()
            }
        }
    }

    private fun enqueue(text: String) {
        channel.trySend(segments.capture(text))
    }

    fun submit(priority: Int, tag: String?, message: String) {
        enqueue(format(priority, tag, message))
    }

    fun setup() {
        enqueue(
''',
    ),
    (
        APP_WRITER,
        '''    private fun append(entry: String) {
        runCatching {
            rotateIfNeeded()
            val bytes = entry.toByteArray(Charsets.UTF_8)
            openStream().write(bytes)
            writtenBytes += bytes.size
        }.onFailure {
            Log.w(TAG, "Failed to write app log", it)
            closeStream()
        }
    }

    private fun flush() {
        runCatching { stream?.flush() }.onFailure {
            Log.w(TAG, "Failed to flush app log", it)
            closeStream()
        }
    }

    private fun openStream(): BufferedOutputStream = stream ?: BufferedOutputStream(
        FileOutputStream(file(), true),
        BUFFER_SIZE,
    ).also {
        stream = it
        writtenBytes = file().length()
    }

    private fun closeStream() {
        runCatching { stream?.close() }
        stream = null
        writtenBytes = -1L
    }

    private fun rotateIfNeeded() {
        if (writtenBytes < 0) {
            val file = file()
            if (!file.exists()) return
            writtenBytes = file.length()
        }
        if (writtenBytes < MAX_FILE_BYTES) return

        closeStream()
        val dir = AppPaths.LOG_DIR
        runCatching {
            File(dir, rotatedName(MAX_FILES - 1)).delete()
            for (index in MAX_FILES - 2 downTo 1) {
                val from = File(dir, rotatedName(index))
                if (from.exists()) from.renameTo(File(dir, rotatedName(index + 1)))
            }
            file().renameTo(File(dir, rotatedName(1)))
        }
        writtenBytes = 0L
    }

    fun listFiles(): List<File> = runCatching {
        AppPaths.LOG_DIR.listFiles()?.filter { it.isFile && FILE_PATTERN.matches(it.name) }
            ?.sortedByDescending { it.lastModified() }.orEmpty()
    }.getOrElse {
        Log.w(TAG, "Failed to list app logs", it)
        emptyList()
    }

    fun purge(): Job = scope.launch {
        closeStream()
        runCatching { listFiles().forEach { it.delete() } }.onFailure {
            Log.w(
                TAG,
                "Failed to clear app logs",
                it
            )
        }
    }

    private fun file(): File = File(AppPaths.LOG_DIR.apply { mkdirs() }, CURRENT_FILE_NAME)

    private fun rotatedName(index: Int): String = "app.$index.log"
''',
        '''    private fun append(entry: AppLogSegments.Record) {
        runCatching { segments.append(entry) }.onFailure { reportWriteFailure(it) }
    }

    private fun reportWriteFailure(error: Throwable) {
        Log.w(TAG, "Failed to write app log", error)
        runCatching { segments.discardActive() }
            .onFailure { Log.w(TAG, "Failed to close app log", it) }
    }

    fun listFiles(): List<File> = runCatching { segments.listFiles() }.getOrElse {
        Log.w(TAG, "Failed to list app logs", it)
        emptyList()
    }

    fun purge(): Job = scope.launch {
        runCatching { segments.purge() }.onFailure { Log.w(TAG, "Failed to clear app logs", it) }
    }
''',
    ),
    (
        APP_WRITER,
        r'''        const val CURRENT_FILE_NAME = "app.log"
        const val BUFFER_SIZE = 8 * 1024
        const val MAX_FILE_BYTES = 4L * 1024 * 1024
        const val MAX_FILES = 5
        const val QUEUE_CAPACITY = 512

        /** `app.log` 与 `app.<n>.log`；与 [rotatedName] 是一对，改一处要改两处 */
        val FILE_PATTERN = Regex("""app(\.\d+)?\.log""")
''',
        '''        const val QUEUE_CAPACITY = 512
        const val MAX_BATCH_SIZE = 64
''',
    ),
    (
        KOTLIN + "MaaFwApp.kt",
        "        val appScope = koin.get<CoroutineScope>(named<AppCoroutineScope>())\n",
        "        val appScope = koin.get<CoroutineScope>(named<AppCoroutineScope>())\n"
        "        // Diagnostic retention runs while the app lives, including idle periods.\n"
        "        appScope.launch(Dispatchers.IO) {\n"
        "            while (true) {\n"
        f"                runCatching {{ {RETENTION}.prune({RETENTION}.ownedRoots(AppPaths.ROOT), appLogRoot = AppPaths.LOG_DIR) }}\n"
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
        runCatching {{ {RETENTION}.prune(roots, appLogRoot = File(appRoot, AppFiles.LOG_DIR)) }}
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
