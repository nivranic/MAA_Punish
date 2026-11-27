package com.aliothmoon.maafw.log

import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.NoSuchFileException
import java.nio.file.attribute.BasicFileAttributes
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
    fun prune(roots: List<File>, now: Long = System.currentTimeMillis(), appLogRoot: File? = null): Int {
        val cutoff = now - TimeUnit.DAYS.toMillis(KEEP_DAYS.toLong())
        val ownedAppLogs = appLogRoot?.toPath()?.toAbsolutePath()?.normalize()
        var deleted = 0
        var failures = 0
        var firstFailure: Exception? = null
        fun report(error: Exception) {
            failures++
            if (firstFailure == null) firstFailure = error
        }
        for (input in roots.distinct()) {
            val path = input.toPath()
            val root = try {
                if (!Files.readAttributes(path, BasicFileAttributes::class.java, NOFOLLOW_LINKS).isDirectory) continue
                input.canonicalFile
            } catch (_: NoSuchFileException) {
                continue  // Optional diagnostics trees may not exist yet.
            } catch (error: IOException) {
                report(error)
                continue
            } catch (error: SecurityException) {
                report(error)
                continue
            }
            val appLogDir = path.toAbsolutePath().normalize() == ownedAppLogs
            root.walkTopDown().onFail { _, error -> report(error) }.onEnter {
                !Files.isSymbolicLink(it.toPath()) && it.canonicalFile.startsWith(root)
            }.filter {
                it.isFile && !Files.isSymbolicLink(it.toPath()) &&
                    it.extension.lowercase() in EXTENSIONS && it.canonicalFile.startsWith(root)
            }.forEach { file ->
                val appLog = appLogDir && file.parentFile == root
                // Only the App's known top-level legacy names lack a trustworthy oldest time.
                // Never apply this migration to PI/native logs or other subdirectories.
                val legacy = appLog && AppLogSegments.isLegacy(file.name)
                val birth = if (appLog) AppLogSegments.birthMillis(file.name) else null
                val modified = file.lastModified()
                if (legacy || (birth != null && birth <= cutoff) || (modified > 0 && modified <= cutoff)) {
                    try {
                        if (Files.deleteIfExists(file.toPath())) deleted++
                    } catch (error: IOException) {
                        // Concurrent sweeps are normal. Some filesystems report AccessDenied
                        // during a racing unlink: suppress only if absence can be confirmed.
                        if (!Files.notExists(file.toPath(), NOFOLLOW_LINKS)) report(error)
                    } catch (error: SecurityException) {
                        report(error)
                    }
                }
            }
        }
        if (failures != 0) throw IOException("Diagnostic retention: $failures failure(s), $deleted deleted", firstFailure)
        return deleted
    }
}
