package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.MaaDispatchers
import com.aliothmoon.maafw.constant.AppPaths
import com.aliothmoon.maafw.domain.ControllerDefinition
import com.aliothmoon.maafw.domain.ProjectDefinition
import com.aliothmoon.maafw.domain.ResourceDefinition
import com.aliothmoon.maafw.domain.RunConfigurationId
import com.aliothmoon.maafw.i18n.UiText
import com.aliothmoon.maafw.project.FakeProjectRepository
import com.aliothmoon.maafw.project.ProjectState
import io.mockk.every
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import kotlin.io.path.createTempDirectory

/** Result diagnostics use the real recorder, normal JSONL writer and debug-disabled path. */
@OptIn(ExperimentalCoroutinesApi::class)
class RunLogRecorderRecoveryTest {
    private val dispatcher = UnconfinedTestDispatcher()
    private lateinit var logDir: File
    private var now = 1_000_000L

    @Before fun setUp() {
        logDir = createTempDirectory("recovery-recorder").toFile()
        mockkObject(AppPaths)
        every { AppPaths.LOG_DIR } returns logDir
        mockkObject(MaaDispatchers)
        every { MaaDispatchers.IO } returns dispatcher
    }

    @After fun tearDown() {
        unmockkObject(AppPaths)
        unmockkObject(MaaDispatchers)
        logDir.deleteRecursively()
    }

    private fun TestScope.recorder(runner: RunnerPort) = RunLogRecorder(
        focusDispatcher = FocusDispatcher(
            projectRepository = FakeProjectRepository(ProjectState.Ready(DEFINITION, emptyList())),
            resolver = PassthroughFocusContentResolver,
            runnerPort = runner,
            scope = backgroundScope,
        ),
        store = RunSessionLogStore(),
        renderText = { (it as? UiText.Verbatim)?.value ?: "<res>" },
        includeDetails = { false },
        scope = backgroundScope,
        clock = { now },
    )

    private fun plan(name: String) = RunPlan(
        projectName = "demo",
        projectVersion = "1",
        controller = ControllerDefinition(),
        resource = ResourceDefinition(name = "official", paths = listOf("resource")),
        runConfigurationId = RunConfigurationId("cfg"),
        tasks = listOf(RuntimeTask(taskName = name, entry = name, pipelineOverrides = emptyList())),
    )

    private fun recordsByTask(): Map<String, List<RunSessionRecord>> =
        File(logDir, "run").listFiles().orEmpty().map { file ->
            file.readLines().filter(String::isNotBlank).map {
                LENIENT.decodeFromString(RunSessionRecord.serializer(), it)
            }
        }.associateBy { (it.first() as RunSessionRecord.Header).tasks.first() }

    private fun List<RunSessionRecord>.texts() = filterIsInstance<RunSessionRecord.Line>().map { it.text }

    @Test fun `raw wait and effective result are persisted before footer without debug details`() = runTest(dispatcher) {
        val runner = RecordingEventRunnerPort()
        val recorder = recorder(runner)
        val message = "succeeded; task_id=71; wait_status=3000; " +
            "recovery_history=shared_recovery_visited; native_status=3000"
        val result = ExecutionResult.CompletedWithFailures(listOf(TaskResult("first", false, message)))
        recorder.begin(plan("first"), "run")
        val ending = async { recorder.end("run", RunEndReason.Ran(result)) }
        runner.emit(RunnerEvent.ExecutionFinished(result), "run")
        ending.await()

        val records = recordsByTask().getValue("first")
        assertEquals(listOf("task_result execution_id=run result_index=0 task=first effective_success=false; $message"), records.texts())
        assertTrue(records.last() is RunSessionRecord.Footer)
        assertEquals(RunSessionOutcome.COMPLETED_WITH_FAILURES, (records.last() as RunSessionRecord.Footer).outcome)
        assertNull(records.filterIsInstance<RunSessionRecord.Line>().single().detail)
    }

    @Test fun `unknown native status and missing messages never fabricate native fields`() = runTest(dispatcher) {
        val runner = RecordingEventRunnerPort()
        val recorder = recorder(runner)
        val message = "succeeded; task_id=72; wait_status=3000; " +
            "recovery_history=task_detail_unavailable; native_status=null"
        val result = ExecutionResult.CompletedWithFailures(listOf(
            TaskResult("unknown", false, message), TaskResult("missing", false),
        ))
        recorder.begin(plan("unknown"), "run")
        runner.emit(RunnerEvent.ExecutionFinished(result), "run")
        recorder.end("run", RunEndReason.Ran(result))

        assertEquals(listOf(
            "task_result execution_id=run result_index=0 task=unknown effective_success=false; $message",
            "task_result execution_id=run result_index=1 task=missing effective_success=false; ",
        ), recordsByTask().getValue("unknown").texts())
    }

    @Test fun `late results retain their execution id after the next run begins`() = runTest(dispatcher) {
        val runner = RecordingEventRunnerPort()
        val recorder = recorder(runner)
        // Equal messages in different runs must not be removed by the shared composer's dedup.
        val old = ExecutionResult.CompletedWithFailures(listOf(TaskResult("same", false, "PostTask 被拒绝")))
        val fresh = ExecutionResult.CompletedWithFailures(listOf(TaskResult("same", false, "PostTask 被拒绝")))
        recorder.begin(plan("old"), "old-run")
        val endingOld = async { recorder.end("old-run", RunEndReason.Ran(old)) }
        now += 1_000
        recorder.begin(plan("fresh"), "fresh-run")
        runner.emit(RunnerEvent.ExecutionFinished(old), "old-run")
        endingOld.await()
        assertNull(recorder.lastUserFacing.value)
        runner.emit(RunnerEvent.ExecutionFinished(fresh), "fresh-run")
        recorder.end("fresh-run", RunEndReason.Ran(fresh))

        val records = recordsByTask()
        assertEquals(listOf("task_result execution_id=old-run result_index=0 task=same effective_success=false; PostTask 被拒绝"), records.getValue("old").texts())
        assertEquals(listOf("task_result execution_id=fresh-run result_index=0 task=same effective_success=false; PostTask 被拒绝"), records.getValue("fresh").texts())
        assertTrue(records.values.all { it.last() is RunSessionRecord.Footer })
    }

    private companion object {
        val LENIENT = Json { ignoreUnknownKeys = true }
        val DEFINITION = ProjectDefinition(
            name = "demo", version = "1", controllers = listOf(ControllerDefinition()),
            resources = emptyList(), tasks = emptyList(), groups = emptyList(),
            options = emptyMap(), templates = emptyList(),
        )
    }
}
