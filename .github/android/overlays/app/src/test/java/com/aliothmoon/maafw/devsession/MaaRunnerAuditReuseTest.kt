package com.aliothmoon.maafw.devsession

import com.aliothmoon.maafw.maa.MaaAgentClientLibrary
import com.aliothmoon.maafw.maa.MaaAgentClientLoader
import com.aliothmoon.maafw.maa.MaaFrameworkLibrary
import com.aliothmoon.maafw.remote.AgentExit
import com.aliothmoon.maafw.remote.AgentHost
import com.aliothmoon.maafw.remote.AgentLaunchException
import com.aliothmoon.maafw.remote.AgentLaunchRequest
import com.aliothmoon.maafw.remote.AgentSession
import com.aliothmoon.maafw.remote.MaaRunner
import com.aliothmoon.maafw.runner.AgentPayload
import com.aliothmoon.maafw.runner.RunPlanPayload
import com.aliothmoon.maafw.third.Ln
import com.sun.jna.Pointer
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.mockkStatic
import io.mockk.Runs
import io.mockk.unmockkAll
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.ExecutorService

/** Executes the generated native Runner's actual lifecycle, with native APIs and child spawning replaced. */
class MaaRunnerAuditReuseTest {
    private val lib = mockk<MaaFrameworkLibrary>(relaxed = true)
    private val agentLib = mockk<MaaAgentClientLibrary>(relaxed = true)
    private val host = RecordingHost()
    private lateinit var runner: MaaRunner
    private val agent = AgentPayload("python", listOf("agent/main.py"))

    @Before fun setUp() {
        mockkObject(MaaAgentClientLoader)
        every { MaaAgentClientLoader.library } returns agentLib
        mockkStatic(Ln::class)
        every { Ln.i(any()) } just Runs
        every { Ln.w(any<String>()) } just Runs
        every { agentLib.MaaAgentClientCreateTcp(any()) } returns Pointer(11)
        every { agentLib.MaaAgentClientBindResource(any(), any()) } returns 1.toByte()
        every { agentLib.MaaAgentClientIdentifier(any(), any()) } returns 1.toByte()
        every { agentLib.MaaAgentClientConnect(any()) } returns 1.toByte()
        every { agentLib.MaaAgentClientAlive(any()) } returns 1.toByte()
        every { lib.MaaStringBufferCreate() } returns Pointer(12)
        every { lib.MaaStringBufferGet(any()) } returns "identifier"
        runner = MaaRunner(host)
        runner.setProjectRoot("pi")
    }

    @After fun tearDown() {
        if (::runner.isInitialized) {
            invoke("releaseAgents")
            (field("worker") as ExecutorService).shutdownNow()
        }
        unmockkAll()
    }

    private fun payload(environment: Map<String, String> = emptyMap(), count: Int = 1) = RunPlanPayload(
        resourcePaths = emptyList(), screenWidth = 1920, screenHeight = 1080,
        tasks = emptyList(), agents = List(count) { agent }, piEnv = environment,
    )

    private fun audit(session: String = "a") = DevAuditEnvironment.forLease(
        emptyMap(), DevSessionLease(session.repeat(32), "b".repeat(32), auditLogging = true),
    )

    private fun prepare(payload: RunPlanPayload): Any? = MaaRunner::class.java.getDeclaredMethod(
        "prepareAgents", MaaFrameworkLibrary::class.java, RunPlanPayload::class.java,
    ).apply { isAccessible = true }.invoke(runner, lib, payload)

    private fun invoke(name: String) = MaaRunner::class.java.getDeclaredMethod(name)
        .apply { isAccessible = true }.invoke(runner)

    private fun field(name: String): Any? = MaaRunner::class.java.getDeclaredField(name)
        .apply { isAccessible = true }.get(runner)

    @Test fun `normal to audit rebuilds the child with its audit startup settings`() {
        assertNull(prepare(payload()))
        assertNull(prepare(payload(audit())))
        assertEquals(2, host.sessions.size)
        assertTrue(host.sessions.first().closed)
        assertEquals(audit(), host.sessions.last().auditEnvironment)
    }

