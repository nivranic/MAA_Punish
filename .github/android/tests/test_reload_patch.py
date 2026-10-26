"""Fast safety checks for the reload patch recipe; JVM/native behavior is tested separately."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/android"))
from maafwapp_reload_patches import NATIVE_MEMBERS, RELOAD_PATCHES
from patch_maafwapp import patched_text


class ReloadPatchTest(unittest.TestCase):
    def test_each_anchor_is_idempotent_and_ambiguous_input_fails(self):
        for path, old, new in RELOAD_PATCHES:
            with self.subTest(path=path, old=old[:60]):
                self.assertEqual(new, patched_text(old, path, old, new))
                self.assertEqual(new, patched_text(new, path, old, new))
                for ambiguous in (old + old, new + new, old + new):
                    with self.assertRaises(ValueError):
                        patched_text(ambiguous, path, old, new)

    def test_maintenance_checks_native_running_before_releasing_resource(self):
        check = NATIVE_MEMBERS.index("while (lib.MaaTaskerRunning(handle).toInt() != 0)")
        release = NATIVE_MEMBERS.index("releaseResource(lib)")
        self.assertLess(check, release)
        self.assertIn("if (cancelled() || System.nanoTime()", NATIVE_MEMBERS[check:release])
        self.assertNotIn("takeTasker()", NATIVE_MEMBERS[:release])

    def test_prepare_has_no_task_or_display_launch(self):
        implementation = NATIVE_MEMBERS.split("private fun devPrepare", 1)[1]
        self.assertIn("payload.tasks.isNotEmpty()", implementation)
        self.assertIn("prepare(lib, payload)", implementation)
        for forbidden in ("runPlan(", "MaaTaskerPostTask", "startVirtualDisplay", "VirtualDisplayManager.start", "releaseController"):
            self.assertNotIn(forbidden, implementation)

    def test_aidl_only_appends_a_fixed_new_transaction(self):
        aidl = [(old, new) for path, old, new in RELOAD_PATCHES if path.endswith("RemoteService.aidl")]
        self.assertEqual(1, len(aidl))
        old, new = aidl[0]
        self.assertIn("String devReload(String requestJson) = 86;", new)
        self.assertIn(old.removesuffix("\n}"), new)

    def test_repository_scope_closing_is_not_mistaken_for_already_applied(self):
        # The exact fixed upstream file includes the nested reloadMutex tail
        # which makes short closing insertions look spuriously already applied.
        source = (Path(__file__).parent / "fixtures/ProjectRepository.bc55dac.kt").read_text(encoding="utf-8")
        patches = [(path, old, new) for path, old, new in RELOAD_PATCHES if path.endswith("ProjectRepository.kt")]
        for path, old, new in patches:
            source = patched_text(source, path, old, new)
        self.assertEqual(source.count("{"), source.count("}"))
        self.assertIn("        }\n    }\n\n    override suspend fun ensureLoaded()", source)
        self.assertIn("// Keep the entire loader read mutually exclusive", source)
        repeated = source
        for path, old, new in patches:
            repeated = patched_text(repeated, path, old, new)
        self.assertEqual(source, repeated)

    def test_pipeline_run_identity_is_last_and_not_an_agent_environment_key(self):
        source = (ROOT / ".github/android/overlays/app/src/main/java/com/aliothmoon/maafw/runner/RunPlanRuntimePayload.kt").read_text(encoding="utf-8")
        self.assertIn("task.pipelineOverrides + auditOverride", source)
        self.assertIn('put("购买",', source)
        self.assertIn('put("audit_run", auditRun?.attach()', source)
        self.assertIn('put("enabled", false)', source)
        environment = (ROOT / ".github/android/overlays/app/src/main/java/com/aliothmoon/maafw/devsession/DevAuditEnvironment.kt").read_text(encoding="utf-8")
        self.assertNotIn("execution_id", environment)

    def test_actual_runner_patch_sequence_is_idempotent(self):
        # Exercise the complete pinned runner, not concatenated isolated anchors:
        # audit start, preparation, callback and conditional stop patches overlap
        # the same lifecycle and must compose on a real consumer.
        source = (Path(__file__).parent / "fixtures/MaaFrameworkRunnerPort.bc55dac.kt").read_text(encoding="utf-8")
        patches = [(path, old, new) for path, old, new in RELOAD_PATCHES if path.endswith("/runner/MaaFrameworkRunnerPort.kt")]
        self.assertTrue(patches)
        for path, old, new in patches:
            source = patched_text(source, path, old, new)
        repeated = source
        for path, old, new in patches:
            repeated = patched_text(repeated, path, old, new)
        self.assertEqual(source, repeated)

        registration = source.index("!auditBinding.register()")
        dispatch = source.index("service.startRun(runPlanWireJson.encodeToString(payload))")
        self.assertLess(registration, dispatch)
        self.assertIn("auditBinding?.lease, auditBinding?.run", source)
        self.assertIn("DevAuditEvidence.newNonce()", source)
        self.assertIn("devLastAuditRun?.takeIf { it.executionId == executionId }?.revoke()", source)

    def test_actual_native_runner_patch_sequence_is_idempotent(self):
        source = (Path(__file__).parent / "fixtures/MaaRunner.bc55dac.kt").read_text(encoding="utf-8")
        patches = [(path, old, new) for path, old, new in RELOAD_PATCHES if path.endswith("/remote/MaaRunner.kt")]
        self.assertTrue(patches)
        for path, old, new in patches:
            source = patched_text(source, path, old, new)
        repeated = source
        for path, old, new in patches:
            repeated = patched_text(repeated, path, old, new)
        self.assertEqual(source, repeated)
        self.assertIn("loadedAuditEnvironment == auditEnvironment && loadedAgents == payload.agents", source)
        self.assertIn("DevAuditEnvironment.installInProcess(auditEnvironment, payload.piEnv)", source)


if __name__ == "__main__":
    unittest.main()
