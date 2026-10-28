package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.ITextInputSink
import com.aliothmoon.maafw.IMaaRunnerCallback
import com.aliothmoon.maafw.MaaDispatchers
import com.aliothmoon.maafw.bridge.NativeBridgeLib
import com.aliothmoon.maafw.constant.AppPaths
import com.aliothmoon.maafw.devsession.DevAuditEvidence
import com.aliothmoon.maafw.devsession.DevSessionHello
import com.aliothmoon.maafw.devsession.DevSessionLease
import com.aliothmoon.maafw.domain.*
import com.aliothmoon.maafw.i18n.UiText
import com.aliothmoon.maafw.maa.MaaStatus
import com.aliothmoon.maafw.maa.MaaFrameworkLibrary
import com.aliothmoon.maafw.maa.MaaFrameworkLoader
import com.aliothmoon.maafw.maa.PurchaseMaintenanceNative
import com.aliothmoon.maafw.maa.TaskRecoveryHistory
import com.aliothmoon.maafw.privileged.FakePrivilegedService
import com.aliothmoon.maafw.privileged.FakePrivilegedServicePort
import com.aliothmoon.maafw.project.FakeProjectRepository
import com.aliothmoon.maafw.project.PiInstaller
import com.aliothmoon.maafw.project.ProjectState
import com.aliothmoon.maafw.remote.AgentHost
import com.aliothmoon.maafw.remote.MaaRunner
import com.aliothmoon.maafw.remote.internal.VirtualDisplayManager
import com.aliothmoon.maafw.third.Ln
import com.sun.jna.Pointer
import com.sun.jna.ptr.ByteByReference
import com.sun.jna.ptr.IntByReference
import com.sun.jna.ptr.LongByReference
import io.mockk.*
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.ExecutorService
import kotlin.io.path.createTempDirectory

/** Real callback/recorder/file path, with scripted service/native observations and no hardware. */
@OptIn(ExperimentalCoroutinesApi::class)
class PurchaseMaintenanceResultLogTest {
    private val dispatcher = UnconfinedTestDispatcher()
    private lateinit var root: File
    private val callbacks = mutableListOf<MaaFrameworkRunnerPort.ExecutionCallback>()
    private val events = mutableListOf<RunnerEventEnvelope>()

    @Before fun setUp() {
        root = createTempDirectory("purchase-result").toFile()
        mockkObject(MaaDispatchers)
        every { MaaDispatchers.IO } returns dispatcher
        every { MaaDispatchers.Default } returns dispatcher
        mockkObject(AppPaths)
        every { AppPaths.ROOT } returns root
        every { AppPaths.LOG_DIR } returns File(root, "log").apply { mkdirs() }
    }

    @After fun tearDown() {
        unmockkAll()
        root.deleteRecursively()
    }

    private fun plan() = RunPlan(
        projectName = "demo", projectVersion = "1", controller = ControllerDefinition(),
        resource = ResourceDefinition("official", listOf("base")), runConfigurationId = RunConfigurationId("cfg"),
        tasks = listOf(RuntimeTask("Screencap", PurchaseMaintenanceNative.ENTRY, emptyList())),
    )

    private fun TestScope.runner(): MaaFrameworkRunnerPort {
        val installer = mockk<PiInstaller>()
        every { installer.installedDir() } returns File(root, "pi").apply { mkdirs() }
        val lease = MutableStateFlow<DevSessionLease?>(DevSessionLease("a".repeat(32), "b".repeat(32), auditLogging = true))
        val evidence = DevAuditEvidence("com.example.maafw", File(root, ".dev-session"), { 1_000L },
            { DevSessionHello(1, "com.example.maafw", "d".repeat(32), "e".repeat(32), 16_384) }, { lease.value })
        val runner = MaaFrameworkRunnerPort(
            installer = installer, apkPath = "/apk", nativeLibraryDir = "/lib",
            runMode = { RunMode.BACKGROUND }, resolutionPreset = { ResolutionPresets.builtIn.first() },
            debugMode = { false }, saveOnError = { false }, scope = backgroundScope,
            servicePort = FakePrivilegedServicePort(FakePrivilegedService()), textInputSink = { mockk<ITextInputSink>() },
            devSessionLease = lease, devAuditEvidence = evidence,
            devAuditPublisher = com.aliothmoon.maafw.devsession.DevAuditPublisher(evidence, dispatcher) { name, contents ->
                com.aliothmoon.maafw.devsession.DevSessionMailbox(root).publish(name, contents)
            },
        )
        runner.bindRunnerCallback = { _, callback -> callbacks += callback }
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { runner.events.collect { events += it } }
        return runner
    }

