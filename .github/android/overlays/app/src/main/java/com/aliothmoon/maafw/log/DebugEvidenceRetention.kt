package com.aliothmoon.maafw.log

import java.io.File
import java.nio.file.Files
import java.util.concurrent.TimeUnit

/** Only Maa-owned diagnostic trees may be passed here, never a shared photo directory. */
object DebugEvidenceRetention {
    const val MAX_KEEP_DAYS = 7
    // Sweep after six days, leaving a day of headroom for the hourly sweep.
    const val KEEP_DAYS = MAX_KEEP_DAYS - 1
    val SWEEP_INTERVAL_MS = TimeUnit.HOURS.toMillis(1)
    private val EXTENSIONS = setOf("log", "jsonl", "png", "jpg", "jpeg", "zip", "txt")

    /** Available before task setup; PI diagnostics can be owned by the shell process. */
    fun ownedRoots(appRoot: File): List<File> = listOf(
        File(appRoot, "log"), File(appRoot, "debug"), File(appRoot, "pi/debug")
    )

    /** Preserve runtime JSON/cache/configuration and refuse symlinks and path escapes. */
    fun prune(roots: List<File>, now: Long = System.currentTimeMillis()): Int {
        val cutoff = now - TimeUnit.DAYS.toMillis(KEEP_DAYS.toLong())
        var deleted = 0
        for (input in roots.distinct()) {
            if (Files.isSymbolicLink(input.toPath()) || !input.isDirectory) continue
            val root = input.canonicalFile
            root.walkTopDown().onEnter {
                !Files.isSymbolicLink(it.toPath()) && it.canonicalFile.startsWith(root)
            }.filter {
                it.isFile && !Files.isSymbolicLink(it.toPath()) &&
                    it.extension.lowercase() in EXTENSIONS && it.canonicalFile.startsWith(root)
            }.forEach { file ->
                val modified = file.lastModified()
                if (modified > 0 && modified <= cutoff && file.delete()) deleted++
            }
        }
        return deleted
    }
}
