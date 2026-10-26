package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.ITextInputSink
import com.aliothmoon.maafw.MaaDispatchers
import com.aliothmoon.maafw.constant.AppPaths
import com.aliothmoon.maafw.domain.ControllerDefinition
import com.aliothmoon.maafw.domain.ProjectDefinition
import com.aliothmoon.maafw.domain.ResourceDefinition
import com.aliothmoon.maafw.domain.RunConfigurationId
import com.aliothmoon.maafw.domain.RunMode
import com.aliothmoon.maafw.i18n.UiText
import com.aliothmoon.maafw.privileged.FakePrivilegedService
import com.aliothmoon.maafw.privileged.FakePrivilegedServicePort
import com.aliothmoon.maafw.project.FakeProjectRepository
import com.aliothmoon.maafw.project.PiInstaller
import com.aliothmoon.maafw.project.ProjectState
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import kotlin.io.path.createTempDirectory

/** The existing terminal callback carries the remote observation; no new callback or event type. */
@OptIn(ExperimentalCoroutinesApi::class)
class MaaFrameworkRecoveryResultLogTest {
    private val dispatcher = UnconfinedTestDispatcher()
    private lateinit var root: File
    private val callbacks = mutableListOf<MaaFrameworkRunnerPort.ExecutionCallback>()
    private val events = mutableListOf<RunnerEventEnvelope>()

    @Before fun setUp() {
        root = createTempDirectory("recovery-summary").toFile()
        mockkObject(MaaDispatchers)
        every { MaaDispatchers.IO } returns dispatcher
        every { MaaDispatchers.Default } returns dispatcher
        mockkObject(AppPaths)
        every { AppPaths.ROOT } returns root
        every { AppPaths.LOG_DIR } returns File(root, "log").apply { mkdirs() }
    }

    @After fun tearDown() {
        unmockkObject(AppPaths)
        unmockkObject(MaaDispatchers)
        root.deleteRecursively()
    }

    private fun plan() = RunPlan(
        projectName = "demo", projectVersion = "1", controller = ControllerDefinition(),
        resource = ResourceDefinition("official", listOf("base")),
        runConfigurationId = RunConfigurationId("cfg"),
        tasks = listOf(RuntimeTask("first", "First", emptyList())),
    )

    private fun TestScope.runner(): MaaFrameworkRunnerPort {
        val installer = mockk<PiInstaller>()
        every { installer.installedDir() } returns File(root, "pi").apply { mkdirs() }
        val runner = MaaFrameworkRunnerPort(
            installer = installer, apkPath = "/apk", nativeLibraryDir = "/lib",
            runMode = { RunMode.BACKGROUND }, resolutionPreset = { ResolutionPresets.builtIn.first() },
            debugMode = { false }, saveOnError = { false }, scope = backgroundScope,
            servicePort = FakePrivilegedServicePort(FakePrivilegedService()),
            textInputSink = { mockk<ITextInputSink>() },
        )
        runner.bindRunnerCallback = { _, callback -> callbacks += callback }
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
            runner.events.collect { events += it }
        }
        return runner
    }

    private fun TestScope.recorder(runner: RunnerPort) = RunLogRecorder(
        focusDispatcher = FocusDispatcher(
            FakeProjectRepository(ProjectState.Ready(DEFINITION, emptyList())),
            PassthroughFocusContentResolver, runner, backgroundScope,
        ),
        store = RunSessionLogStore(), renderText = { (it as? UiText.Verbatim)?.value ?: "<res>" },
        includeDetails = { false }, scope = backgroundScope,
    )

    @Test fun `recovery observation is logged on its execution before terminal and footer`() = runTest(dispatcher) {
        val runner = runner()
        val recorder = recorder(runner)
        recorder.begin(plan(), "recovery-run")
        assertEquals(RunnerCommandResult.Accepted, runner.start(plan(), "recovery-run"))
        callbacks.single().onTaskStarted("first", 0, 1)
        callbacks.single().onTaskFinished("first", false, "task-failed", false)
        callbacks.single().onFinished(RunOutcome.COMPLETED_WITH_FAILURES, SUMMARY)
        val result = runner.state.value.latestResult!!
        recorder.end("recovery-run", RunEndReason.Ran(result))

        val log = events.indexOfFirst { it.event is RunnerEvent.Log }
        val terminal = events.indexOfFirst { it.event is RunnerEvent.ExecutionFinished }
        assertTrue(log >= 0 && log < terminal)
        assertEquals("recovery-run", events[log].executionId)
        assertEquals("recovery_result execution_id=recovery-run; $SUMMARY", (events[log].event as RunnerEvent.Log).message)
        assertTrue(result is ExecutionResult.CompletedWithFailures)
        assertFalse(result.taskResults.single().success)
        assertEquals(RunnerPhase.Idle, runner.state.value.phase)
        val file = File(root, "log/run").listFiles()!!.single()
        val records = file.readLines().filter(String::isNotBlank).map {
            Json.decodeFromString(RunSessionRecord.serializer(), it)
        }
        val line = records.filterIsInstance<RunSessionRecord.Line>().single { it.text.startsWith("recovery_result ") }
        assertEquals("recovery_result execution_id=recovery-run; $SUMMARY", line.text)
        assertTrue(records.last() is RunSessionRecord.Footer)
    }

    @Test fun `normal and agent launch reasons keep their original result semantics without recovery logs`() = runTest(dispatcher) {
        val runner = runner()
        runner.start(plan(), "normal")
        callbacks.last().onFinished(RunOutcome.COMPLETED, null)
        assertTrue(runner.state.value.latestResult is ExecutionResult.Completed)
        runner.start(plan(), "agent-failed")
        callbacks.last().onFinished(RunOutcome.AGENT_LAUNCH_FAILED, "0")
        assertTrue(runner.state.value.latestResult is ExecutionResult.Failed)
        assertTrue(events.none { it.event is RunnerEvent.Log })
    }

    @Test fun `old callback cannot route a recovery summary into a newer execution`() = runTest(dispatcher) {
        val runner = runner()
        runner.start(plan(), "old")
        val old = callbacks.single()
        old.onFinished(RunOutcome.COMPLETED, null)
        runner.start(plan(), "new")
        val before = events.size
        old.onFinished(RunOutcome.COMPLETED_WITH_FAILURES, SUMMARY)
        assertEquals(before, events.size)
        assertEquals("new", runner.state.value.activeExecution?.executionId)
        assertEquals(RunnerPhase.Running, runner.state.value.phase)
        callbacks.last().onFinished(RunOutcome.COMPLETED_WITH_FAILURES, SUMMARY)
        val logs = events.filter { it.event is RunnerEvent.Log }
        assertEquals(listOf("new"), logs.map { it.executionId })
    }

    private companion object {
        // Fixture payload only: producer tests independently bind real nanoTime observations.
        const val SUMMARY = "recovery_history:shared_recovery_visited; task_id=77; " +
            "native_running_observed=false; native_idle_observed_at_ns=101; runner_running_cleared_at_ns=102"
        val DEFINITION = ProjectDefinition(
            name = "demo", version = "1", controllers = listOf(ControllerDefinition()),
            resources = emptyList(), tasks = emptyList(), groups = emptyList(),
            options = emptyMap(), templates = emptyList(),
        )
    }
}
