package com.aliothmoon.maafw.devsession

import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.nio.file.Files

class DevSourceTransactionTest {
    @get:Rule val temporary = TemporaryFolder()
    private val session = "a".repeat(32)
    private val operation = "b".repeat(32)
    private val packageName = "example.test"
    private val combatMap = "agent/action/basics/InterfaceZone/combat_roi_zone_map.json"
    private val policy = DevSourcePolicy(DevSourcePolicySpec(
        files = listOf("interface.json", combatMap),
        roots = mapOf("tasks" to listOf(".json"), "agent" to listOf(".py"), "resource" to listOf(".json", ".png")),
        excluded_prefixes = listOf("agent/tests/"), excluded_segments = listOf("model", "models", "__pycache__"),
        limits = DevSourceLimits(1_048_576, 100, 4_194_304, 1_048_576),
    ))

    private fun file(root: File, path: String, bytes: String): File = File(root, path).also { it.parentFile!!.mkdirs(); it.writeText(bytes) }
    private fun transaction(root: File) = DevSourceTransaction(root, packageName, 2208, policy)
    private fun stage(root: File, op: String = operation, content: String = "new"): DevSourceManifest {
        val items = listOf(DevSourceFile("interface.json", content.toByteArray().size.toLong(), sha256(content.toByteArray())))
        val manifest = DevSourceManifest(1, packageName, 2208, session, op, "", sourceRevision(items), items.map { DevSourceChange(it.path, it.size, it.sha256) }, items)
        val dir = transaction(root).directory(session, op)
        file(dir, "stage/interface.json", content)
        file(dir, "manifest.json", devSessionJson.encodeToString(manifest))
        return manifest
    }
    private fun validate(root: File, manifest: DevSourceManifest): DevSourceManifest {
        val raw = File(transaction(root).directory(session, manifest.operation_id), "manifest.json").readBytes()
        return transaction(root).validate(session, manifest.operation_id, manifest.revision, sha256(raw))
    }

    private fun establishOldBaseline(root: File): DevSourceState {
        file(root, "pi/interface.json", "old")
        val txn = transaction(root)
        val manifest = validate(root, stage(root, content = "old"))
        txn.promote(manifest)
        txn.commit(manifest)
        txn.finishCommit(manifest)
        return txn.readState()!!
    }

    private fun stageCombatMap(root: File, previous: DevSourceState): DevSourceManifest {
        val bytes = "new-generated-map".toByteArray()
        val item = DevSourceFile(combatMap, bytes.size.toLong(), sha256(bytes))
        val items = (previous.files + item).sortedBy { it.path }
        val manifest = DevSourceManifest(1, packageName, 2208, session, "c".repeat(32), previous.revision,
            sourceRevision(items), listOf(DevSourceChange(item.path, item.size, item.sha256)), items)
        val dir = transaction(root).directory(session, manifest.operation_id)
        file(dir, "stage/$combatMap", bytes.toString(Charsets.UTF_8))
        file(dir, "manifest.json", devSessionJson.encodeToString(manifest))
        return validate(root, manifest)
    }

    @Test fun sourcePolicyAllowsOnlyTheExactGeneratedAgentJsonAndStillRejectsDuplicates() {
        policy.requireAllowed(combatMap)
        for (path in listOf("agent/action/basics/InterfaceZone/other.json", "agent/action/combat/config/user.json",
            "agent/config/user.json", "agent/debug/adb_zone_offset.json", "config/user.json", combatMap.uppercase())) {
            assertThrows(IllegalArgumentException::class.java) { policy.requireAllowed(path) }
        }
        val item = DevSourceFile(combatMap, 2, sha256("{}".toByteArray()))
        assertThrows(IllegalArgumentException::class.java) { policy.requireFiles(listOf(item, item)) }
    }

    @Test fun oldBaselineCanCommitNewGeneratedMapWithoutReinstallingPi() {
        val root = temporary.newFolder()
        val previous = establishOldBaseline(root)
        val history = file(root, "pi/debug/user-history.png", "user-history")
        val txn = transaction(root)
        val manifest = stageCombatMap(root, previous)
        assertFalse(File(root, "pi/$combatMap").exists())
        txn.promote(manifest)
        txn.commit(manifest)
        txn.finishCommit(manifest)
        assertEquals("new-generated-map", File(root, "pi/$combatMap").readText())
        assertEquals("old", File(root, "pi/interface.json").readText())
        assertEquals("user-history", history.readText())
        assertEquals(manifest.revision, txn.readState()!!.revision)
        assertTrue(txn.readState()!!.files.any { it.path == combatMap })
        assertTrue(txn.pending().isEmpty())
    }

