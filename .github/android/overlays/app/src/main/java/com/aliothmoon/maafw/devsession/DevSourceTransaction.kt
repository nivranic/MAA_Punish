package com.aliothmoon.maafw.devsession

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import java.io.File
import java.io.FileOutputStream
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.StandardCopyOption
import java.nio.file.Path
import java.nio.file.SimpleFileVisitor
import java.nio.file.FileVisitResult
import java.nio.file.attribute.BasicFileAttributes

@Serializable
data class DevBackupEntry(val path: String, val existed: Boolean, val size: Long, val sha256: String)

@Serializable
data class DevSourceJournal(
    val manifest: DevSourceManifest,
    val originals: List<DevBackupEntry>,
    val previous_state: String?,
    val phase: String = "promoting",
)

/** No native calls and no recursive mirror/delete. The caller owns the native stop barrier. */
class DevSourceTransaction(
    private val filesRoot: File,
    private val packageName: String,
    private val apkVersion: Long,
    private val policy: DevSourcePolicy,
) {
    private val piRoot = File(filesRoot, "pi")
    private val mailboxRoot = File(filesRoot, ".dev-session")
    private val sourceState: File get() = safeFile(mailboxRoot, "source-state.json")

    fun directory(session: String, operation: String): File {
        require(devToken.matches(session) && devToken.matches(operation)) { "invalid_transaction_id" }
        return safeFile(mailboxRoot, "reload/$session/$operation")
    }

    fun readState(): DevSourceState? {
        if (!sourceState.exists()) return null
        val result = devSessionJson.decodeFromString<DevSourceState>(readLimited(sourceState))
        require(result.version == 1 && result.`package` == packageName) { "source_state_package_mismatch" }
        if (result.apk_version_code != apkVersion) {
            require(result.apk_version_code < apkVersion) { "source_state_from_newer_apk" }
            requireCurrentApkInstalled()
            require(pending().isEmpty()) { "old_apk_transaction_recovery_required" }
            Files.delete(sourceState.toPath())
            return null
        }
        policy.requireFiles(result.files)
        require(result.revision == sourceRevision(result.files)) { "source_state_hash_mismatch" }
        return result
    }

    fun validate(session: String, operation: String, revision: String, manifestHash: String): DevSourceManifest {
        val dir = directory(session, operation)
        val raw = readLimited(safeFile(dir, "manifest.json"))
        require(devHash.matches(manifestHash) && sha256(raw.toByteArray(Charsets.UTF_8)) == manifestHash) { "manifest_hash_mismatch" }
        val manifest = devSessionJson.decodeFromString<DevSourceManifest>(raw)
        require(manifest.version == 1 && manifest.`package` == packageName && manifest.apk_version_code == apkVersion) { "manifest_apk_mismatch" }
        require(manifest.session_id == session && manifest.operation_id == operation && manifest.revision == revision) { "manifest_binding_mismatch" }
        policy.requireFiles(manifest.managed_files)
        require(manifest.revision == sourceRevision(manifest.managed_files)) { "revision_mismatch" }
        require(manifest.files.size <= policy.spec.limits.max_files) { "change_count_limit" }
        require(manifest.files.map { it.path.lowercase() }.toSet().size == manifest.files.size) { "duplicate_change" }
        val previous = readState()
        require(manifest.base_revision == previous?.revision.orEmpty()) { "base_revision_mismatch" }
        val old = previous?.files.orEmpty().associateBy { it.path }
        val next = manifest.managed_files.associateBy { it.path }
        val changed = manifest.files.associateBy { it.path }
        for (change in manifest.files) {
            policy.requireAllowed(change.path)
            val target = safeFile(piRoot, change.path)
            require(!target.exists() || target.isFile) { "target_not_regular_file" }
            if (change.delete) {
                require(change.path in old && change.path !in next && change.size == old.getValue(change.path).size && change.sha256 == old.getValue(change.path).sha256) { "unmanaged_delete" }
                verifyFile(target, old.getValue(change.path))
            } else {
                val expected = next[change.path] ?: error("change_not_managed")
                require(change.size == expected.size && change.sha256 == expected.sha256) { "change_metadata_mismatch" }
                verifyFile(safeFile(dir, "stage/${change.path}"), expected)
                old[change.path]?.let { verifyFile(target, it) }
            }
        }
        for ((path, item) in next) {
            if (path !in changed) verifyFile(safeFile(piRoot, path), item)
            // A full new manifest must describe every old managed removal.
        }
        require((old.keys - next.keys).all { changed[it]?.delete == true }) { "missing_managed_delete" }
        val space = manifest.files.filterNot { it.delete }.sumOf { it.size } +
            manifest.files.sumOf { safeFile(piRoot, it.path).takeIf(File::isFile)?.length() ?: 0L } + 1_048_576L
        require(filesRoot.usableSpace >= space) { "insufficient_space" }
        return manifest
    }

    /** Must only be called after STOPPED. All backup bytes and the journal precede the first PI write. */
    fun promote(manifest: DevSourceManifest, checkpoint: () -> Unit = {}) {
        val dir = directory(manifest.session_id, manifest.operation_id)
        require(!safeFile(dir, "journal.json").exists()) { "transaction_already_started" }
        val originals = manifest.files.map { change ->
            checkpoint()
            val target = safeFile(piRoot, change.path)
            if (target.exists()) {
                require(target.isFile) { "target_not_regular_file" }
                val bytes = readFile(target, policy.spec.limits.max_file_bytes)
                atomicBytes(safeFile(dir, "backup/${change.path}"), bytes)
                DevBackupEntry(change.path, true, bytes.size.toLong(), sha256(bytes))
            } else DevBackupEntry(change.path, false, 0, "")
        }
        val previous = sourceState.takeIf(File::exists)?.let(::readLimited)
        val journal = DevSourceJournal(manifest, originals, previous)
        atomicText(safeFile(dir, "journal.json"), devSessionJson.encodeToString(journal))
        // After this point the caller must commit or roll back even if USB disappears.
        for (change in manifest.files) {
            checkpoint()
            val target = safeFile(piRoot, change.path)
            if (change.delete) Files.deleteIfExists(target.toPath())
            else {
                val expected = manifest.managed_files.first { it.path == change.path }
                val stage = safeFile(dir, "stage/${change.path}")
                verifyFile(stage, expected)
                atomicBytes(target, readFile(stage, policy.spec.limits.max_file_bytes))
            }
        }
        verifyManaged(manifest.managed_files)
    }

    fun commit(manifest: DevSourceManifest) {
        val dir = directory(manifest.session_id, manifest.operation_id)
        val journalFile = safeFile(dir, "journal.json")
        require(journalFile.isFile) { "missing_recovery_journal" }
        verifyManaged(manifest.managed_files)
        val state = DevSourceState(`package` = packageName, apk_version_code = apkVersion, revision = manifest.revision, files = manifest.managed_files.sortedBy { it.path })
        atomicText(sourceState, devSessionJson.encodeToString(state))
        // COMMITTED is durable before unlink: a crash here must not undo a valid activation.
        val journal = devSessionJson.decodeFromString<DevSourceJournal>(readLimited(journalFile))
        atomicText(journalFile, devSessionJson.encodeToString(journal.copy(phase = "committed")))
        // Keep the committed journal until native RELEASE and UI publication are acknowledged.
    }

    fun finishCommit(manifest: DevSourceManifest) {
        val journalFile = safeFile(directory(manifest.session_id, manifest.operation_id), "journal.json")
        val journal = devSessionJson.decodeFromString<DevSourceJournal>(readLimited(journalFile))
        require(journal.phase == "committed" && readState()?.revision == manifest.revision) { "commit_ack_missing" }
        Files.delete(journalFile.toPath())
        runCatching { discardStaging(manifest) }
    }

    fun hasJournal(manifest: DevSourceManifest): Boolean =
        safeFile(directory(manifest.session_id, manifest.operation_id), "journal.json").isFile

    /** Caller must first release candidate native readers. Idempotent after interrupted rollback. */
    fun rollback(manifest: DevSourceManifest) {
        restore(directory(manifest.session_id, manifest.operation_id))
        discardStaging(manifest)
    }

    /** Remove only this validated transaction's known artifacts; never mirror/delete the PI. */
    fun discardStaging(manifest: DevSourceManifest) {
        val dir = directory(manifest.session_id, manifest.operation_id)
        require(!safeFile(dir, "journal.json").exists()) { "cannot_clean_active_recovery" }
        for (change in manifest.files) {
            policy.requireAllowed(change.path)
            for (prefix in listOf("stage", "backup")) {
                val file = safeFile(dir, "$prefix/${change.path}")
                if (file.exists()) {
                    require(file.isFile) { "unexpected_staging_artifact" }
                    Files.delete(file.toPath())
                }
                var parent = file.parentFile
                while (parent != null && parent.canonicalFile != dir.canonicalFile && parent.isDirectory && parent.list()?.isEmpty() == true) {
                    require(parent.canonicalFile.toPath().startsWith(dir.canonicalFile.toPath()))
                    Files.delete(parent.toPath())
                    parent = parent.parentFile
                }
            }
        }
        Files.deleteIfExists(safeFile(dir, "manifest.json").toPath())
        if (dir.isDirectory && dir.list()?.isEmpty() == true) Files.delete(dir.toPath())
        val sessionDir = dir.parentFile
        if (sessionDir.isDirectory && sessionDir.list()?.isEmpty() == true) Files.delete(sessionDir.toPath())
    }

    fun pending(): List<File> {
        val root = safeFile(mailboxRoot, "reload")
        if (!root.exists()) return emptyList()
        require(root.isDirectory) { "invalid_reload_directory" }
        val sessions = checkNotNull(root.listFiles()) { "recovery_directory_unreadable" }
        val journals = sessions.filter { devToken.matches(it.name) }.flatMap { session ->
            safeFile(root, session.name)
            require(session.isDirectory) { "invalid_session_directory" }
            val operations = checkNotNull(session.listFiles()) { "recovery_session_unreadable" }
            operations.filter { devToken.matches(it.name) }.mapNotNull { operation ->
                val dir = safeFile(session, operation.name)
                require(dir.isDirectory) { "invalid_operation_directory" }
                dir.takeIf { safeFile(it, "journal.json").isFile }
            }
        }
        require(journals.size <= 64) { "too_many_recovery_journals" }
        return journals
    }

    /** Only for a finished APPLY or an expired session; never called for the active uploader. */
    fun discardUnapplied(session: String, operation: String) {
        val dir = directory(session, operation)
        if (!dir.exists() || safeFile(dir, "journal.json").exists()) return
        require(dir.isDirectory) { "invalid_staging_directory" }
        val entries = checkNotNull(dir.listFiles()) { "staging_directory_unreadable" }
        require(entries.all { it.name in setOf("stage", "backup", "manifest.json", ".manifest.json.reload-tmp", ".journal.json.reload-tmp") }) { "unknown_staging_artifact" }
        for (entry in entries) {
            val item = safeFile(dir, entry.name)
            if (entry.name in setOf("stage", "backup")) {
                require(item.isDirectory) { "invalid_staging_tree" }
                Files.walkFileTree(item.toPath(), object : SimpleFileVisitor<Path>() {
                    override fun visitFile(path: Path, attrs: BasicFileAttributes): FileVisitResult {
                        require(attrs.isRegularFile && !attrs.isSymbolicLink) { "unsafe_staging_artifact" }
                        val relative = item.toPath().relativize(path).joinToString("/")
                        policy.requireAllowed(relative)
                        Files.delete(safeFile(item, relative).toPath())
                        return FileVisitResult.CONTINUE
                    }
                    override fun postVisitDirectory(path: Path, failure: java.io.IOException?): FileVisitResult {
                        if (failure != null) throw failure
                        require(!Files.isSymbolicLink(path) && path.toAbsolutePath().startsWith(item.toPath().toAbsolutePath()))
                        Files.delete(path)
                        return FileVisitResult.CONTINUE
                    }
                })
            } else {
                require(item.isFile) { "invalid_staging_metadata" }
                Files.delete(item.toPath())
            }
        }
        if (dir.list()?.isEmpty() == true) Files.delete(dir.toPath())
        if (dir.parentFile.list()?.isEmpty() == true) Files.delete(dir.parentFile.toPath())
    }

    fun discardAbandonedSessions(retainedSessions: Set<String>) {
        val reload = safeFile(mailboxRoot, "reload")
        if (!reload.exists()) return
        for (session in checkNotNull(reload.listFiles()) { "staging_directory_unreadable" }) {
            if (!devToken.matches(session.name) || session.name in retainedSessions) continue
            val folder = safeFile(reload, session.name)
            require(folder.isDirectory) { "invalid_session_directory" }
            for (operation in checkNotNull(folder.listFiles()) { "staging_session_unreadable" }) {
                if (devToken.matches(operation.name)) discardUnapplied(session.name, operation.name)
            }
        }
    }

    fun restore(dir: File) {
        val journalFile = safeFile(dir, "journal.json")
        if (!journalFile.exists()) return
        val journal = devSessionJson.decodeFromString<DevSourceJournal>(readLimited(journalFile))
        require(journal.manifest.`package` == packageName && journal.manifest.apk_version_code == apkVersion) { "recovery_apk_mismatch" }
        require(directory(journal.manifest.session_id, journal.manifest.operation_id).canonicalFile == dir.canonicalFile) { "recovery_directory_mismatch" }
        require(journal.phase in setOf("promoting", "committed")) { "invalid_recovery_phase" }
        policy.requireFiles(journal.manifest.managed_files)
        if (journal.phase == "committed") {
            verifyManaged(journal.manifest.managed_files)
            val state = readState()
            require(state?.revision == journal.manifest.revision) { "committed_state_missing" }
            Files.delete(journalFile.toPath())
            runCatching { discardStaging(journal.manifest) }
            return
        }
        require(journal.originals.map { it.path }.toSet() == journal.manifest.files.map { it.path }.toSet()) { "recovery_backup_mismatch" }
        // Verify every backup before restoring any file.
        journal.originals.forEach {
            policy.requireAllowed(it.path)
            if (it.existed) verifyFile(safeFile(dir, "backup/${it.path}"), DevSourceFile(it.path, it.size, it.sha256))
        }
        for (entry in journal.originals.asReversed()) {
            val target = safeFile(piRoot, entry.path)
            if (entry.existed) atomicBytes(target, readFile(safeFile(dir, "backup/${entry.path}"), policy.spec.limits.max_file_bytes))
            else Files.deleteIfExists(target.toPath())
        }
        journal.previous_state?.let { atomicText(sourceState, it) } ?: Files.deleteIfExists(sourceState.toPath())
        journal.originals.forEach {
            val target = safeFile(piRoot, it.path)
            if (it.existed) verifyFile(target, DevSourceFile(it.path, it.size, it.sha256))
            else require(!target.exists()) { "rollback_delete_failed" }
        }
        Files.delete(journalFile.toPath())
        runCatching { discardStaging(journal.manifest) }
    }

    /** Native readers must already be stopped. Never restore old source across an APK upgrade. */
    fun recoverForCurrentApk(dir: File, installCurrent: () -> Unit) {
        val journalFile = safeFile(dir, "journal.json")
        val journal = devSessionJson.decodeFromString<DevSourceJournal>(readLimited(journalFile))
        require(journal.manifest.`package` == packageName) { "recovery_package_mismatch" }
        require(directory(journal.manifest.session_id, journal.manifest.operation_id).canonicalFile == dir.canonicalFile) { "recovery_directory_mismatch" }
        if (journal.manifest.apk_version_code == apkVersion) return restore(dir)
        require(journal.manifest.apk_version_code < apkVersion) { "recovery_journal_from_newer_apk" }
        // The existing non-wiping upgrade path writes pi.version only after complete unpacking.
        // Installation failures retain the journal/guard and never restore old bytes.
        installCurrent()
        requireCurrentApkInstalled()
        Files.deleteIfExists(sourceState.toPath())
        Files.delete(journalFile.toPath())
        runCatching { discardStaging(journal.manifest) }
    }

    private fun requireCurrentApkInstalled() {
        val marker = safeFile(filesRoot, "pi.version")
        require(marker.isFile && marker.length() <= 32 && marker.readText().trim() == apkVersion.toString()) { "current_apk_not_fully_installed" }
    }

    private fun verifyManaged(files: List<DevSourceFile>) = files.forEach { verifyFile(safeFile(piRoot, it.path), it) }

    private fun verifyFile(file: File, expected: DevSourceFile) {
        require(file.isFile && file.length() == expected.size) { "source_file_size_mismatch" }
        require(sha256(readFile(file, policy.spec.limits.max_file_bytes)) == expected.sha256) { "source_file_hash_mismatch" }
    }

    private fun readLimited(file: File): String = readFile(file, policy.spec.limits.max_manifest_bytes).toString(Charsets.UTF_8)

    private fun readFile(file: File, maxBytes: Long): ByteArray {
        require(file.isFile && file.length() in 0..maxBytes) { "file_size_limit" }
        return file.inputStream().use { stream ->
            val result = ByteArrayOutputStream()
            val buffer = ByteArray(8192)
            while (true) {
                val count = stream.read(buffer)
                if (count < 0) break
                require(result.size().toLong() + count <= maxBytes) { "file_size_limit" }
                result.write(buffer, 0, count)
            }
            result.toByteArray()
        }
    }

    internal fun safeFile(root: File, relative: String): File {
        require(!relative.startsWith('/') && '\\' !in relative && ':' !in relative && relative.split('/').none { it.isEmpty() || it == "." || it == ".." }) { "invalid_transaction_path" }
        var current = root.absoluteFile
        require(!Files.isSymbolicLink(current.toPath())) { "symlink_path" }
        // Reject links in the root's existing ancestors too.
        var ancestor: File? = current
        while (ancestor != null) {
            require(!Files.isSymbolicLink(ancestor.toPath())) { "symlink_path" }
            ancestor = ancestor.parentFile
        }
        for (segment in relative.split('/')) {
            current = File(current, segment)
            require(!Files.isSymbolicLink(current.toPath())) { "symlink_path" }
            require(!Files.exists(current.toPath(), LinkOption.NOFOLLOW_LINKS) || current.isFile || current.isDirectory) { "special_file_path" }
        }
        require(current.canonicalFile.toPath().startsWith(root.canonicalFile.toPath())) { "path_escape" }
        return current
    }

    private fun atomicText(file: File, text: String) = atomicBytes(file, text.toByteArray(Charsets.UTF_8))

    private fun atomicBytes(file: File, bytes: ByteArray) {
        file.parentFile!!.mkdirs()
        val temp = File(file.parentFile, ".${file.name}.reload-tmp")
        require(!Files.isSymbolicLink(temp.toPath())) { "symlink_temporary_file" }
        FileOutputStream(temp).use { it.write(bytes); it.fd.sync() }
        // Unsupported atomic replacement fails closed; never delete the old target as a fallback.
        Files.move(temp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }
}
