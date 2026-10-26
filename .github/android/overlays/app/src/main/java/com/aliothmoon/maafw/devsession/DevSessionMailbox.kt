package com.aliothmoon.maafw.devsession

import java.io.File
import java.io.FileOutputStream
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

/** Only fixed JSON files below the application's external files root.
 * Same-directory rename publishes a complete message. No PI/user paths are read
 * or modified, and no recursive deletion or arbitrary path API is exposed.
 */
class DevSessionMailbox(externalFilesRoot: File) {
    private val parent = externalFilesRoot.canonicalFile
    private val directory = File(parent, ".dev-session")

    fun prepare() {
        check(directory.canonicalFile == directory.absoluteFile) { "Session directory must not be a symlink" }
        check(directory.isDirectory || directory.mkdir()) { "Session directory unavailable" }
    }

    private fun file(name: String): File {
        require(name in setOf("hello.json", "state.json", "request.json", "close.json",
            "reload-request.json", "reload-state.json", "reload-ack.json",
            "audit-request.json", "audit-state.json", "audit-permit.json"))
        prepare()
        return File(directory, name).also {
            check(it.canonicalFile == it.absoluteFile) { "Mailbox file must not be a symlink" }
        }
    }

    fun readRequest(name: String = "request.json"): String? {
        require(name in setOf("request.json", "close.json", "reload-request.json", "audit-request.json"))
        val request = file(name)
        if (!request.exists()) return null
        check(request.isFile && request.length() <= DEV_SESSION_MAX_BYTES) { "Invalid request size/type" }
        return request.inputStream().use {
            // InputStream.readNBytes is not available on every supported Android.
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(1024)
            while (output.size() <= DEV_SESSION_MAX_BYTES) {
                val count = it.read(buffer, 0, minOf(buffer.size, DEV_SESSION_MAX_BYTES + 1 - output.size()))
                if (count == -1) break
                output.write(buffer, 0, count)
            }
            val bytes = output.toByteArray()
            check(bytes.size <= DEV_SESSION_MAX_BYTES) { "Request exceeds limit" }
            bytes.toString(Charsets.UTF_8)
        }
    }

    fun publish(name: String, contents: String) {
        require(name in setOf("hello.json", "state.json", "reload-state.json", "reload-ack.json",
            "audit-state.json", "audit-permit.json"))
        val destination = file(name)
        val bytes = contents.toByteArray(Charsets.UTF_8)
        require(bytes.size <= DEV_SESSION_MAX_BYTES)
        val temporary = File(directory, ".$name.${UUID.randomUUID()}.tmp")
        try {
            FileOutputStream(temporary).use { it.write(bytes); it.fd.sync() }
            // File.renameTo cannot replace an existing destination on Windows.
            // NIO is supported from Android API 26 (this app's minSdk is 28).
            // Fail closed if atomic replacement is unsupported; never delete
            // the published file first or silently use a partial-write fallback.
            Files.move(temporary.toPath(), destination.toPath(),
                StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally {
            temporary.delete()
        }
    }
}