    @Test fun generatedMapRollbackRestoresUnmanagedOldBytesAndPreviousBaseline() {
        val root = temporary.newFolder()
        val previous = establishOldBaseline(root)
        val previousState = File(root, ".dev-session/source-state.json").readBytes()
        val oldMap = file(root, "pi/$combatMap", "previous-apk-map")
        val privateCache = file(root, "pi/debug/adb_zone_offset.json", "user-layout")
        val txn = transaction(root)
        val manifest = stageCombatMap(root, previous)
        txn.promote(manifest)
        assertEquals("new-generated-map", oldMap.readText())
        txn.rollback(manifest)
        assertEquals("previous-apk-map", oldMap.readText())
        assertArrayEquals(previousState, File(root, ".dev-session/source-state.json").readBytes())
        assertEquals(previous.revision, txn.readState()!!.revision)
        assertEquals("user-layout", privateCache.readText())
        assertFalse(txn.directory(session, manifest.operation_id).exists())
    }

    @Test fun generatedMapRollbackRemovesOnlyTheNewFileWhenPreviouslyAbsent() {
        val root = temporary.newFolder()
        val previous = establishOldBaseline(root)
        val previousState = File(root, ".dev-session/source-state.json").readBytes()
        val privateFile = file(root, "pi/agent/action/basics/InterfaceZone/user.json", "private")
        val txn = transaction(root)
        val manifest = stageCombatMap(root, previous)
        txn.promote(manifest)
        assertTrue(File(root, "pi/$combatMap").isFile)
        txn.rollback(manifest)
        assertFalse(File(root, "pi/$combatMap").exists())
        assertArrayEquals(previousState, File(root, ".dev-session/source-state.json").readBytes())
        assertEquals(previous.revision, txn.readState()!!.revision)
        assertEquals("private", privateFile.readText())
        assertFalse(txn.directory(session, manifest.operation_id).exists())
    }

    @Test fun stagingCannotTouchPiUntilPromotedAndCommitHasReadback() {
        val root = temporary.newFolder()
        val old = file(root, "pi/interface.json", "old")
        file(root, "pi/config/user.json", "private")
        val manifest = validate(root, stage(root))
        assertEquals("old", old.readText())
        val txn = transaction(root)
        txn.promote(manifest)
        assertEquals("new", old.readText())
        assertEquals(1, txn.pending().size)
        txn.commit(manifest)
        assertEquals(manifest.revision, txn.readState()!!.revision)
        assertEquals(1, txn.pending().size)
        txn.finishCommit(manifest)
        assertTrue(txn.pending().isEmpty())
        assertFalse(txn.directory(session, operation).exists())
        assertEquals("private", File(root, "pi/config/user.json").readText())
    }

    @Test fun interruptedPromotionRestoresAllOldBytesAndLeavesConfigurationUntouched() {
        val root = temporary.newFolder()
        val old = file(root, "pi/interface.json", "old")
        file(root, "pi/config/user.json", "private")
        val txn = transaction(root)
        val manifest = validate(root, stage(root))
        var checkpoints = 0
        assertThrows(IllegalStateException::class.java) {
            txn.promote(manifest) { if (++checkpoints == 2) error("injected_after_journal") }
        }
        assertEquals(1, txn.pending().size)
        txn.restore(txn.pending().single())
        assertEquals("old", old.readText())
        assertNull(txn.readState())
        assertTrue(txn.pending().isEmpty())
        assertEquals("private", File(root, "pi/config/user.json").readText())
    }

    @Test fun candidateFailureCanRollBackAfterEveryNewFileWasPromoted() {
        val root = temporary.newFolder()
        val old = file(root, "pi/interface.json", "old")
        val txn = transaction(root)
        val manifest = validate(root, stage(root))
        txn.promote(manifest)
        txn.rollback(manifest)
        assertEquals("old", old.readText())
        assertNull(txn.readState())
        assertFalse(txn.directory(session, operation).exists())
    }

    @Test fun committedRecoveryKeepsNewRevisionRatherThanUndoingIt() {
        val root = temporary.newFolder()
        file(root, "pi/interface.json", "old")
        val txn = transaction(root)
        val manifest = validate(root, stage(root))
        txn.promote(manifest)
        txn.commit(manifest)
        txn.restore(txn.pending().single())
        assertEquals("new", File(root, "pi/interface.json").readText())
        assertEquals(manifest.revision, txn.readState()!!.revision)
        assertTrue(txn.pending().isEmpty())
    }

    @Test fun corruptStageAndManifestNeverTouchExistingFiles() {
        val root = temporary.newFolder()
        val old = file(root, "pi/interface.json", "old")
        val manifest = stage(root)
        file(transaction(root).directory(session, operation), "stage/interface.json", "bad")
        assertThrows(IllegalArgumentException::class.java) { validate(root, manifest) }
        assertEquals("old", old.readText())
        assertTrue(transaction(root).pending().isEmpty())
    }

