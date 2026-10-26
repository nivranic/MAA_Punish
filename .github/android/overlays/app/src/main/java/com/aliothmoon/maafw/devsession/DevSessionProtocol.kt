package com.aliothmoon.maafw.devsession

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.UUID

const val DEV_SESSION_PROTOCOL = 1
const val DEV_SESSION_MAX_BYTES = 16_384
const val DEV_SESSION_LEASE_MS = 8_000L

private fun newToken(): String = UUID.randomUUID().toString().replace("-", "")
private val tokenPattern = Regex("[a-f0-9]{32}")

// Strict decoding deliberately rejects unknown fields and operations. This is a
// USB session protocol, not a shell/task/path RPC or the privileged heartbeat.
val devSessionJson = Json { encodeDefaults = true; ignoreUnknownKeys = false }

@Serializable
data class DevSessionRequest(
    val version: Int,
    val `package`: String,
    val app_instance: String,
    val challenge: String,
    val host_id: String,
    val session_id: String,
    val request_id: String,
    val sequence: Long,
    val operation: String,
    val audit_logging: Boolean = false,
)

@Serializable
data class DevSessionHello(
    val version: Int,
    val `package`: String,
    val app_instance: String,
    val challenge: String,
    val max_message_bytes: Int,
    val audit_logging_supported: Boolean = true,
)

@Serializable
data class DevSessionStatus(
    val version: Int,
    val `package`: String,
    val app_instance: String,
    val challenge: String,
    val phase: String,
    val session_id: String,
    val host_id: String,
    val last_sequence: Long,
    val lease_remaining_ms: Long,
    val request_id: String,
    val result: String,
    val reason: String,
    val screen_saver_status: String = "inactive",
    val audit_logging: Boolean = false,
)

data class DevSessionLease(val sessionId: String, val hostId: String, val auditLogging: Boolean = false)

/** Single-threaded state machine. Deadlines use only the phone's monotonic clock.
 * A host ID labels an authorized USB ADB session; it is not host authentication.
 * New process instances always get a new challenge, regardless of disk contents.
 */
class DevSessionProtocol(
    private val packageName: String,
    private val nowMs: () -> Long,
    private val token: () -> String = ::newToken,
) {
    private val appInstance = token()
    private var challenge = token()
    private var phase = "idle"
    var lease: DevSessionLease? = null
        private set
    private var deadlineMs = 0L
    private var lastSequence = 0L
    private var lastRequestId = ""
    private var result = ""
    private var reason = ""

    fun hello() = DevSessionHello(
        DEV_SESSION_PROTOCOL, packageName, appInstance, challenge, DEV_SESSION_MAX_BYTES,
    )

    fun status(): DevSessionStatus = DevSessionStatus(
        DEV_SESSION_PROTOCOL, packageName, appInstance, challenge, phase,
        lease?.sessionId.orEmpty(), lease?.hostId.orEmpty(), lastSequence,
        if (lease == null) 0 else (deadlineMs - nowMs()).coerceAtLeast(0),
        lastRequestId, result, reason,
        audit_logging = lease?.auditLogging == true,
    )

    /** True only when expiry changed the state. Disk replay cannot renew it. */
    fun tick(): Boolean {
        if (lease == null || nowMs() < deadlineMs) return false
        end("expired", "heartbeat_timeout")
        return true
    }

    fun rejectInvalid(): DevSessionStatus {
        tick()
        lastRequestId = ""
        result = "rejected"
        reason = "invalid_message"
        return status()
    }

    fun receive(request: DevSessionRequest): DevSessionStatus {
        tick()
        fun reject(why: String): DevSessionStatus {
            lastRequestId = request.request_id.takeIf(tokenPattern::matches).orEmpty()
            result = "rejected"
            reason = why
            return status()
        }
        if (request.version != DEV_SESSION_PROTOCOL || request.`package` != packageName) return reject("protocol_mismatch")
        if (request.app_instance != appInstance || request.challenge != challenge) return reject("stale_challenge")
        if (listOf(request.host_id, request.session_id, request.request_id).any { !tokenPattern.matches(it) } ||
            (if (request.operation == "close") request.sequence != 0L
             else request.sequence !in 1..9_007_199_254_740_991L)
        ) return reject("invalid_identity")
        if (request.operation !in setOf("open", "heartbeat", "close")) return reject("unknown_operation")

        val requestedLease = DevSessionLease(request.session_id, request.host_id, request.audit_logging)
        if (lease != null && (lease?.sessionId != request.session_id || lease?.hostId != request.host_id)) return reject("session_busy")
        if (lease != null && lease?.auditLogging != request.audit_logging) return reject("audit_mode_mismatch")
        // Close has a separate fixed mailbox/sequence space so a second CLI
        // terminal cannot overwrite the session owner's heartbeat messages.
        if (request.operation == "close") {
            if (lease != requestedLease) return reject("session_not_open")
            lastRequestId = request.request_id
            result = "accepted"
            end("closed", "host_closed")
            return status()
        }
        // A duplicate is an acknowledgement only: it never extends the lease.
        if (request.sequence == lastSequence && request.request_id == lastRequestId && result == "accepted") return status()
        if (request.sequence <= lastSequence) return reject("stale_sequence")
        if (request.operation == "open") {
            if (lease != null || request.sequence != 1L) return reject("already_open_or_bad_sequence")
            lease = requestedLease
            phase = "active"
        } else if (lease != requestedLease) {
            return reject("session_not_open")
        }
        lastSequence = request.sequence
        lastRequestId = request.request_id
        result = "accepted"
        reason = ""
        deadlineMs = nowMs() + DEV_SESSION_LEASE_MS
        return status()
    }

    fun end(endedPhase: String = "closed", why: String = "phone_closed") {
        lease = null
        deadlineMs = 0
        phase = endedPhase
        reason = why
        challenge = token()
        // Keep the final request acknowledgement, but the new challenge starts
        // a fresh sequence and prevents replay after close/timeout/process death.
        lastSequence = 0
    }
}
