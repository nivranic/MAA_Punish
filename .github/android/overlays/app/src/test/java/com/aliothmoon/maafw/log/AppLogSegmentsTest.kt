package com.aliothmoon.maafw.log

import com.aliothmoon.maafw.telemetry.EvidenceSource
import com.aliothmoon.maafw.telemetry.TaskEvidence
import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.FileTime
import java.util.concurrent.TimeUnit
import kotlin.io.path.createTempDirectory
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class AppLogSegmentsTest {
    private lateinit var root: File
    private val log: File get() = File(root, "log")
    private val day = TimeUnit.DAYS.toMillis(1)
    private var wall = 100 * day
    private var elapsed = 1000L

    @Before fun setup() { root = createTempDirectory("maa-app-log-segments").toFile() }
    @After fun teardown() { root.deleteRecursively() }

    private fun writer() = AppLogSegments({ log }, { elapsed }, { wall })

    private fun AppLogSegments.write(text: String) {
        append(capture(text))
        closeBatch()
    }

    @Test fun appendReusesOnlyAHalfDaySegmentAndAgeUsesItsBirthNotMtime() {
        val writer = writer()
        val born = wall
        writer.write("old\n")
        val first = writer.listFiles().single()
        wall += AppLogSegments.SEGMENT_MAX_AGE_MS - 1
        elapsed += AppLogSegments.SEGMENT_MAX_AGE_MS - 1
        writer.write("same segment\n")
        assertEquals("old\nsame segment\n", first.readText())
        wall++
        elapsed++
        writer.write("new segment\n")
        val second = writer.listFiles().single { it != first }
        assertEquals("new segment\n", second.readText())
        assertEquals(born, AppLogSegments.birthMillis(first.name))

        val cutoff = born + DebugEvidenceRetention.KEEP_DAYS * day
        // Simulate an mtime refresh regardless of which OS runs the test.
        Files.setLastModifiedTime(first.toPath(), FileTime.fromMillis(cutoff))
        Files.setLastModifiedTime(second.toPath(), FileTime.fromMillis(cutoff))
        assertEquals(0, DebugEvidenceRetention.prune(listOf(log), cutoff - 1, appLogRoot = log))
        assertEquals(1, DebugEvidenceRetention.prune(listOf(log), cutoff, appLogRoot = log))
        assertFalse(first.exists())
        assertTrue(second.exists())
        assertTrue(AppLogSegments.QUEUE_MAX_AGE_MS + AppLogSegments.SEGMENT_MAX_AGE_MS +
            DebugEvidenceRetention.KEEP_DAYS * day + DebugEvidenceRetention.SWEEP_INTERVAL_MS < 7 * day)
    }

    @Test fun rollbackElapsedExpiryAndNewWriterNeverAppendToAnOldSegment() {
        val writer = writer()
        writer.write("before\n")
        val original = writer.listFiles().single()
        wall--
        writer.write("rollback\n")
        val rollback = writer.listFiles().single { it != original }
        elapsed += AppLogSegments.SEGMENT_MAX_AGE_MS
        writer.write("elapsed limit\n")
        val rotated = writer.listFiles().single { it !in listOf(original, rollback) }
        writer().write("after restart\n")
        val restart = writer.listFiles().single { it !in listOf(original, rollback, rotated) }
        assertEquals("before\n", original.readText())
        assertEquals("rollback\n", rollback.readText())
        assertEquals("elapsed limit\n", rotated.readText())
        assertEquals("after restart\n", restart.readText())
    }

    @Test fun wallTimeAloneAlsoClosesTheOldSegmentAfterSleep() {
        val writer = writer()
        writer.write("before\n")
        val original = writer.listFiles().single()
        wall += AppLogSegments.SEGMENT_MAX_AGE_MS
        writer.write("after\n")
        assertEquals("before\n", original.readText())
        assertEquals("after\n", writer.listFiles().single { it != original }.readText())
    }

    @Test fun delayedQueueCannotRefreshOldRecords() {
        val writer = writer()
        val delayed = writer.capture("must not persist\n")
        elapsed += AppLogSegments.QUEUE_MAX_AGE_MS
        writer.append(delayed)
        writer.closeBatch()
        assertTrue(writer.listFiles().isEmpty())
        writer.write("fresh\n")
        assertEquals("fresh\n", writer.listFiles().single().readText())
    }

    @Test fun externallyRemovedSegmentIsNotRecreatedAndPurgeStaysInScope() {
        val writer = writer()
        writer.write("before\n")
        val old = writer.listFiles().single()
        Files.delete(old.toPath())
        writer.write("after\n")
        val replacement = writer.listFiles().single()
        assertFalse(old.exists())
        assertNotEquals(old, replacement)
        val other = File(log, "app.5.log").apply { writeText("not created by the old writer") }
        val native = File(log, "maafw.log").apply { writeText("native") }
        File(log, "app.1.log").writeText("recognized legacy name")
        writer.purge()
        assertTrue(other.exists() && native.exists())
        assertTrue(writer.listFiles().isEmpty())
        writer.write("after purge\n")
        assertNotEquals(replacement, writer.listFiles().single())
    }

    @Test fun oversizedChineseEntryClosesAtAValidUtf8Boundary() {
        val writer = writer()
        writer.write("[date] E Tag: " + "测".repeat(AppLogSegments.MAX_FILE_BYTES.toInt() / 3 + 1))
        val segment = writer.listFiles().single()
        val text = Charsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(segment.readBytes())).toString()
        assertTrue(segment.length() <= AppLogSegments.MAX_FILE_BYTES)
        assertTrue(text.startsWith("[date] E Tag: 测"))
        assertTrue(text.endsWith("[日志条目过长，已截断]\n"))
    }

    @Test fun logCollectorsRecognizeBothSegmentsAcrossOneTask() {
        val writer = writer()
        writer.write("context before task\n")
        val start = TaskEvidence.captureStart(listOf(EvidenceSource(log)))
        writer.write("本次任务前半段\n")
        wall += AppLogSegments.SEGMENT_MAX_AGE_MS
        writer.write("本次任务后半段\n")
        val files = writer.listFiles()
        assertEquals(2, files.size)
        val plan = LogExportCollector.plan(LogExportCollector.collect(listOf(log), System.currentTimeMillis()))
        assertEquals(files.toSet(), plan.required.toSet())
        TaskEvidence.captureEnd(start).use { selection ->
            val evidence = TaskEvidence.buildLogs(selection)
            assertEquals(files.map { it.name }.toSet(), evidence.entries.map { it.source }.toSet())
            assertTrue(evidence.entries.all { it.kind == "maafwapp" })
            assertTrue(evidence.entries.any { "本次任务前半段" in it.content })
            assertTrue(evidence.entries.any { "本次任务后半段" in it.content })
        }
    }

    @Test fun sizeAndCountBoundTheUsualRotation() {
        val writer = writer()
        writer.write("x".repeat(AppLogSegments.MAX_FILE_BYTES.toInt()))
        val full = writer.listFiles().single()
        writer.write("next\n")
        assertEquals(AppLogSegments.MAX_FILE_BYTES, full.length())
        assertEquals("next\n", writer.listFiles().single { it != full }.readText())
        repeat(AppLogSegments.MAX_FILES + 2) {
            wall += AppLogSegments.SEGMENT_MAX_AGE_MS
            writer.write("small\n")
        }
        assertEquals(AppLogSegments.MAX_FILES, writer.listFiles().size)
        assertTrue(writer.listFiles().all { AppLogSegments.birthMillis(it.name) != null })
    }
}
