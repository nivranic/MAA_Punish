package com.aliothmoon.maafw.runner

import com.aliothmoon.maafw.R
import com.aliothmoon.maafw.i18n.UiText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RunLogComposerRecoveryTest {
    private val composer = RunLogComposer()
    private val context = RunLogContext(currentTaskName = "幻痛囚笼")

    private fun compose(event: RunnerEvent, at: Long = 0) =
        composer.compose(event, at, at, context)

    @Test
    fun `native success followed by effective failure emits only failure`() {
        compose(RunnerEvent.Callback("Tasker.Task.Starting", "{}"), 1000)
        assertNull(compose(RunnerEvent.Callback("Tasker.Task.Succeeded", "{}"), 2000))
        val failed = compose(RunnerEvent.TaskFinished(0, false), 3000)
        assertEquals(RunLogKind.Error, failed?.kind)
        assertEquals(
            UiText.Resource(R.string.run_log_task_elapsed, listOf(
                UiText.Resource(R.string.run_log_task_failed, listOf("幻痛囚笼")),
                UiText.Resource(R.string.run_log_elapsed_s, listOf(2L)),
            )),
            failed?.text,
        )
        assertNull(compose(RunnerEvent.Callback("Tasker.Task.Succeeded", "{}"), 4000))
    }

    @Test
    fun `next successful task gets its own elapsed time and result`() {
        compose(RunnerEvent.Callback("Tasker.Task.Starting", "{}"), 1000)
        compose(RunnerEvent.TaskFinished(0, false), 2000)
        compose(RunnerEvent.Callback("Tasker.Task.Starting", "{}"), 5000)
        val completed = compose(RunnerEvent.TaskFinished(1, true), 8000)
        assertEquals(RunLogKind.Success, completed?.kind)
        assertEquals(
            UiText.Resource(R.string.run_log_task_elapsed, listOf(
                UiText.Resource(R.string.run_log_task_succeeded, listOf("幻痛囚笼")),
                UiText.Resource(R.string.run_log_elapsed_s, listOf(3L)),
            )),
            completed?.text,
        )
    }

    @Test
    fun `cancelled task emits no completion and clears elapsed time`() {
        compose(RunnerEvent.Callback("Tasker.Task.Starting", "{}"), 1000)
        assertNull(compose(RunnerEvent.TaskFinished(0, false, true), 2000))
        val completed = compose(RunnerEvent.TaskFinished(1, true), 8000)
        assertEquals(
            UiText.Resource(R.string.run_log_task_succeeded, listOf("幻痛囚笼")),
            completed?.text,
        )
    }
}