    @Test fun `audit to normal rebuilds and removes inherited audit settings`() {
        assertNull(prepare(payload(audit())))
        assertNull(prepare(payload()))
        assertEquals(2, host.sessions.size)
        assertTrue(host.sessions.first().closed)
        assertEquals(emptyMap<String, String>(), host.sessions.last().auditEnvironment)
    }

    @Test fun `a different audit session rebuilds even with identical agent definitions`() {
        assertNull(prepare(payload(audit("a"))))
        assertNull(prepare(payload(audit("c"))))
        assertEquals(2, host.sessions.size)
        assertTrue(host.sessions.first().closed)
        assertEquals("c".repeat(32), host.sessions.last().auditEnvironment[DevAuditEnvironment.SESSION_ID])
    }

    @Test fun `the same audit session reuses across unrelated environment changes`() {
        assertNull(prepare(payload(audit() + ("PI_CLIENT_NAME" to "before"))))
        assertNull(prepare(payload(audit() + ("PI_CLIENT_NAME" to "after"))))
        assertEquals(1, host.sessions.size)
        assertFalse(host.sessions.single().closed)
    }

    @Test fun `ordinary runs reuse the empty audit projection`() {
        assertNull(prepare(payload(mapOf("PI_CLIENT_NAME" to "before"))))
        assertNull(prepare(payload(mapOf("PI_CLIENT_NAME" to "after"))))
        assertEquals(1, host.sessions.size)
        assertEquals(emptyMap<String, String>(), host.sessions.single().auditEnvironment)
    }

    @Test fun `failed replacement spawn cannot retain the previous session key`() {
        assertNull(prepare(payload(audit("a"))))
        host.failIndex = 0
        assertNotNull(prepare(payload(audit("c"))))
        assertTrue(host.sessions.single().closed)
        assertNull(field("loadedAuditEnvironment"))
        host.failIndex = null
        assertNull(prepare(payload(audit("a"))))
        assertEquals(2, host.sessions.size)
        assertEquals(audit("a"), host.sessions.last().auditEnvironment)
    }

    @Test fun `partial startup failure closes started children and leaves no reuse key`() {
        host.failIndex = 1
        assertNotNull(prepare(payload(audit(), count = 2)))
        assertTrue(host.sessions.single().closed)
        assertNull(field("loadedAuditEnvironment"))
        host.failIndex = null
        assertNull(prepare(payload(audit(), count = 2)))
        assertEquals(3, host.sessions.size)
    }

    @Test fun `release and unexpected exit invalidate the environment key`() {
        assertNull(prepare(payload(audit())))
        runner.onAgentExited(AgentExit(0, "python", 1))
        assertNull(field("loadedAuditEnvironment"))
        assertNull(prepare(payload(audit())))
        assertEquals(2, host.sessions.size)
        assertNull(prepare(payload(count = 0)))
        assertNull(field("loadedAuditEnvironment"))
        assertTrue(host.sessions.all { it.closed })
    }

    @Test fun `invalid reserved settings cannot bypass spawn validation through reuse`() {
        assertNull(prepare(payload(audit())))
        assertNotNull(prepare(payload(audit() + (DevAuditEnvironment.PREFIX + "UNKNOWN" to "1"))))
        assertNull(field("loadedAuditEnvironment"))
        assertEquals(1, host.sessions.size)
    }

    private class RecordingHost : AgentHost {
        val sessions = mutableListOf<RecordingSession>()
        var failIndex: Int? = null

        override fun launch(request: AgentLaunchRequest): AgentSession {
            if (request.index == failIndex) throw AgentLaunchException("spawn failed")
            val environment = mutableMapOf(DevAuditEnvironment.LOG_MODE to "file",
                DevAuditEnvironment.SESSION_ID to "stale", DevAuditEnvironment.PREFIX + "UNKNOWN" to "stale")
            DevAuditEnvironment.installInProcess(environment, request.piEnv)
            return RecordingSession(environment).also { sessions += it }
        }
    }

    private class RecordingSession(val auditEnvironment: Map<String, String>) : AgentSession {
        override val executable = "python"
        var closed = false
        override fun isAlive() = !closed
        override fun expectExit() {}
        override fun close() { closed = true }
    }
}
