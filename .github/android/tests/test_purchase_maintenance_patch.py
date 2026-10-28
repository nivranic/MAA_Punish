"""Pinned maintenance producer composition; JVM tests exercise actual consumers."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/android"))
from maafwapp_patches import PATCHES
from maafwapp_purchase_patches import PURCHASE_PATCHES
from patch_maafwapp import patched_text


class PurchaseMaintenancePatchTest(unittest.TestCase):
    def compose(self, name):
        source = (Path(__file__).parent / f"fixtures/{name}.bc55dac.kt").read_text(encoding="utf-8")
        patches = [p for p in PATCHES if p[0].endswith(f"/{name}.kt")]
        for path, old, new in patches:
            source = patched_text(source, path, old, new)
        repeated = source
        for path, old, new in patches:
            repeated = patched_text(repeated, path, old, new)
        self.assertEqual(source, repeated)
        return source

    def test_fixed_native_observation_is_after_wait_and_before_task_finish(self):
        source = self.compose("MaaRunner")
        wait = source.index("val status = lib.MaaTaskerWait(currentTasker, taskId)")
        history = source.index("TaskRecoveryHistory.inspect(", wait)
        maintenance = source.index("PurchaseMaintenanceNative.suffix(", history)
        finish = source.index("notify { onTaskFinished(", maintenance)
        self.assertLess(wait, history)
        self.assertLess(history, maintenance)
        self.assertLess(maintenance, finish)
        self.assertIn('payload.tasks.size == 1 && task.taskName == "Screencap"', source[maintenance:finish])

    def test_matching_callback_produces_log_before_execution_marker(self):
        source = self.compose("MaaFrameworkRunnerPort")
        callback = source.index("fun onFinished(outcome: Int, reason: String?)")
        prior_stop = source.index("val maintenanceStopped =", callback)
        finish_guard = source.index("auditBinding?.finish(", prior_stop)
        ownership = source.index("if (!ownsAuditRun()) return", finish_guard)
        observation = source.index("PurchaseMaintenanceTerminal.render(", ownership)
        terminal = source.index("emit(RunnerEvent.ExecutionFinished(result))", observation)
        self.assertLess(prior_stop, finish_guard)
        self.assertLess(ownership, observation)
        self.assertLess(observation, terminal)
        self.assertIn("plan.tasks.map { it.taskName to it.entry }", source)

    def test_each_added_anchor_is_idempotent_and_ambiguous_source_rejects(self):
        for path, old, new in PURCHASE_PATCHES:
            with self.subTest(path=path):
                self.assertEqual(new, patched_text(old, path, old, new))
                self.assertEqual(new, patched_text(new, path, old, new))
                for ambiguous in (old + old, new + new, old + new):
                    with self.assertRaises(ValueError):
                        patched_text(ambiguous, path, old, new)


if __name__ == "__main__":
    unittest.main()
