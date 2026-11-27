package com.aliothmoon.maafw.log

import java.io.File
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.io.path.createTempDirectory
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Assume.assumeNoException
import org.junit.Test

class DebugEvidenceRetentionTest {
    private lateinit var base: File
    private val now = TimeUnit.DAYS.toMillis(100)
    private val day = TimeUnit.DAYS.toMillis(1)

    @Before fun setup() { base = createTempDirectory("maa-evidence-retention").toFile() }
    @After fun teardown() { base.deleteRecursively() }

    private fun file(path: String, age: Long): File = File(base, path).apply {
        parentFile.mkdirs()
        writeText("evidence")
        check(setLastModified(now - age))
    }

    @Test fun expiresAllDiagnosticKindsIncludingTheOnlyRemainingFile() {
        val files = listOf("log/run/run.jsonl", "log/manual/one.png", "log/on_error/two.jpg",
            "log/export/maafw_logs.zip", "debug/startup.txt", "pi/debug/custom.log",
            "log/maafw.bak.2026.01.01-00.00.00.000.log").map { file(it, 8 * day) }
        assertEquals(files.size, DebugEvidenceRetention.prune(
            listOf(File(base, "log"), File(base, "debug"), File(base, "pi/debug")), now))
        assertTrue(files.none { it.exists() })
    }

    @Test fun keepsFreshEvidenceAndRuntimeStateAndUnrelatedFiles() {
        val kept = listOf(file("log/manual/fresh.png", day), file("pi/debug/adb_zone_offset.json", 9 * day),
            file("pi/resource/image/template.png", 9 * day), file("photos/user.png", 9 * day),
            file("log/future.log", -day))
        assertEquals(0, DebugEvidenceRetention.prune(listOf(File(base, "log"), File(base, "pi/debug")), now))
        assertTrue(kept.all { it.exists() })
    }

    @Test fun expiresPiDiagnosticsBeforeAnyRuntimePathsAreConfigured() {
        val expired = file("pi/debug/custom.log", 8 * day)
        val cache = file("pi/debug/adb_zone_offset.json", 8 * day)
        val resource = file("pi/resource/image/template.png", 8 * day)
        assertEquals(1, DebugEvidenceRetention.prune(DebugEvidenceRetention.ownedRoots(base), now))
        assertFalse(expired.exists())
        assertTrue(cache.exists())
        assertTrue(resource.exists())
    }

    @Test fun refusesFileAndDirectorySymlinkEscapes() {
        val outside = file("photos/user.png", 9 * day)
        val log = File(base, "log").apply { mkdirs() }
        try {
            Files.createSymbolicLink(File(log, "image.png").toPath(), outside.toPath())
            Files.createSymbolicLink(File(log, "app.log").toPath(), outside.toPath())
            Files.createSymbolicLink(File(log, "escape").toPath(), outside.parentFile.toPath())
        } catch (e: java.nio.file.FileSystemException) {
            assumeNoException("Host does not grant symbolic-link creation", e)
        }
        assertEquals(0, DebugEvidenceRetention.prune(listOf(log), now, appLogRoot = log))
        assertTrue(Files.isSymbolicLink(File(log, "app.log").toPath()))
        assertTrue(outside.exists())
    }

    @Test fun onlyExplicitlyOwnedTopLevelAppLogsMigrateLegacyNames() {
        val legacy = listOf("app.log", "app.1.log", "app.2.log", "app.3.log", "app.4.log")
            .map { file("log/$it", 0) }
        val unrelated = listOf("log/app.5.log", "log/run/app.log", "debug/app.1.log", "pi/debug/app.log",
            "photos/app.log", "log/app.00000000.log", "log/maafw.log").map { file(it, 0) }
        val roots = DebugEvidenceRetention.ownedRoots(base)
        assertEquals(0, DebugEvidenceRetention.prune(roots, now))
        assertEquals(legacy.size, DebugEvidenceRetention.prune(roots, now, appLogRoot = File(base, "log")))
        assertTrue(legacy.none { it.exists() })
        assertTrue(unrelated.all { it.exists() })
        assertEquals(0, DebugEvidenceRetention.prune(roots, now, appLogRoot = File(base, "log")))
    }

    @Test fun overlappingSweepsConvergeWithoutDeletingNewSegments() {
        val log = File(base, "log")
        val roots = DebugEvidenceRetention.ownedRoots(base)
        val stale = (1..24).map { file("log/expired-$it.log", 8 * day) }
        val start = CountDownLatch(1)
        val workers = Executors.newFixedThreadPool(2)
        try {
            val sweeps = (1..2).map {
                workers.submit<Int> {
                    check(start.await(5, TimeUnit.SECONDS))
                    DebugEvidenceRetention.prune(roots, now, appLogRoot = log)
                }
            }
            start.countDown()
            val writer = AppLogSegments({ log }, { 0L }, { now })
            writer.append(writer.capture("fresh\n"))
            writer.closeBatch()
            sweeps.forEach { it.get(5, TimeUnit.SECONDS) }
            // A Windows provider can acknowledge both racing delete calls. Check
            // the filesystem and an idempotent follow-up, not their summed count.
            assertTrue(stale.none { it.exists() })
            assertEquals(0, DebugEvidenceRetention.prune(roots, now, appLogRoot = log))
            assertEquals("fresh\n", writer.listFiles().single().readText())
        } finally {
            workers.shutdownNow()
        }
    }

    @Test fun hourlySweepHasHeadroomWithinSevenDays() {
        val boundary = file("log/manual/boundary.png", 6 * day)
        val recent = file("log/manual/recent.png", 6 * day - 1)
        assertEquals(1, DebugEvidenceRetention.prune(listOf(File(base, "log")), now))
        assertFalse(boundary.exists())
        assertTrue(recent.exists())
        assertTrue(6 * day + DebugEvidenceRetention.SWEEP_INTERVAL_MS < 7 * day)
    }
}
