package com.aliothmoon.maafw.devsession

import kotlinx.serialization.SerializationException
import org.junit.Assert.*
import org.junit.Test

class DevSessionProtocolTest {
    private var clock = 0L
    private var tokenIndex = 10
    private val protocol = DevSessionProtocol("com.example.maa", { clock }) {
        (tokenIndex++).toString(16).padStart(32, '0')
    }

    private fun request(operation: String = "open", sequence: Long = 1) = protocol.hello().let {
        DevSessionRequest(1, it.`package`, it.app_instance, it.challenge,
            "1".repeat(32), "2".repeat(32), sequence.toString(16).padStart(32, '0'), sequence, operation)
    }

    @Test
    fun `audit capability is advertised but leases are off by default`() {
        assertTrue(protocol.hello().audit_logging_supported)
        assertFalse(protocol.status().audit_logging)
        protocol.receive(request())
        assertFalse(protocol.lease!!.auditLogging)
        assertFalse(protocol.status().audit_logging)
    }

    @Test
    fun `explicit audit lease is acknowledged immutable and gone after close or expiry`() {
        val open = request().copy(audit_logging = true)
        assertTrue(protocol.receive(open).audit_logging)
        assertTrue(protocol.lease!!.auditLogging)
        assertEquals("audit_mode_mismatch", protocol.receive(request("heartbeat", 2)).reason)
        assertTrue(protocol.receive(request("heartbeat", 2).copy(audit_logging = true)).audit_logging)
        val closed = protocol.receive(request("close", 0).copy(audit_logging = true))
        assertFalse(closed.audit_logging)
        assertNull(protocol.lease)
        protocol.receive(request().copy(audit_logging = true))
        clock = 8_000
        protocol.tick()
        assertFalse(protocol.status().audit_logging)
        protocol.receive(request())
        assertFalse(protocol.status().audit_logging)
    }

    @Test
    fun `a process starts without a lease and old disk messages cannot reopen it`() {
        assertNull(protocol.lease)
        val old = request()
        val fresh = DevSessionProtocol("com.example.maa", { clock }) { "f".repeat(32) }
        assertEquals("stale_challenge", fresh.receive(old).reason)
        assertNull(fresh.lease)
    }

    @Test
    fun `valid open and increasing heartbeat renew by monotonic phone time`() {
        assertEquals("accepted", protocol.receive(request()).result)
        clock = 7_900
        assertEquals("accepted", protocol.receive(request("heartbeat", 2)).result)
        clock = 15_899
        assertFalse(protocol.tick())
        assertNotNull(protocol.lease)
        clock = 15_900
        assertTrue(protocol.tick())
        assertNull(protocol.lease)
        assertEquals("expired", protocol.status().phase)
    }

    @Test
    fun `duplicate acknowledgement cannot renew and old challenge cannot restore expiry`() {
        val open = request()
        protocol.receive(open)
        clock = 7_000
        assertEquals("accepted", protocol.receive(open).result)
        clock = 8_000
        assertTrue(protocol.tick())
        assertEquals("stale_challenge", protocol.receive(open).reason)
        assertNull(protocol.lease)
    }

    @Test
    fun `second owner and sequence replay do not steal or extend a lease`() {
        val open = request()
        protocol.receive(open)
        clock = 7_000
        assertEquals("session_busy", protocol.receive(request("heartbeat", 2).copy(session_id = "f".repeat(32))).reason)
        assertEquals("stale_sequence", protocol.receive(request("heartbeat", 1).copy(request_id = "e".repeat(32))).reason)
        clock = 8_000
        assertTrue(protocol.tick())
    }

    @Test
    fun `close uses independent zero sequence and retires challenge`() {
        protocol.receive(request())
        protocol.receive(request("heartbeat", 50))
        val before = protocol.hello().challenge
        val result = protocol.receive(request("close", 0).copy(request_id = "a".repeat(32)))
        assertEquals("accepted", result.result)
        assertEquals("closed", result.phase)
        assertNull(protocol.lease)
        assertNotEquals(before, protocol.hello().challenge)
        // Another explicit session starts at one under the new challenge.
        assertEquals("accepted", protocol.receive(request()).result)
    }

    @Test
    fun `unknown operation wrong package invalid identity and heartbeat before open fail`() {
        for (invalid in listOf(
            request("shell"), request().copy(`package` = "com.other.app"),
            request().copy(version = 2), request().copy(host_id = "bad"),
            request(sequence = -1), request("heartbeat"), request("close", 0),
        )) {
            assertEquals("rejected", protocol.receive(invalid).result)
            assertNull(protocol.lease)
        }
    }

    @Test
    fun `malformed JSON missing fields and unknown fields are rejected strictly`() {
        for (json in listOf("{}", "[]", "not JSON", """{"version":1,"shell":"reboot"}""")) {
            try {
                devSessionJson.decodeFromString<DevSessionRequest>(json)
                fail("Malformed request accepted")
            } catch (_: SerializationException) { }
        }
        protocol.receive(request())
        clock = 7_000
        protocol.rejectInvalid()
        clock = 8_000
        assertTrue(protocol.tick())
    }

    @Test
    fun `process lifecycle starts polling only after user foreground and drains existing lease`() {
        val lifetime = DevSessionLifetime()
        assertFalse(lifetime.canAcceptOpen)
        assertFalse(lifetime.shouldPoll(null))
        lifetime.enterForeground()
        assertTrue(lifetime.canAcceptOpen)
        assertTrue(lifetime.shouldPoll(null))
        protocol.receive(request())
        lifetime.enterBackground()
        assertFalse(lifetime.canAcceptOpen)
        assertTrue(lifetime.shouldPoll(protocol.lease))
        clock = 8_000
        protocol.tick()
        assertFalse(lifetime.shouldPoll(protocol.lease))
        lifetime.enterForeground()
        assertTrue(lifetime.shouldPoll(null))
    }
}
