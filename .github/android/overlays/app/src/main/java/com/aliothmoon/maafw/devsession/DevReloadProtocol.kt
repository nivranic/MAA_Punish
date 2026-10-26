package com.aliothmoon.maafw.devsession

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import java.security.MessageDigest

@Serializable
data class DevSourceFile(val path: String, val size: Long, val sha256: String)

@Serializable
data class DevSourceChange(
    val path: String,
    val size: Long = 0,
    val sha256: String = "",
    val delete: Boolean = false,
)

@Serializable
data class DevSourceManifest(
    val version: Int,
    val `package`: String,
    val apk_version_code: Long,
    val session_id: String,
    val operation_id: String,
    val base_revision: String,
    val revision: String,
    val files: List<DevSourceChange>,
    val managed_files: List<DevSourceFile>,
)

@Serializable
data class DevSourceState(
    val version: Int = 1,
    val `package`: String,
    val apk_version_code: Long,
    val revision: String,
    val files: List<DevSourceFile>,
)

@Serializable
data class DevReloadRequest(
    val version: Int,
    val `package`: String,
    val app_instance: String,
    val challenge: String,
    val host_id: String,
    val session_id: String,
    val request_id: String,
    val operation: String,
    val operation_id: String,
    val revision: String = "",
    val manifest_sha256: String = "",
)

@Serializable
data class DevReloadStatus(
    val version: Int = 1,
    val session_id: String = "",
    val operation_id: String = "",
    val request_id: String = "",
    val phase: String = "idle",
    val revision: String = "",
    val reason: String = "",
    val rolled_back: Boolean = false,
    val native_generation: Long = 0,
    val agent_generation: Long = 0,
    val display_id: Int = -1,
    val controller_reused: Boolean = false,
    val resource_loaded: Boolean = false,
    val agent_loaded: Boolean = false,
)

/** Private app-to-privileged-process protocol. Never accepted from the USB mailbox. */
@Serializable
data class DevNativeRequest(
    val operation: String,
    val operation_id: String,
    val timeout_ms: Long = 15_000,
    val revision: String = "",
    val payload: String = "",
)

@Serializable
data class DevNativeStatus(
    val operation_id: String = "",
    val phase: String = "idle",
    val safe_stopped: Boolean = false,
    val revision: String = "",
    val reason: String = "",
    val native_generation: Long = 0,
    val agent_generation: Long = 0,
    val display_id: Int = -1,
    val controller_reused: Boolean = false,
    val prepared_payload: String = "",
)

internal val devToken = Regex("[a-f0-9]{32}")
internal val devHash = Regex("[a-f0-9]{64}")

internal fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }

internal fun sourceRevision(files: List<DevSourceFile>): String =
    sha256(devSessionJson.encodeToString(files.sortedBy { it.path }).toByteArray(Charsets.UTF_8))
