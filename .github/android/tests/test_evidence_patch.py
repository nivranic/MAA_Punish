"""Compose diagnostics retention with the existing pinned native patches."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/android"))
from maafwapp_patches import PATCHES
from patch_maafwapp import patched_text


class EvidencePatchTest(unittest.TestCase):
    def compose(self, name):
        content = (Path(__file__).parent / f"fixtures/{name}.bc55dac.kt").read_text(encoding="utf-8")
        patches = [(p, old, new) for p, old, new in PATCHES if p.endswith(f"/{name}.kt")]
        for path, old, new in patches:
            content = patched_text(content, path, old, new)
        repeated = content
        for path, old, new in patches:
            repeated = patched_text(repeated, path, old, new)
        self.assertEqual(content, repeated)
        return content

    def test_app_sweeps_while_idle_under_its_existing_scope(self):
        content = self.compose("MaaFwApp")
        start = content.index("appScope.launch(Dispatchers.IO)")
        sweep = content.index("DebugEvidenceRetention.prune(", start)
        delay = content.index("kotlinx.coroutines.delay(", sweep)
        self.assertLess(start, sweep)
        self.assertLess(sweep, delay)
        self.assertIn("DebugEvidenceRetention.ownedRoots(AppPaths.ROOT)", content[sweep:delay])
        # No task/remote setup is required for the app's own idle sweep.
        self.assertIn("DevReloadCoordinator>().setup()", content)

    def test_privileged_sweep_covers_agent_without_a_second_watchdog(self):
        content = self.compose("RemoteServiceImpl")
        self.assertEqual(content.count('name = "remote-heartbeat-watchdog"'), 1)
        self.assertIn('File(it, "debug")', content)
        self.assertIn("it.name == AppFiles.LOG_DIR", content)
        start = content.index("private fun startHeartbeatWatchdog()")
        self.assertIn("pruneEvidenceIfDue()", content[start:])
        setup = content.index("override fun setup(")
        self.assertIn("pruneEvidenceIfDue()", content[setup:content.index("private fun pruneMaafwBackups()")])

    def test_privileged_idle_sweep_does_not_require_first_task_setup(self):
        content = self.compose("RemoteServiceImpl")
        sweep = content[content.index("private fun pruneEvidenceIfDue()"):content.index("private fun startHeartbeatWatchdog()")]
        self.assertIn("android.os.Environment.getExternalStorageDirectory()", sweep)
        self.assertIn('"Android/data/${BuildConfig.APPLICATION_ID}/files"', sweep)
        self.assertIn("DebugEvidenceRetention.ownedRoots(appRoot)", sweep)
        self.assertNotIn("?: return", sweep)

    def test_caller_cannot_extend_run_archive_retention(self):
        content = self.compose("RunSessionLog")
        self.assertIn("const val KEEP_DAYS = 7", content)
        self.assertIn("keepDays.coerceIn(1, KEEP_DAYS)", content)
