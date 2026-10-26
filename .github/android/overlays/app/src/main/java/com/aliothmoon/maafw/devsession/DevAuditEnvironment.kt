package com.aliothmoon.maafw.devsession

/** Reserved process-start settings. Only a current app lease may create them. */
object DevAuditEnvironment {
    const val PREFIX = "MAA_PUNISH_AUDIT_"
    const val LOG_MODE = PREFIX + "LOG_MODE"
    const val SESSION_ID = PREFIX + "SESSION_ID"
    private val token = Regex("[a-f0-9]{32}")

    fun forLease(base: Map<String, String>, lease: DevSessionLease?): Map<String, String> {
        val clean = base.filterKeys { !it.startsWith(PREFIX) }
        if (lease?.auditLogging != true) return clean
        require(token.matches(lease.sessionId)) { "invalid_audit_session_id" }
        return clean + mapOf(LOG_MODE to "stdout", SESSION_ID to lease.sessionId)
    }

    /** After inherited/runtime-descriptor env, before ProcessBuilder.start(). */
    fun installInProcess(environment: MutableMap<String, String>, payload: Map<String, String>) {
        val audit = payload.filterKeys { it.startsWith(PREFIX) }
        require(audit.isEmpty() || (audit.keys == setOf(LOG_MODE, SESSION_ID) &&
            audit[LOG_MODE] == "stdout" && token.matches(audit[SESSION_ID].orEmpty()))) {
            "invalid_audit_process_environment"
        }
        environment.keys.removeAll { it.startsWith(PREFIX) }
        environment.putAll(audit)
    }
}
