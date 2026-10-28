package com.aliothmoon.maafw.devsession

import java.io.File
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class DevAuditPublisherTest {
    @get:Rule val directory = TemporaryFolder()

    private fun authority() = DevAuditEvidence("pkg", File(directory.root, ".dev-session"), { 1_000L },
        { DevSessionHello(1, "pkg", "d".repeat(32), "e".repeat(32), 16_384) },
        { DevSessionLease("a".repeat(32), "b".repeat(32), true) })

    private fun run(evidence: DevAuditEvidence) = checkNotNull(evidence.binding(
        DevSessionLease("a".repeat(32), "b".repeat(32), true), "actual", "c".repeat(32)))

    private fun phase() = Json.parseToJsonElement(File(directory.root, ".dev-session/audit-state.json").readText())
        .jsonObject.getValue("phase").jsonPrimitive.content

    @Test fun `old poll cannot write its idle snapshot after a dispatch publication ACK`() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val evidence = authority()
        val mailbox = DevSessionMailbox(directory.root)
        val held = CompletableDeferred<Unit>()
        var first = true
        val written = mutableListOf<String>()
        val publisher = DevAuditPublisher(evidence, dispatcher) { name, contents ->
            if (name == "audit-state.json" && first) { first = false; held.await() }
            mailbox.publish(name, contents)
            if (name == "audit-state.json") written += phase()
        }
        val oldPoll = async { publisher.publish() }
        runCurrent()
        val actual = run(evidence)
        assertTrue(evidence.register(actual))
        val dispatch = async { publisher.publishForDispatch(actual) }
        runCurrent()
        assertFalse(dispatch.isCompleted)
        held.complete(Unit)
        runCurrent()
        oldPoll.await()
        assertNotNull(dispatch.await())
        assertEquals(listOf("idle", "running", "running"), written)
        assertEquals("running", phase())
    }

    @Test fun `finish during a write replaces the snapshot and returns no dispatch ACK`() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val evidence = authority()
        val actual = run(evidence)
        assertTrue(evidence.register(actual))
        val mailbox = DevSessionMailbox(directory.root)
        val held = CompletableDeferred<Unit>()
        var first = true
        val publisher = DevAuditPublisher(evidence, dispatcher) { name, contents ->
            if (name == "audit-state.json" && first) { first = false; held.await() }
            mailbox.publish(name, contents)
        }
        val dispatch = async { publisher.publishForDispatch(actual) }
        runCurrent()
        evidence.finish(actual)
        held.complete(Unit)
        runCurrent()
        assertNull(dispatch.await())
        assertEquals("idle", phase())
    }
}