    private fun TestScope.recorder(runner: RunnerPort) = RunLogRecorder(
        focusDispatcher = FocusDispatcher(FakeProjectRepository(ProjectState.Ready(ProjectDefinition(
            name = "demo", version = "1", controllers = listOf(ControllerDefinition()), resources = emptyList(),
            tasks = emptyList(), groups = emptyList(), options = emptyMap(), templates = emptyList(),
        ), emptyList())), PassthroughFocusContentResolver, runner, backgroundScope),
        store = RunSessionLogStore(), renderText = { (it as? UiText.Verbatim)?.value ?: "<res>" },
        includeDetails = { false }, scope = backgroundScope,
    )

    private fun nativeMessage() = "succeeded; task_id=77; wait_status=3000; recovery_history=no_shared_recovery; native_status=3000" +
        checkNotNull(PurchaseMaintenanceNative.suffix(true, PurchaseMaintenanceNative.ENTRY, 77, MaaStatus.SUCCEEDED,
            TaskRecoveryHistory.Result(true, false, "no_shared_recovery", MaaStatus.SUCCEEDED, 1, 0, true), true, false))

    private fun records() = File(root, "log/run").listFiles()!!.single().readLines().filter(String::isNotBlank).map {
        Json.decodeFromString(RunSessionRecord.serializer(), it)
    }

