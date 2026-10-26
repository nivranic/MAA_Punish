package com.aliothmoon.maafw.devsession

import java.io.File
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/** Runtime identity is carried by this run's Pipeline attach, never by a reusable agent's environment. */
data class DevAuditRunBinding(
    val packageName: String,
    val appInstance: String,
    val lease: DevSessionLease,
    val executionId: String,
    val runNonce: String,
    val mailboxRoot: String,
) {
    fun attach(): JsonObject = buildJsonObject {
        put("enabled", true)
        put("package", packageName)
        put("app_instance", appInstance)
        put("session_id", lease.sessionId)
        put("execution_id", executionId)
        put("run_nonce", runNonce)
        put("mailbox_root", mailboxRoot)
    }

    internal fun identity(): Map<String, JsonPrimitive> = mapOf(
        "package" to JsonPrimitive(packageName), "app_instance" to JsonPrimitive(appInstance),
        "session_id" to JsonPrimitive(lease.sessionId), "execution_id" to JsonPrimitive(executionId),
        "run_nonce" to JsonPrimitive(runNonce),
    )
}

/** Small in-memory authority. No stdout parsing, task dispatch, file I/O, or independent scheduler. */
class DevAuditEvidence(
    private val packageName: String,
    mailboxRoot: File,
    private val nowMs: () -> Long,
    private val currentHello: () -> DevSessionHello?,
    private val currentLease: () -> DevSessionLease?,
) {
    private val mailboxRoot = mailboxRoot.absoluteFile.normalize().path
    private var active: DevAuditRunBinding? = null
    private var last: DevAuditRunBinding? = null
    private var startedMs = 0L
    private var channel: JsonObject? = null
    private var channelExpiresMs = 0L
    private var permit: JsonObject? = null
    private val usedNonces = mutableSetOf<String>()
    private val usedChallenges = mutableSetOf<String>()
    private var invalidRequest = false
    private var revision = 0L

    init {
        require(packageName.isNotBlank())
        require(mailboxRoot.isAbsolute && mailboxRoot.name == ".dev-session" &&
            mailboxRoot.absoluteFile.normalize() == mailboxRoot.absoluteFile) { "invalid_audit_mailbox_root" }
    }

    /** May run while Preparing. It creates identity only; register is the dispatch boundary. */
    @Synchronized fun binding(lease: DevSessionLease, executionId: String, nonce: String): DevAuditRunBinding? {
        val hello = currentHello() ?: return null
        if (!lease.auditLogging || currentLease() != lease || hello.`package` != packageName ||
            !token.matches(hello.app_instance) || !token.matches(lease.sessionId) ||
            !name.matches(executionId) || !token.matches(nonce)
        ) return null
        return DevAuditRunBinding(packageName, hello.app_instance, lease, executionId, nonce, mailboxRoot)
    }

    private fun authoritative(run: DevAuditRunBinding): Boolean =
        run.lease.auditLogging && currentLease() == run.lease &&
            currentHello()?.let { it.`package` == run.packageName && it.app_instance == run.appInstance } == true

    @Synchronized fun register(run: DevAuditRunBinding): Boolean {
        expire()
        if (!authoritative(run) || active != null) return false
        active = run
        last = run
        startedMs = nowMs()
        usedNonces.clear()
        usedChallenges.clear()
        invalidRequest = false
        // A new run waits for its own first challenge. Retaining the idle/old
        // permit identity makes the strict Python consumer reject that wait.
        permit = null
        revokeChannel()
        return true
    }

    /** An old callback cannot revoke a later run, even if the execution ID was accidentally reused. */
    @Synchronized fun finish(run: DevAuditRunBinding) {
        if (active != run) return
        active = null
        revokeChannel()
    }

    @Synchronized fun invalidate() {
        active = null
        revokeChannel()
    }

    @Synchronized fun rejectInvalid() {
        invalidRequest = true
        revokeChannel()
    }

    private fun revoked(message: JsonObject? = permit): JsonObject {
        val base = message ?: buildJsonObject {
            put("version", 1)
            val identity = last?.identity() ?: mapOf(
                "package" to JsonPrimitive(packageName),
                "app_instance" to JsonPrimitive(currentHello()?.app_instance ?: "0".repeat(32)),
                "session_id" to JsonPrimitive("0".repeat(32)),
                "execution_id" to JsonPrimitive("idle"), "run_nonce" to JsonPrimitive("0".repeat(32)),
            )
            identity.forEach { (key, value) -> put(key, value) }
            put("collector_id", "0".repeat(32)); put("source_challenge", "0".repeat(32))
            put("request_id", newNonce()); put("operation", "revoke")
        }
        return JsonObject(base + mapOf("phase" to JsonPrimitive("revoked"),
            "observed_boottime_ms" to JsonPrimitive(nowMs()), "permit_expires_boottime_ms" to JsonPrimitive(0)))
    }

    private fun revokeChannel() {
        channel = null
        channelExpiresMs = 0
        permit = revoked()
        revision++
    }

    private fun expire() {
        val run = active
        if (run != null && !authoritative(run)) {
            active = null
            revokeChannel()
        } else if (channel != null && nowMs() >= channelExpiresMs) {
            revokeChannel()
        } else if (permit?.get("phase") == JsonPrimitive("permitted") &&
            nowMs() >= (permit?.get("permit_expires_boottime_ms") as? JsonPrimitive)?.longOrNull.orZero()
        ) {
            permit = revoked()
            revision++
        }
    }

    /** Invalid current-channel data consumes any previous permit. Foreign runs/channels cannot mutate this one. */
    @Synchronized fun receive(raw: String) {
        expire()
        val request = try { decodeObject(raw) } catch (_: IllegalArgumentException) {
            rejectInvalid()
            return
        }
        val run = active ?: return
        // Attribute a well-formed object before validating operation fields. A bad ACK
        // for an old run or another collector must not revoke the current run.
        try {
            require(request.string("package").isNotBlank() && name.matches(request.string("execution_id")))
            for (key in listOf("app_instance", "session_id", "run_nonce")) require(token.matches(request.string(key)))
        } catch (_: IllegalArgumentException) { rejectInvalid(); return }
        if (run.identity().any { (key, value) -> request[key] != value }) return
        val existing = channel
        try {
            for (key in listOf("collector_id", "source_challenge")) require(token.matches(request.string(key)))
        } catch (_: IllegalArgumentException) { rejectInvalid(); return }
        if (existing != null && listOf("collector_id", "source_challenge").any {
                request[it] != existing[it]
            }) return
        try { validateRequest(request) } catch (_: IllegalArgumentException) { rejectInvalid(); return }
        val now = nowMs()
        when (request.string("operation")) {
            "source_challenge" -> {
                val expiry = request.number("expires_boottime_ms")
                // A repeated challenge cannot extend its original 30 second authority.
                val challenge = request.string("source_challenge")
                if (existing != null || expiry <= now || expiry - now > 30_000 ||
                    challenge in usedChallenges || usedChallenges.size >= 4096) {
                    rejectInvalid()
                    return
                }
                usedChallenges += challenge
                invalidRequest = false
                channel = request
                channelExpiresMs = expiry
                permit = JsonObject(request + mapOf("phase" to JsonPrimitive("challenge"),
                    "observed_boottime_ms" to JsonPrimitive(now), "permit_expires_boottime_ms" to JsonPrimitive(expiry)))
                revision++
            }
            "persist_ack" -> {
                if (existing == null) { permit = revoked(request); revision++; return }
                val source = request.number("source_boottime_ms")
                val expiry = request.number("expires_boottime_ms")
                val nonce = request.string("source_nonce")
                if (source < startedMs || source > now || now - source >= 5_000 ||
                    expiry != source + 5_000 || expiry <= now || nonce in usedNonces || usedNonces.size >= 4096
                ) {
                    rejectInvalid()
                    return
                }
                usedNonces += nonce
                permit = JsonObject(request + mapOf("phase" to JsonPrimitive("permitted"),
                    "observed_boottime_ms" to JsonPrimitive(now),
                    "permit_expires_boottime_ms" to JsonPrimitive(minOf(expiry, now + 2_000, channelExpiresMs))))
                revision++
            }
            "revoke" -> if (existing != null) { permit = revoked(request); revokeChannel() }
        }
    }

    data class Publication(val revision: Long, val state: String, val permit: String)

    /** File publication belongs to the Coordinator's existing IO poll, outside Runner's command mutex. */
    @Synchronized fun publication(): Publication {
        expire()
        if (permit == null) permit = revoked()
        val run = active ?: last
        val state = buildJsonObject {
            put("version", 1); put("package", packageName)
            put("app_instance", run?.appInstance ?: currentHello()?.app_instance.orEmpty())
            put("session_id", run?.lease?.sessionId.orEmpty())
            put("execution_id", run?.executionId.orEmpty()); put("run_nonce", run?.runNonce.orEmpty())
            put("phase", if (active == null) "idle" else if (invalidRequest) "error" else "running")
            put("started_boottime_ms", startedMs); put("observed_boottime_ms", nowMs())
            put("audit_persist_supported", true)
        }
        return Publication(revision, state.toString(), permit.toString())
    }

    @Synchronized fun unchanged(publication: Publication): Boolean {
        expire()
        return revision == publication.revision
    }

    companion object {
        fun newNonce(): String = UUID.randomUUID().toString().replace("-", "")
        private val token = Regex("[a-f0-9]{32}")
        private val name = Regex("[A-Za-z0-9_.:-]{1,128}")
        private val hash = Regex("[a-f0-9]{64}")
        private val baseKeys = setOf("version", "package", "app_instance", "session_id", "execution_id",
            "run_nonce", "collector_id", "source_challenge", "request_id", "operation")
        private const val MAX_INTEGER = 9_007_199_254_740_991L

        private fun Long?.orZero() = this ?: 0L
        private fun JsonObject.string(key: String): String {
            val value = get(key) as? JsonPrimitive ?: throw IllegalArgumentException("invalid_audit_string")
            require(value.isString)
            return value.content
        }
        private fun JsonObject.number(key: String): Long {
            val value = get(key) as? JsonPrimitive ?: throw IllegalArgumentException("invalid_audit_integer")
            require(!value.isString && Regex("0|[1-9][0-9]*").matches(value.content))
            return requireNotNull(value.longOrNull).also { require(it in 0..MAX_INTEGER) }
        }

        /** All wire requests are flat primitive objects. Parse each key before Json can discard duplicates. */
        internal fun decodeRequest(raw: String): JsonObject {
            return decodeObject(raw).also { validateRequest(it) }
        }

        private fun decodeObject(raw: String): JsonObject {
            require(raw.toByteArray(Charsets.UTF_8).size <= DEV_SESSION_MAX_BYTES)
            var offset = 0
            fun spaces() { while (offset < raw.length && raw[offset] in " \t\r\n") offset++ }
            fun quoted(): String {
                require(offset < raw.length && raw[offset] == '"')
                val start = offset++
                while (offset < raw.length) {
                    when (raw[offset++]) {
                        '\\' -> { require(offset < raw.length); offset++ }
                        '"' -> return raw.substring(start, offset)
                    }
                }
                throw IllegalArgumentException("unterminated_audit_string")
            }
            spaces(); require(offset < raw.length && raw[offset++] == '{'); spaces()
            val keys = mutableSetOf<String>()
            while (offset < raw.length && raw[offset] != '}') {
                val key = Json.decodeFromString<String>(quoted())
                require(keys.add(key)) { "duplicate_audit_key" }
                spaces(); require(offset < raw.length && raw[offset++] == ':'); spaces()
                var depth = 0
                while (offset < raw.length) {
                    val character = raw[offset]
                    if (depth == 0 && character in ",}") break
                    when (character) {
                        '"' -> { quoted(); continue }
                        '{', '[' -> depth++
                        '}', ']' -> depth--
                    }
                    require(depth >= 0)
                    offset++
                }
                spaces()
                if (offset < raw.length && raw[offset] == ',') { offset++; spaces(); require(offset < raw.length && raw[offset] != '}') }
                else break
            }
            require(offset < raw.length && raw[offset++] == '}'); spaces(); require(offset == raw.length)
            val value = Json.parseToJsonElement(raw) as? JsonObject ?: throw IllegalArgumentException("invalid_audit_object")
            return value
        }

        private fun validateRequest(value: JsonObject) {
            require(value.values.all { it is JsonPrimitive })
            val operation = value.string("operation")
            val extra = when (operation) {
                "source_challenge" -> setOf("expires_boottime_ms")
                "persist_ack" -> setOf("stream_id", "sequence", "source_nonce", "intent_sha256", "source_boottime_ms", "expires_boottime_ms")
                "revoke" -> emptySet()
                else -> throw IllegalArgumentException("unknown_audit_operation")
            }
            require(value.keys == baseKeys + extra)
            require(value.number("version") == 1L)
            require(value.string("package").isNotBlank() && name.matches(value.string("execution_id")))
            for (key in listOf("app_instance", "session_id", "run_nonce", "collector_id", "source_challenge", "request_id")) {
                require(token.matches(value.string(key)))
            }
            if (operation != "revoke") value.number("expires_boottime_ms")
            if (operation == "persist_ack") {
                require(token.matches(value.string("stream_id")) && token.matches(value.string("source_nonce")))
                require(hash.matches(value.string("intent_sha256")))
                require(value.number("sequence") > 0)
                value.number("source_boottime_ms")
            }
        }
    }
}
