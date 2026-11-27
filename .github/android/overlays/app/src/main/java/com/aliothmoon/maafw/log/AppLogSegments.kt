package com.aliothmoon.maafw.log

import java.io.BufferedOutputStream
import java.io.File
import java.io.OutputStream
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.NoSuchFileException
import java.nio.file.StandardOpenOption.APPEND
import java.nio.file.StandardOpenOption.CREATE_NEW
import java.nio.file.StandardOpenOption.WRITE
import java.util.UUID
import java.util.concurrent.TimeUnit

/** App-owned log segments. Only the writer's serialized coroutine may change the active segment. */
internal class AppLogSegments(
    private val directory: () -> File,
    private val elapsedMillis: () -> Long,
    private val wallMillis: () -> Long = System::currentTimeMillis,
) {
    data class Record(val text: String, val wall: Long, val elapsed: Long)

    private data class Segment(val file: File, val startWall: Long, val startElapsed: Long) {
        var bytes = 0L
        var lastWall = startWall
    }

    private var active: Segment? = null
    private var stream: OutputStream? = null

    fun capture(text: String): Record = Record(text, wallMillis(), elapsedMillis())

    fun append(record: Record) {
        val now = wallMillis()
        val elapsed = elapsedMillis()
        // A suspended or stalled writer must not make very old queued records look new.
        if (now <= 0 || now - record.wall !in 0 until QUEUE_MAX_AGE_MS ||
            elapsed - record.elapsed !in 0 until QUEUE_MAX_AGE_MS) return

        val bytes = encode(record.text)
        val current = active
        if (current == null || now < current.lastWall ||
            now - current.startWall >= SEGMENT_MAX_AGE_MS ||
            elapsed - current.startElapsed !in 0 until SEGMENT_MAX_AGE_MS ||
            current.bytes + bytes.size > MAX_FILE_BYTES ||
            (stream == null && !Files.isRegularFile(current.file.toPath(), NOFOLLOW_LINKS))) {
            newSegment(now, elapsed)
        } else if (stream == null) {
            try {
                // No CREATE: a sweep between the existence check and open must not resurrect the path.
                stream = BufferedOutputStream(Files.newOutputStream(current.file.toPath(), APPEND, NOFOLLOW_LINKS))
            } catch (_: NoSuchFileException) {
                newSegment(now, elapsed)
            }
        }
        checkNotNull(stream).write(bytes)
        checkNotNull(active).apply {
            this.bytes += bytes.size
            lastWall = now
        }
    }

    private fun newSegment(now: Long, elapsed: Long) {
        discardActive()
        val dir = directory()
        Files.createDirectories(dir.toPath())
        check(Files.isDirectory(dir.toPath(), NOFOLLOW_LINKS)) { "Invalid App log directory" }
        val file = File(dir, "app.$now.${UUID.randomUUID().toString().replace("-", "")}.log")
        stream = BufferedOutputStream(Files.newOutputStream(file.toPath(), WRITE, CREATE_NEW))
        active = Segment(file, now, elapsed)
        // Only remove closed App segments; keep the just-created segment even after a clock rollback.
        appFiles(dir).filter { it != file && birthMillis(it.name) != null }
            .sortedByDescending { it.lastModified() }
            .drop(MAX_FILES - 1).forEach { Files.deleteIfExists(it.toPath()) }
    }

    /** Flush and close even while idle. The next batch may append, but must never recreate. */
    fun closeBatch() {
        val output = stream ?: return
        stream = null
        output.close()
    }

    fun discardActive() {
        active = null
        closeBatch()
    }

    fun listFiles(): List<File> = appFiles(directory()).sortedByDescending { it.lastModified() }

    fun purge() {
        discardActive()
        listFiles().forEach { Files.deleteIfExists(it.toPath()) }
    }

    companion object {
        val SEGMENT_MAX_AGE_MS = TimeUnit.HOURS.toMillis(12)
        val QUEUE_MAX_AGE_MS = TimeUnit.HOURS.toMillis(1)
        const val MAX_FILE_BYTES = 4L * 1024 * 1024
        const val MAX_FILES = 5
        private val SEGMENT_NAME = Regex("""app\.([1-9][0-9]{0,18})\.[0-9a-f]{32}\.log""")
        private val LEGACY_NAME = Regex("""app(?:\.[1-4])?\.log""")
        private val TRUNCATED = " …[日志条目过长，已截断]\n".toByteArray(Charsets.UTF_8)

        private fun encode(text: String): ByteArray {
            val raw = text.toByteArray(Charsets.UTF_8)
            if (raw.size <= MAX_FILE_BYTES) return raw
            var end = MAX_FILE_BYTES.toInt() - TRUNCATED.size
            // The prefix must stop before a UTF-8 code point, not within one.
            while ((raw[end].toInt() and 0xc0) == 0x80) end--
            return ByteArray(end + TRUNCATED.size).apply {
                raw.copyInto(this, endIndex = end)
                TRUNCATED.copyInto(this, destinationOffset = end)
            }
        }

        fun birthMillis(name: String): Long? = SEGMENT_NAME.matchEntire(name)?.groupValues?.get(1)?.toLongOrNull()
        fun isLegacy(name: String): Boolean = LEGACY_NAME.matches(name)

        private fun appFiles(dir: File): List<File> {
            if (!Files.exists(dir.toPath(), NOFOLLOW_LINKS)) return emptyList()
            check(Files.isDirectory(dir.toPath(), NOFOLLOW_LINKS)) { "Invalid App log directory" }
            return Files.newDirectoryStream(dir.toPath()).use { paths ->
                paths.filter { path ->
                    val name = path.fileName.toString()
                    (isLegacy(name) || birthMillis(name) != null) && Files.isRegularFile(path, NOFOLLOW_LINKS)
                }.map { it.toFile() }
            }
        }
    }
}