    private fun sorted(value: JsonElement): JsonElement = when (value) {
        is JsonObject -> JsonObject(value.toSortedMap().mapValues { sorted(it.value) })
        is JsonArray -> JsonArray(value.map(::sorted))
        else -> value
    }
    private fun digest(value: JsonElement) = MessageDigest.getInstance("SHA-256")
        .digest(sorted(value).toString().toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it.toInt() and 255) }

    /** Prepared purchase data is synthetic; native terminal/result/footer come from actual production code. */
    private fun ready(runner: MaaFrameworkRunnerPort, label: String): String {
        val fixture = System.getProperty("purchase.terminal.fixture") ?: return READY
        val template = Json.parseToJsonElement(File(fixture).readText()).jsonObject
        val identity = JsonObject(checkNotNull(runner.devLastAuditRun).run.identity())
        val request = JsonObject(template.getValue("request").jsonObject + ("maintenance_identity" to identity))
        val receiptBase = buildJsonObject {
            put("version", 1); put("state", "confirmed_consumed"); put("request", request)
            put("reconciled_boottime_ms", 1000)
        }
        val receipt = JsonObject(receiptBase + ("checksum_sha256" to JsonPrimitive(digest(receiptBase))))
        val pending = template.getValue("pending")
        val claimHash = request.getValue("claim_sha256")
        val event = buildJsonObject {
            put("audit_event", "purchase_reconciliation_ready"); put("maintenance_identity", identity)
            put("request_id", request.getValue("request_id")); put("receipt_sha256", digest(receipt))
            put("pending_sha256", digest(pending)); put("claim_sha256", claimHash)
            put("runtime", request.getValue("runtime"))
        }
        val envelope = buildJsonObject {
            put("event", "maa_punish_audit"); put("session_id", identity.getValue("session_id"))
            put("stream_id", "e".repeat(32)); put("sequence", 1); put("monotonic_ns", System.nanoTime())
            put("level", "INFO"); put("logger", "synthetic.prepared-input"); put("message", sorted(event).toString())
        }
        System.getProperty("purchase.terminal.evidence.dir")?.let { dir ->
            File(dir).mkdirs()
            File(dir, "$label-binding.json").writeText(sorted(JsonObject(template + mapOf("request" to request, "receipt" to receipt))).toString() + "\n")
        }
        return envelope.toString()
    }

    private fun export(label: String) {
        System.getProperty("purchase.terminal.evidence.dir")?.let { dir ->
            File(dir).mkdirs()
            File(dir, "$label-run.jsonl").writeText(File(root, "log/run").listFiles()!!.single().readText())
        }
    }

    /** Executes the real remote loop against scripted native endpoints, forwarding its actual callbacks. */
    private fun runNative(appCallback: MaaFrameworkRunnerPort.ExecutionCallback, completed: Byte = 1, readyLine: String? = null) {
        val lib = mockk<MaaFrameworkLibrary>(relaxed = true)
        val callback = mockk<IMaaRunnerCallback>(relaxed = true)
        val tasker = Pointer(33)
        var buffer = 100L
        var posts = 0
        val strings = mutableMapOf<Long, String>()
        mockkStatic(Ln::class)
        every { Ln.i(any()) } just Runs
        every { Ln.w(any<String>()) } just Runs
        every { Ln.e(any<String>()) } just Runs
        every { Ln.e(any<String>(), any<Throwable>()) } just Runs
        val bridgeLoaded = NativeBridgeLib.LOADED
        NativeBridgeLib.LOADED = true
        mockkObject(MaaFrameworkLoader)
        every { MaaFrameworkLoader.library } returns lib
        mockkObject(VirtualDisplayManager)
        every { VirtualDisplayManager.getDisplayId() } returns 42
        every { lib.MaaControllerConnected(any()) } returns 1
        every { lib.MaaTaskerPostTask(tasker, any(), any()) } answers { posts++; 77L }
        every { lib.MaaTaskerWait(tasker, any()) } answers {
            readyLine?.let { appCallback.onAgentOutput(it, false) }
            MaaStatus.SUCCEEDED
        }
        every { lib.MaaStringBufferCreate() } answers { Pointer(++buffer) }
        every { lib.MaaStringBufferGet(any()) } answers { strings[Pointer.nativeValue(firstArg())] }
        every { lib.MaaTaskerGetTaskDetail(tasker, any(), any(), any(), any(), any()) } answers {
            strings[Pointer.nativeValue(arg(2))] = PurchaseMaintenanceNative.ENTRY
            arg<Pointer?>(3)?.setLong(0, 101)
            arg<LongByReference>(4).value = 1
            arg<IntByReference>(5).value = MaaStatus.SUCCEEDED
            1.toByte()
        }
        every { lib.MaaTaskerGetNodeDetail(tasker, any(), any(), any(), any(), any()) } answers {
            strings[Pointer.nativeValue(arg(2))] = PurchaseMaintenanceNative.ENTRY
            arg<ByteByReference>(5).value = completed
            1.toByte()
        }
        every { lib.MaaTaskerRunning(tasker) } returns 0
        every { callback.onTaskStarted(any(), any(), any()) } answers { appCallback.onTaskStarted(firstArg(), arg(1), arg(2)) }
        every { callback.onTaskFinished(any(), any(), any(), any()) } answers { appCallback.onTaskFinished(firstArg(), arg(1), arg(2), arg(3)) }
        every { callback.onFinished(any(), any()) } answers { appCallback.onFinished(firstArg(), arg(1)) }
        val native = MaaRunner(mockk<AgentHost>())
        fun field(name: String) = MaaRunner::class.java.getDeclaredField(name).apply { isAccessible = true }
        native.setCallback(callback)
        field("resource").set(native, Pointer(11)); field("controller").set(native, Pointer(22))
        field("tasker").set(native, tasker); field("boundDisplayId").set(native, 42); field("running").set(native, true)
        try {
            val payload = RunPlanPayload(emptyList(), 1280, 720,
                tasks = listOf(RuntimeTaskPayload("Screencap", PurchaseMaintenanceNative.ENTRY, emptyList())))
            MaaRunner::class.java.getDeclaredMethod("runPlan", RunPlanPayload::class.java)
                .apply { isAccessible = true }.invoke(native, payload)
            assertEquals(1, posts)
            assertFalse(native.isRunning())
        } finally {
            (field("worker").get(native) as ExecutorService).shutdownNow()
            NativeBridgeLib.LOADED = bridgeLoaded
        }
    }

    @Test fun `ready terminal task result and footer are reachable in actual recorder order`() = runTest(dispatcher) {
        val runner = runner()
        val recorder = recorder(runner)
        recorder.begin(plan(), "purchase")
        assertEquals(RunnerCommandResult.Accepted, runner.start(plan(), "purchase"))
        val callback = callbacks.single()
        runNative(callback, readyLine = ready(runner, "positive"))
        recorder.end("purchase", RunEndReason.Ran(checkNotNull(runner.state.value.latestResult)))
        val lines = records().filterIsInstance<RunSessionRecord.Line>()
        val ready = lines.indexOfFirst { it.text.contains("purchase_reconciliation_ready") }
        val terminal = lines.indexOfFirst { it.text.startsWith(PurchaseMaintenanceTerminal.PREFIX) }
        val taskResult = lines.indexOfFirst { it.text.startsWith("task_result ") }
        assertTrue(ready >= 0 && ready < terminal && terminal < taskResult)
        assertEquals(RunLogKind.Info, lines[terminal].kind)
        assertEquals(listOf("Screencap"), (records().first() as RunSessionRecord.Header).tasks)
        assertEquals(RunSessionOutcome.COMPLETED, (records().last() as RunSessionRecord.Footer).outcome)
        val logEvent = events.indexOfFirst { (it.event as? RunnerEvent.Log)?.message?.startsWith(PurchaseMaintenanceTerminal.PREFIX) == true }
        val finishedEvent = events.indexOfFirst { it.event is RunnerEvent.ExecutionFinished }
        assertTrue(logEvent >= 0 && logEvent < finishedEvent)
        export("positive")
    }

    @Test fun `late stdout is not invented or moved before native terminal`() = runTest(dispatcher) {
        val runner = runner()
        val recorder = recorder(runner)
        recorder.begin(plan(), "late")
        runner.start(plan(), "late")
        val callback = callbacks.single()
        val lateReady = ready(runner, "late")
        runNative(callback)
        recorder.end("late", RunEndReason.Ran(checkNotNull(runner.state.value.latestResult)))
        callback.onAgentOutput(lateReady, false)
        assertFalse(records().filterIsInstance<RunSessionRecord.Line>().any { it.text.contains("purchase_reconciliation_ready") })
        assertTrue(records().last() is RunSessionRecord.Footer)
        export("late")
    }

    @Test fun `actual native succeeded with incomplete maintenance node has no acceptance terminal`() = runTest(dispatcher) {
        val runner = runner()
        val recorder = recorder(runner)
        recorder.begin(plan(), "incomplete")
        runner.start(plan(), "incomplete")
        runNative(callbacks.single(), completed = 0, readyLine = ready(runner, "incomplete"))
        recorder.end("incomplete", RunEndReason.Ran(checkNotNull(runner.state.value.latestResult)))
        assertFalse(records().filterIsInstance<RunSessionRecord.Line>().any { it.text.startsWith(PurchaseMaintenanceTerminal.PREFIX) })
        export("incomplete")
    }

    @Test fun `stop and old callbacks cannot mint maintenance success`() = runTest(dispatcher) {
        val runner = runner()
        runner.start(plan(), "old")
        val old = callbacks.single()
        old.onTaskFinished("Screencap", true, nativeMessage(), false)
        runner.stop()
        old.onFinished(RunOutcome.COMPLETED, null)
        assertFalse(events.any { (it.event as? RunnerEvent.Log)?.message?.startsWith(PurchaseMaintenanceTerminal.PREFIX) == true })
        runner.start(plan(), "fresh")
        val before = events.size
        old.onFinished(RunOutcome.COMPLETED, null)
        assertEquals(before, events.size)
        callbacks.last().onTaskFinished("Screencap", true, nativeMessage(), true)
        callbacks.last().onFinished(RunOutcome.COMPLETED, null)
        assertFalse(events.any { (it.event as? RunnerEvent.Log)?.message?.startsWith(PurchaseMaintenanceTerminal.PREFIX) == true })
    }

    private companion object {
        const val READY = "{\"event\":\"maa_punish_audit\",\"message\":{\"audit_event\":\"purchase_reconciliation_ready\"}}"
    }
}
