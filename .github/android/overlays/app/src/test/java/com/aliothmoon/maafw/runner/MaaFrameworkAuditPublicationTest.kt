package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.ITextInputSink
import com.aliothmoon.maafw.MaaDispatchers
import com.aliothmoon.maafw.constant.AppPaths
import com.aliothmoon.maafw.devsession.*
import com.aliothmoon.maafw.domain.ControllerDefinition
import com.aliothmoon.maafw.domain.ResourceDefinition
import com.aliothmoon.maafw.domain.RunConfigurationId
import com.aliothmoon.maafw.domain.RunMode
import com.aliothmoon.maafw.privileged.FakePrivilegedService
import com.aliothmoon.maafw.privileged.FakePrivilegedServicePort
import com.aliothmoon.maafw.project.PiInstaller
import io.mockk.*
import java.io.File
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class MaaFrameworkAuditPublicationTest {
    @get:Rule val temporary = TemporaryFolder()
    private val dispatcher = StandardTestDispatcher()
    private lateinit var root: File

    @Before fun setUp() {
        mockkObject(MaaDispatchers, AppPaths)
        every { MaaDispatchers.IO } returns dispatcher
        every { MaaDispatchers.Default } returns dispatcher
        root = temporary.newFolder("external")
        every { AppPaths.ROOT } returns root
        every { AppPaths.LOG_DIR } returns File(root, "log").apply { mkdirs() }
    }

    @After fun tearDown() { unmockkObject(MaaDispatchers, AppPaths) }

    private data class Harness(
        val runner: MaaFrameworkRunnerPort,
        val evidence: DevAuditEvidence,
        val lease: MutableStateFlow<DevSessionLease?>,
        val starts: AtomicInteger,
        val callbacks: MutableList<MaaFrameworkRunnerPort.ExecutionCallback>,
        val finalGateHook: AtomicReference<((MaaFrameworkRunnerPort.ExecutionCallback) -> Unit)?>,
    )

    private fun plan() = RunPlan(
        projectName = "demo", projectVersion = "1", controller = ControllerDefinition(),
        resource = ResourceDefinition("official", listOf("base")), runConfigurationId = RunConfigurationId("cfg"),
        tasks = listOf(RuntimeTask("task", "Start", emptyList())),
    )

    private suspend fun TestScope.harness(
        audit: Boolean = true,
        initializeIdle: Boolean = true,
        missingPublisher: Boolean = false,
        beforeWrite: suspend (String) -> Unit = {},
    ): Harness {
        val lease = MutableStateFlow<DevSessionLease?>(DevSessionLease("a".repeat(32), "b".repeat(32), audit))
        val evidence = DevAuditEvidence("pkg", File(root, ".dev-session"), { testScheduler.currentTime + 1_000 },
            { DevSessionHello(1, "pkg", "d".repeat(32), "e".repeat(32), 16_384) }, { lease.value })
        val mailbox = DevSessionMailbox(root)
        if (initializeIdle) DevAuditPublisher(evidence, dispatcher) { name, contents -> mailbox.publish(name, contents) }.publish()
        val publisher = DevAuditPublisher(evidence, dispatcher) { name, contents ->
            beforeWrite(name)
            mailbox.publish(name, contents)
        }
        val starts = AtomicInteger()
        lateinit var runner: MaaFrameworkRunnerPort
        val service = object : FakePrivilegedService() {
            override fun startRun(runPlanJson: String?): Boolean {
                starts.incrementAndGet()
                // This models the first synchronous statement of a DirectHit Custom.
                runner.devLastAuditRun?.run?.let { run ->
                    val state = Json.parseToJsonElement(File(root, ".dev-session/audit-state.json").readText()).jsonObject
                    assertEquals(JsonPrimitive("running"), state["phase"])
                    assertEquals(JsonPrimitive(true), state["audit_persist_supported"])
                    run.identity().forEach { (key, value) -> assertEquals(value, state[key]) }
                }
                return super.startRun(runPlanJson)
            }
        }
        val installer = mockk<PiInstaller>()
        every { installer.installedDir() } returns File(root, "pi").apply { mkdirs() }
        runner = MaaFrameworkRunnerPort(
            installer = installer, apkPath = "/apk", nativeLibraryDir = "/lib",
            runMode = { RunMode.BACKGROUND }, resolutionPreset = { ResolutionPresets.builtIn.first() },
            debugMode = { false }, saveOnError = { false }, scope = backgroundScope,
            servicePort = FakePrivilegedServicePort(service), textInputSink = { mockk<ITextInputSink>() },
            devSessionLease = lease, devAuditEvidence = evidence,
            devAuditPublisher = publisher.takeUnless { missingPublisher },
        )
        val callbacks = mutableListOf<MaaFrameworkRunnerPort.ExecutionCallback>()
        val hook = AtomicReference<((MaaFrameworkRunnerPort.ExecutionCallback) -> Unit)?>(null)
        runner.bindRunnerCallback = { _, callback -> callbacks += callback; hook.get()?.invoke(callback) }
        return Harness(runner, evidence, lease, starts, callbacks, hook)
    }

    private fun challenge(run: DevAuditRunBinding) = buildJsonObject {
        put("version", 1); run.identity().forEach { (key, value) -> put(key, value) }
        put("collector_id", "1".repeat(32)); put("source_challenge", "2".repeat(32))
        put("request_id", "3".repeat(32)); put("operation", "source_challenge")
        put("expires_boottime_ms", 30_000)
    }.toString()

    private fun persistAck(run: DevAuditRunBinding) = buildJsonObject {
        Json.parseToJsonElement(challenge(run)).jsonObject.forEach { (key, value) -> put(key, value) }
        put("request_id", "5".repeat(32)); put("operation", "persist_ack")
        put("stream_id", "6".repeat(32)); put("sequence", 1)
        put("source_nonce", "7".repeat(32)); put("intent_sha256", "8".repeat(64))
        put("source_boottime_ms", 1_000); put("expires_boottime_ms", 6_000)
    }.toString()

    @Test fun `first native statement reads the real running file before any poll tick`() = runTest(dispatcher) {
        val h = harness()
        assertEquals(RunnerCommandResult.Accepted, h.runner.start(plan(), "fast"))
        assertEquals(0L, testScheduler.currentTime)
        assertEquals(1, h.starts.get())
        h.callbacks.single().onFinished(RunOutcome.COMPLETED, null)
    }

    @Test fun `delayed publisher holds native dispatch until the actual state file is written`() = runTest(dispatcher) {
        val held = CompletableDeferred<Unit>()
        val h = harness(beforeWrite = { if (it == "audit-state.json") held.await() })
        val started = async { h.runner.start(plan(), "held") }
        runCurrent()
        assertEquals(0, h.starts.get())
        assertFalse(started.isCompleted)
        held.complete(Unit)
        runCurrent()
        assertEquals(RunnerCommandResult.Accepted, started.await())
        assertEquals(1, h.starts.get())
        h.callbacks.single().onFinished(RunOutcome.COMPLETED, null)
    }

    @Test fun `Stop can acquire the command gate while publication is suspended`() = runTest(dispatcher) {
        val held = CompletableDeferred<Unit>()
        val h = harness(beforeWrite = { if (it == "audit-state.json") held.await() })
        val started = async { h.runner.start(plan(), "stop-before-publish") }
        runCurrent()
        assertEquals(RunnerCommandResult.Accepted, h.runner.stop())
        assertTrue(checkNotNull(h.runner.devLastAuditRun).revoked)
        held.complete(Unit)
        runCurrent()
        assertTrue(started.await() is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
    }

    @Test fun `publication cancellation cleans its registration and never dispatches`() = runTest(dispatcher) {
        val held = CompletableDeferred<Unit>()
        val h = harness(beforeWrite = { if (it == "audit-state.json") held.await() })
        val started = async { h.runner.start(plan(), "cancelled") }
        runCurrent()
        val binding = checkNotNull(h.runner.devLastAuditRun)
        started.cancel()
        runCurrent()
        assertTrue(binding.ended.isCompleted)
        assertFalse(binding.hasWatcher())
        assertEquals(RunnerPhase.Idle, h.runner.state.value.phase)
        assertEquals(0, h.starts.get())
        assertTrue(h.evidence.publication().state.contains("\"phase\":\"idle\""))
    }

    @Test fun `publication timeout rejects without native dispatch`() = runTest(dispatcher) {
        val held = CompletableDeferred<Unit>()
        val h = harness(beforeWrite = { if (it == "audit-state.json") held.await() })
        val started = async { h.runner.start(plan(), "timeout") }
        runCurrent()
        advanceTimeBy(5_001); runCurrent()
        assertTrue(started.await() is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
        assertTrue(checkNotNull(h.runner.devLastAuditRun).ended.isCompleted)
    }

    @Test fun `cancellation inside the final synchronous callback gate prevents dispatch`() = runTest(dispatcher) {
        val h = harness()
        lateinit var started: Deferred<RunnerCommandResult>
        h.finalGateHook.set { started.cancel() }
        started = async { h.runner.start(plan(), "cancel-at-final-gate") }
        runCurrent()
        assertTrue(started.isCancelled)
        assertEquals(0, h.starts.get())
        assertTrue(checkNotNull(h.runner.devLastAuditRun).ended.isCompleted)
        assertEquals(RunnerPhase.Idle, h.runner.state.value.phase)
    }

    @Test fun `real atomic publication failure rejects without dispatch`() = runTest(dispatcher) {
        val h = harness(initializeIdle = false)
        val destination = File(root, ".dev-session/audit-state.json")
        assertTrue(destination.mkdirs()) // An actual move over this directory must fail.
        assertTrue(h.runner.start(plan(), "write-failure") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
        assertTrue(checkNotNull(h.runner.devLastAuditRun).ended.isCompleted)
    }

    @Test fun `missing publisher fails closed despite available in-memory authority`() = runTest(dispatcher) {
        val h = harness(missingPublisher = true)
        assertTrue(h.runner.start(plan(), "missing-publisher") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
        assertTrue(checkNotNull(h.runner.devLastAuditRun).ended.isCompleted)
    }

    @Test fun `lease loss during publication rejects even before its watcher runs`() = runTest(dispatcher) {
        val held = CompletableDeferred<Unit>()
        val h = harness(beforeWrite = { if (it == "audit-state.json") held.await() })
        val started = async { h.runner.start(plan(), "expired") }
        runCurrent()
        h.lease.value = null
        held.complete(Unit)
        runCurrent()
        assertTrue(started.await() is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
    }

    @Test fun `final command gate rejects a lease replacement after a successful publication`() = runTest(dispatcher) {
        val h = harness()
        h.finalGateHook.set { h.lease.value = checkNotNull(h.lease.value).copy(sessionId = "c".repeat(32)) }
        assertTrue(h.runner.start(plan(), "new-lease") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
    }

    @Test fun `legitimate permit progress and equal lease do not invalidate a running publication`() = runTest(dispatcher) {
        val h = harness()
        h.finalGateHook.set {
            val run = checkNotNull(h.runner.devLastAuditRun).run
            h.evidence.receive(challenge(run))
            h.evidence.receive(persistAck(run))
            h.lease.value = checkNotNull(h.lease.value).copy()
        }
        assertEquals(RunnerCommandResult.Accepted, h.runner.start(plan(), "permit-progress"))
        assertEquals(1, h.starts.get())
        h.callbacks.single().onFinished(RunOutcome.COMPLETED, null)
    }

    @Test fun `current error phase rejects an otherwise matching publication`() = runTest(dispatcher) {
        val h = harness()
        h.finalGateHook.set { h.evidence.rejectInvalid() }
        assertTrue(h.runner.start(plan(), "error") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
    }

    @Test fun `error and recovery phase ABA invalidates the old running publication`() = runTest(dispatcher) {
        val h = harness()
        h.finalGateHook.set {
            val run = checkNotNull(h.runner.devLastAuditRun).run
            h.evidence.rejectInvalid()
            h.evidence.receive(challenge(run))
        }
        assertTrue(h.runner.start(plan(), "phase-aba") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
    }

    @Test fun `same identity re-registration rejects old ACK without finishing the new registration`() = runTest(dispatcher) {
        val h = harness()
        h.finalGateHook.set {
            val run = checkNotNull(h.runner.devLastAuditRun).run
            h.evidence.finish(run)
            assertTrue(h.evidence.register(run))
        }
        assertTrue(h.runner.start(plan(), "registration-aba") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
        assertTrue(h.evidence.publication().state.contains("\"phase\":\"running\""))
    }

    @Test fun `different run cannot borrow an old publication or be revoked by its cleanup`() = runTest(dispatcher) {
        val h = harness()
        lateinit var newer: DevAuditRunBinding
        h.finalGateHook.set {
            val old = checkNotNull(h.runner.devLastAuditRun).run
            h.evidence.finish(old)
            newer = checkNotNull(h.evidence.binding(checkNotNull(h.lease.value), "newer", "9".repeat(32)))
            assertTrue(h.evidence.register(newer))
        }
        assertTrue(h.runner.start(plan(), "older") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
        assertTrue(h.evidence.publication().state.contains(newer.runNonce))
        assertTrue(h.evidence.publication().state.contains("\"phase\":\"running\""))
    }

    @Test fun `finish before dispatch invalidates ACK and leaves no native run`() = runTest(dispatcher) {
        val h = harness()
        h.finalGateHook.set { it.onFinished(RunOutcome.CANCELLED, null) }
        assertTrue(h.runner.start(plan(), "finished") is RunnerCommandResult.Rejected)
        assertEquals(0, h.starts.get())
    }

    @Test fun `ordinary No does not enter the audit publisher`() = runTest(dispatcher) {
        val h = harness(audit = false, beforeWrite = { throw IOException("ordinary must not publish") })
        assertEquals(RunnerCommandResult.Accepted, h.runner.start(plan(), "ordinary"))
        assertEquals(1, h.starts.get())
        assertNull(h.runner.devLastAuditRun)
        h.callbacks.single().onFinished(RunOutcome.COMPLETED, null)
    }
}
