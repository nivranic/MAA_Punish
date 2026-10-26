package com.aliothmoon.maafw.devsession

import java.io.File
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class DevAuditEvidenceTest {
    @get:Rule val folder = TemporaryFolder()
    private var now = 1_000L
    private var lease: DevSessionLease? = DevSessionLease("a".repeat(32), "b".repeat(32), true)
    private val hello = DevSessionHello(1, "pkg", "c".repeat(32), "d".repeat(32), 16_384)
    private fun authority() = DevAuditEvidence("pkg", File(folder.root, ".dev-session"), { now }, { hello }, { lease })
    private fun run(e: DevAuditEvidence, nonce: String = "e") = checkNotNull(e.binding(checkNotNull(lease), "exec", nonce.repeat(32)))
    private fun request(run: DevAuditRunBinding, op: String, vararg fields: Pair<String, JsonElement>): String = buildJsonObject {
        put("version", 1)
        run.identity().forEach { (key, value) -> put(key, value) }
        put("collector_id", "f".repeat(32)); put("source_challenge", "1".repeat(32))
        put("request_id", DevAuditEvidence.newNonce()); put("operation", op)
        if (op == "source_challenge") put("expires_boottime_ms", now + 30_000)
        if (op == "persist_ack") {
            put("stream_id", "2".repeat(32)); put("sequence", 40)
            put("source_nonce", "3".repeat(32)); put("intent_sha256", "4".repeat(64))
            put("source_boottime_ms", now); put("expires_boottime_ms", now + 5_000)
        }
        fields.forEach { (key, value) -> put(key, value) }
    }.toString()
    private fun permit(e: DevAuditEvidence) = Json.parseToJsonElement(e.publication().permit).jsonObject
    private fun state(e: DevAuditEvidence) = Json.parseToJsonElement(e.publication().state).jsonObject
    private fun challenge(e: DevAuditEvidence, run: DevAuditRunBinding) = e.receive(request(run, "source_challenge"))

    @Test fun `prepare creates identity without authorizing and idle disk requests cannot grant`() {
        val e = authority(); val run = run(e)
        challenge(e, run)
        assertEquals("idle", state(e)["phase"]!!.jsonPrimitive.content)
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        assertTrue(e.register(run)); challenge(e, run)
        assertEquals("challenge", permit(e)["phase"]!!.jsonPrimitive.content)
    }

    @Test fun `ACK requires current identity and permit is bounded to two seconds`() {
        val e = authority(); val run = run(e); e.register(run); challenge(e, run)
        e.receive(request(run, "persist_ack"))
        assertEquals(3_000, permit(e)["permit_expires_boottime_ms"]!!.jsonPrimitive.long)
        now = 3_000
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        e.receive(request(run, "persist_ack"))
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
    }

    @Test fun `first run replaces published idle permit with its own empty revoke identity`() {
        val e = authority()
        e.publication()
        val run = run(e)
        assertTrue(e.register(run))
        val waiting = permit(e)
        run.identity().forEach { (key, value) -> assertEquals(value, waiting[key]) }
        assertEquals("revoked", waiting["phase"]!!.jsonPrimitive.content)
        assertEquals("revoke", waiting["operation"]!!.jsonPrimitive.content)
        assertEquals(0L, waiting["permit_expires_boottime_ms"]!!.jsonPrimitive.long)
    }

    @Test fun `next run replaces prior ACK identity and fields before its first challenge`() {
        val e = authority(); val old = run(e); e.register(old); challenge(e, old)
        e.receive(request(old, "persist_ack")); e.finish(old)
        val current = run(e, "5")
        assertTrue(e.register(current))
        val waiting = permit(e)
        current.identity().forEach { (key, value) -> assertEquals(value, waiting[key]) }
        assertEquals("revoked", waiting["phase"]!!.jsonPrimitive.content)
        assertEquals("revoke", waiting["operation"]!!.jsonPrimitive.content)
        assertEquals("0".repeat(32), waiting["collector_id"]!!.jsonPrimitive.content)
        assertEquals("0".repeat(32), waiting["source_challenge"]!!.jsonPrimitive.content)
        for (key in listOf("stream_id", "sequence", "source_nonce", "intent_sha256", "source_boottime_ms", "expires_boottime_ms")) {
            assertFalse(waiting.containsKey(key))
        }
    }

    @Test fun `challenge cannot exceed thirty seconds or replay after revoke`() {
        val e = authority(); val run = run(e); e.register(run)
        e.receive(request(run, "source_challenge", "expires_boottime_ms" to JsonPrimitive(now + 30_001)))
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        challenge(e, run)
        e.receive(request(run, "revoke"))
        challenge(e, run)
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
    }

    @Test fun `source expiry future timestamps and stale timestamps fail closed`() {
        for ((source, expiry) in listOf(1_001L to 6_001L, 1_000L to 6_001L, 0L to 5_000L)) {
            val e = authority(); val run = run(e); e.register(run); challenge(e, run)
            e.receive(request(run, "persist_ack", "source_boottime_ms" to JsonPrimitive(source),
                "expires_boottime_ms" to JsonPrimitive(expiry)))
            assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        }
        val e = authority(); val run = run(e); e.register(run); challenge(e, run)
        val ack = request(run, "persist_ack"); now = 6_000; e.receive(ack)
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
    }

    @Test fun `old callbacks old run ACK and foreign collector cannot clear newer permit`() {
        val e = authority(); val old = run(e); e.register(old); challenge(e, old); e.finish(old)
        val current = run(e, "5"); e.register(current); challenge(e, current)
        e.receive(request(current, "persist_ack"))
        val before = permit(e)
        e.finish(old)
        e.receive(request(old, "revoke"))
        e.receive(request(old, "persist_ack", "sequence" to JsonPrimitive(false)))
        e.receive(request(old, "persist_ack", "extra" to buildJsonObject { put("nested", true) }))
        e.receive(request(current, "persist_ack", "collector_id" to JsonPrimitive("6".repeat(32)),
            "sequence" to JsonPrimitive(false)))
        assertEquals(before, permit(e))
        e.receive(request(current, "revoke"))
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
    }

    @Test fun `malformed current ACK and unassignable JSON revoke prior permit and leave error state`() {
        for (invalid in listOf("{", "{\"version\":1,\"version\":1}", "{}")) {
            val e = authority(); val run = run(e); e.register(run); challenge(e, run)
            e.receive(request(run, "persist_ack")); e.receive(invalid)
            assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
            assertEquals("error", state(e)["phase"]!!.jsonPrimitive.content)
        }
        val e = authority(); val run = run(e); e.register(run); challenge(e, run)
        e.receive(request(run, "persist_ack"))
        e.receive(request(run, "persist_ack", "sequence" to JsonPrimitive(true)))
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        val other = authority(); val otherRun = run(other); other.register(otherRun); challenge(other, otherRun)
        other.receive(request(otherRun, "persist_ack"))
        other.receive(request(otherRun, "persist_ack", "run_nonce" to JsonNull))
        assertEquals("revoked", permit(other)["phase"]!!.jsonPrimitive.content)
        assertEquals("error", state(other)["phase"]!!.jsonPrimitive.content)
    }

    @Test fun `strict wire rejects extra keys numeric strings floats bool and escaped duplicate keys`() {
        val e = authority(); val run = run(e)
        val valid = request(run, "persist_ack")
        for (raw in listOf(
            request(run, "persist_ack", "extra" to JsonPrimitive(1)),
            request(run, "persist_ack", "sequence" to JsonPrimitive("1")),
            request(run, "persist_ack", "sequence" to JsonPrimitive(1.0)),
            request(run, "persist_ack", "sequence" to JsonPrimitive(false)),
            valid.dropLast(1) + ",\"vers\\u0069on\":1}",
        )) assertTrue(runCatching { DevAuditEvidence.decodeRequest(raw) }.isFailure)
    }

    @Test fun `lease loss and guard revoke invalidate authority and prevent redispatch`() {
        val e = authority(); val run = run(e)
        val guard = DevAuditRunGuard(checkNotNull(lease), "exec", run, e)
        assertTrue(guard.register()); challenge(e, run); e.receive(request(run, "persist_ack"))
        lease = null
        assertEquals("idle", state(e)["phase"]!!.jsonPrimitive.content)
        assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        guard.revoke(); assertFalse(guard.register())
    }

    @Test fun `concurrent dispatch registration and cancellation never leave authority after revoke`() {
        repeat(30) {
            val e = authority(); val run = run(e)
            val guard = DevAuditRunGuard(checkNotNull(lease), "exec", run, e)
            val gate = java.util.concurrent.CountDownLatch(1)
            val dispatch = Thread { gate.await(); guard.register() }
            val cancel = Thread { gate.await(); guard.revoke() }
            dispatch.start(); cancel.start(); gate.countDown()
            dispatch.join(5_000); cancel.join(5_000)
            assertFalse(dispatch.isAlive || cancel.isAlive)
            assertTrue(guard.revoked)
            assertEquals("idle", state(e)["phase"]!!.jsonPrimitive.content)
            assertEquals("revoked", permit(e)["phase"]!!.jsonPrimitive.content)
        }
    }
}
