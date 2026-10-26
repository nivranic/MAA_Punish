"""Pinned consumer composition and dispatch/cleanup ordering, not Android acceptance."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/android"))
from maafwapp_patches import PATCHES
from maafwapp_recovery_patches import RECOVERY_PATCHES
from patch_maafwapp import patched_text


class RecoveryPatchTest(unittest.TestCase):
    def compose(self, filename, extension="kt"):
        source = (Path(__file__).parent / ("fixtures/" + filename + ".bc55dac." + extension)).read_text(encoding="utf-8")
        patches = [(path, old, new) for path, old, new in PATCHES if path.endswith("/" + filename + "." + extension)]
        self.assertTrue(patches)
        for path, old, new in patches:
            source = patched_text(source, path, old, new)
        repeated = source
        for path, old, new in patches:
            repeated = patched_text(repeated, path, old, new)
        self.assertEqual(source, repeated)
        return source

    def test_pinned_runner_all_patches_compose_and_repeat(self):
        source = self.compose("MaaRunner")
        wait = source.index("val status = lib.MaaTaskerWait(currentTasker, taskId)")
        inspect = source.index("TaskRecoveryHistory.inspect(", wait)
        success = source.index("val success = recoveryGate.success(status, history)", inspect)
        notify = source.index("notify { onTaskFinished(task.taskName, success", success)
        self.assertLess(wait, inspect)
        self.assertLess(inspect, success)
        self.assertLess(success, notify)
        self.assertIn("if (cancelled || recoveryGate.aborted) return@forEachIndexed", source)
        self.assertIn("running && recoveryHistoryOwner === historyOwner", source)
        self.assertIn("tasker == currentTasker && !stopRequested", source)
        self.assertIn("cancelled = stopped", source[notify:])
        self.assertIn("task_id=$taskId; wait_status=$status", source[success:notify])
        self.assertIn("native_status=${history.nativeStatus}", source[success:notify])

    def test_jna_reflected_reference_constructors_survive_shrinking(self):
        source = self.compose("proguard-rules", "pro")
        for reference in ("IntByReference", "ByteByReference", "LongByReference"):
            self.assertEqual(1, source.count(f"-keep class com.sun.jna.ptr.{reference} {{ public <init>(); }}"))
        self.assertNotIn("-keep class com.sun.jna.ptr.**", source)
        self.assertNotIn("子包（ptr / internal / win32）没有 native 反查", source)

    def test_task_result_diagnostics_are_recorded_before_session_drains(self):
        source = self.compose("RunLogRecorder")
        terminal = source.index("if (event is RunnerEvent.ExecutionFinished)")
        results = source.index("event.result.taskResults.forEachIndexed", terminal)
        recorded = source.index("record(envelope.copy(event = RunnerEvent.Log(", results)
        drained = source.index("session?.drained?.complete(Unit)", recorded)
        self.assertLess(results, recorded)
        self.assertLess(recorded, drained)
        self.assertIn("effective_success=${result.success}; ${result.message.orEmpty()}", source[recorded:drained])
        self.assertIn("execution_id=${envelope.executionId}", source[recorded:drained])
        self.assertNotIn("latestResult", source[terminal:drained])

    def test_native_running_barrier_precedes_finish_and_busy_release(self):
        source = self.compose("MaaRunner")
        barrier = source.index("RecoveryNativeStopBarrier.awaitStopped(")
        retained = source.index("if (recoveryCleanup?.stopped == false)", barrier)
        busy_release = source.index("running = false", retained)
        witness = source.index("native_idle_observed_at_ns=${System.nanoTime()}", retained)
        cleared = source.index("runner_running_cleared_at_ns=${System.nanoTime()}", busy_release)
        finish = source.index("notify { onFinished(outcome, reason) }", busy_release)
        self.assertLess(barrier, retained)
        self.assertLess(retained, busy_release)
        self.assertLess(witness, busy_release)
        self.assertLess(busy_release, cleared)
        self.assertLess(busy_release, finish)
        self.assertIn("return", source[retained:busy_release])
        self.assertNotIn("MaaTaskerDestroy", source[barrier:finish])
        self.assertNotIn("MaaControllerDestroy", source[barrier:finish])

    def test_recovery_summary_uses_existing_ordered_lane_before_terminal_marker(self):
        source = self.compose("MaaFrameworkRunnerPort")
        callback = source.index("fun onFinished(outcome: Int, reason: String?)")
        owner = source.index("if (!ownsAuditRun()) return", callback)
        summary = source.index('if (reason?.startsWith("recovery_history:") == true)', owner)
        event = source.index("recovery_result execution_id=$executionId; $reason", summary)
        terminal = source.index("emit(RunnerEvent.ExecutionFinished(result))", event)
        self.assertLess(owner, summary)
        self.assertLess(summary, event)
        self.assertLess(event, terminal)

    def test_pinned_jna_uses_fixed_abi_types_and_repeats(self):
        source = self.compose("MaaFrameworkLibrary")
        self.assertIn("nodeIdListSize: LongByReference, status: IntByReference", source)
        self.assertIn("actionId: LongByReference, completed: ByteByReference", source)
        self.assertNotIn("NativeLong", source)

    def test_new_anchors_reject_duplicate_or_mixed_source(self):
        for path, old, new in RECOVERY_PATCHES:
            with self.subTest(path=path, anchor=old[:50]):
                self.assertEqual(new, patched_text(old, path, old, new))
                self.assertEqual(new, patched_text(new, path, old, new))
                for bad in (old + old, new + new, old + new):
                    with self.assertRaises(ValueError):
                        patched_text(bad, path, old, new)


if __name__ == "__main__":
    unittest.main()
