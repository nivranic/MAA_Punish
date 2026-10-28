package com.aliothmoon.maafw.devsession

import java.io.File
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class DevAuditCoordinatorTest {
    @get:Rule val folder = TemporaryFolder()

    @Test fun `real coordinator poll opens lease publishes current run and revokes on close`() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(dispatcher)
        try {
            val coordinator = DevSessionCoordinator(backgroundScope, folder.root, "pkg", { testScheduler.currentTime + 1_000 }, dispatcher)
            coordinator.onResume(mockk())
            runCurrent()
            val directory = File(folder.root, ".dev-session")
            val hello = checkNotNull(coordinator.binding.value)
            val open = DevSessionRequest(1, "pkg", hello.app_instance, hello.challenge, "a".repeat(32),
                "b".repeat(32), "c".repeat(32), 1, "open", true)
            File(directory, "request.json").writeText(devSessionJson.encodeToString(open))
            advanceTimeBy(250); runCurrent()
            val lease = checkNotNull(coordinator.lease.value)
            val evidence = coordinator.auditEvidence
            val run = checkNotNull(evidence.binding(lease, "actual-run", "d".repeat(32)))
            assertTrue(evidence.register(run))
            val published = checkNotNull(coordinator.auditPublisher.publishForDispatch(run))
            assertTrue(evidence.isPublishedCurrent(run, published))
            val immediate = Json.parseToJsonElement(File(directory, "audit-state.json").readText()).jsonObject
            assertEquals("running", immediate.getValue("phase").jsonPrimitive.content)
            run.identity().forEach { (key, value) -> assertEquals(value, immediate[key]) }
            val challenge = buildJsonObject {
                put("version", 1); run.identity().forEach { (key, value) -> put(key, value) }
                put("collector_id", "e".repeat(32)); put("source_challenge", "f".repeat(32))
                put("request_id", "1".repeat(32)); put("operation", "source_challenge")
                put("expires_boottime_ms", 30_000)
            }
            File(directory, "audit-request.json").writeText(challenge.toString())
            advanceTimeBy(250); runCurrent()
            fun read(name: String) = Json.parseToJsonElement(File(directory, name).readText()).jsonObject
            assertEquals("running", read("audit-state.json")["phase"]!!.jsonPrimitive.content)
            assertEquals("challenge", read("audit-permit.json")["phase"]!!.jsonPrimitive.content)
            // A malformed audit request invalidates purchase authority, not the shared USB lease.
            File(directory, "audit-request.json").writeText("{")
            advanceTimeBy(250); runCurrent()
            assertEquals(lease, coordinator.lease.value)
            assertEquals("error", read("audit-state.json")["phase"]!!.jsonPrimitive.content)
            assertEquals("revoked", read("audit-permit.json")["phase"]!!.jsonPrimitive.content)
            coordinator.closeCurrent(lease.sessionId, "test_close")
            advanceTimeBy(250); runCurrent()
            assertNull(coordinator.lease.value)
            assertEquals("idle", read("audit-state.json")["phase"]!!.jsonPrimitive.content)
            coordinator.onStop(mockk())
            advanceTimeBy(250); runCurrent()
        } finally { Dispatchers.resetMain() }
    }
}