    @Test fun sourcePolicyProtectsDataModelsTestsAndTraversalWithoutBanningCombatConfig() {
        for (path in listOf("../config/user.json", "config/user.json", "/tasks/a.json", "agent/tests/test.py", "resource/model/a.json", "agent/__pycache__/x.py", "tasks/A:bad.json", "tasks/a\\b.json")) {
            assertThrows(IllegalArgumentException::class.java) { policy.requireAllowed(path) }
        }
        policy.requireAllowed("agent/action/combat/config/role.py")
        policy.requireAllowed("tasks/日常.json")
    }

    @Test fun deletesCannotRemoveAFileOutsideThePreviousManagedManifest() {
        val root = temporary.newFolder()
        val target = file(root, "pi/tasks/private.json", "private")
        val base = stage(root)
        val invalid = base.copy(files = base.files + DevSourceChange("tasks/private.json", 7, sha256("private".toByteArray()), true))
        file(transaction(root).directory(session, operation), "manifest.json", devSessionJson.encodeToString(invalid))
        assertThrows(IllegalArgumentException::class.java) { validate(root, invalid) }
        assertEquals("private", target.readText())
    }

    @Test fun symlinkTargetsAreRejectedWhenSupported() {
        val root = temporary.newFolder()
        val outside = temporary.newFolder()
        val link = File(root, "pi")
        try { Files.createSymbolicLink(link.toPath(), outside.toPath()) }
        catch (_: UnsupportedOperationException) { return }
        catch (_: java.nio.file.FileSystemException) { return }
        val manifest = stage(root)
        assertThrows(IllegalArgumentException::class.java) { validate(root, manifest) }
        assertFalse(File(outside, "interface.json").exists())
    }

    @Test fun upgradeRecoveryWaitsForFullNewApkAndNeverRestoresOldSource() {
        val root = temporary.newFolder()
        file(root, "pi/interface.json", "old")
        val oldTxn = transaction(root)
        val manifest = validate(root, stage(root))
        oldTxn.promote(manifest)
        val upgraded = DevSourceTransaction(root, packageName, 2209, policy)
        val pending = upgraded.pending().single()
        assertThrows(IllegalStateException::class.java) {
            upgraded.recoverForCurrentApk(pending) { error("injected_unpack_failure") }
        }
        assertEquals("new", File(root, "pi/interface.json").readText())
        assertEquals(1, upgraded.pending().size)
        assertThrows(IllegalArgumentException::class.java) { upgraded.recoverForCurrentApk(pending) {} }
        upgraded.recoverForCurrentApk(pending) {
            file(root, "pi/interface.json", "new-apk")
            file(root, "pi.version", "2209")
        }
        assertEquals("new-apk", File(root, "pi/interface.json").readText())
        assertTrue(upgraded.pending().isEmpty())
        assertNull(upgraded.readState())
    }

    @Test fun successfulOperationsDoNotAccumulateUntilTheStagingLimit() {
        val root = temporary.newFolder()
        file(root, "pi/interface.json", "old")
        val txn = transaction(root)
        repeat(70) { index ->
            val op = "%032x".format(index + 1)
            val content = "new-$index"
            val previous = txn.readState()
            val baseline = stage(root, op, content)
            val manifest = baseline.copy(base_revision = previous?.revision.orEmpty())
            file(txn.directory(session, op), "manifest.json", devSessionJson.encodeToString(manifest))
            validate(root, manifest)
            txn.promote(manifest)
            txn.commit(manifest)
            txn.finishCommit(manifest)
        }
        assertTrue(txn.pending().isEmpty())
        assertFalse(File(root, ".dev-session/reload/$session").exists())
    }

    @Test fun failedUnpromotedUploadsNeverCountAsRecoveryOrDeleteTheActiveUploader() {
        val root = temporary.newFolder()
        file(root, "pi/interface.json", "old")
        val txn = transaction(root)
        repeat(70) { index -> stage(root, "%032x".format(index + 1), "bad-$index") }
        assertTrue(txn.pending().isEmpty())
        txn.discardAbandonedSessions(setOf(session))
        assertEquals(70, File(root, ".dev-session/reload/$session").listFiles()!!.size)
        txn.discardAbandonedSessions(emptySet())
        assertTrue(txn.pending().isEmpty())
        assertFalse(File(root, ".dev-session/reload/$session").exists())
        assertEquals("old", File(root, "pi/interface.json").readText())
    }

    @Test fun orphanCleanupPreservesRecoveryJournalAndItsBackup() {
        val root = temporary.newFolder()
        file(root, "pi/interface.json", "old")
        val txn = transaction(root)
        val manifest = validate(root, stage(root))
        txn.promote(manifest)
        txn.discardAbandonedSessions(emptySet())
        assertEquals(1, txn.pending().size)
        txn.restore(txn.pending().single())
        assertEquals("old", File(root, "pi/interface.json").readText())
    }
}
