package com.aliothmoon.maafw.devsession

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class DevSessionMailboxTest {
    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun `publish replaces complete contents and never leaves temporary files`() {
        val mailbox = DevSessionMailbox(folder.root)
        mailbox.publish("hello.json", "{\"first\":true}")
        mailbox.publish("hello.json", "{\"second\":true}")
        val directory = File(folder.root, ".dev-session")
        assertEquals("{\"second\":true}", File(directory, "hello.json").readText())
        assertEquals(listOf("hello.json"), directory.list()!!.toList())
    }

    @Test
    fun `mailbox cannot publish user paths or request files`() {
        val mailbox = DevSessionMailbox(folder.root)
        for (name in listOf("../pi/config.json", "/tmp/anything", "request.json", "close.json")) {
            assertTrue(runCatching { mailbox.publish(name, "{}") }.isFailure)
        }
        assertFalse(File(folder.root, "pi").exists())
    }

    @Test
    fun `request reads are bounded and close uses independent fixed file`() {
        val mailbox = DevSessionMailbox(folder.root)
        mailbox.prepare()
        assertNull(mailbox.readRequest())
        val directory = File(folder.root, ".dev-session")
        File(directory, "request.json").writeText("heartbeat")
        File(directory, "close.json").writeText("close")
        assertEquals("heartbeat", mailbox.readRequest())
        assertEquals("close", mailbox.readRequest("close.json"))
        assertTrue(runCatching { mailbox.readRequest("../pi/config.json") }.isFailure)
        File(directory, "request.json").writeText("x".repeat(DEV_SESSION_MAX_BYTES + 1))
        assertTrue(runCatching { mailbox.readRequest() }.isFailure)
    }

    @Test
    fun `audit mailbox only reads independent request and publishes fixed responses`() {
        val mailbox = DevSessionMailbox(folder.root)
        mailbox.prepare()
        File(folder.root, ".dev-session/audit-request.json").writeText("{}")
        assertEquals("{}", mailbox.readRequest("audit-request.json"))
        for (name in listOf("audit-state.json", "audit-permit.json")) {
            mailbox.publish(name, "{}")
            assertTrue(runCatching { mailbox.readRequest(name) }.isFailure)
        }
        assertTrue(runCatching { mailbox.publish("audit-request.json", "{}") }.isFailure)
        assertTrue(runCatching { mailbox.publish("audit-other.json", "{}") }.isFailure)
    }
}
