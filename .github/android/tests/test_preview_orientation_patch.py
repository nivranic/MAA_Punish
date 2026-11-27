"""全屏预览退出方向还原补丁：锚点幂等、歧义拒绝、旧行为确实被移除。"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/android"))
from maafwapp_patches import PATCHES
from patch_maafwapp import patched_text

TARGET = "app/src/main/java/com/aliothmoon/maafw/ui/tasks/TasksPreviewSection.kt"
OLD_RESTORE = "onDispose { if (original != null) activity.requestedOrientation = original }"
OVERLAY_MAIN = (
    ROOT
    / ".github/android/overlays/app/src/main/java/com/aliothmoon/maafw/ui/tasks"
    / "PreviewOrientationRestore.kt"
)
OVERLAY_TEST = (
    ROOT
    / ".github/android/overlays/app/src/test/java/com/aliothmoon/maafw/ui/tasks"
    / "PreviewOrientationRestoreTest.kt"
)


class PreviewOrientationPatchTest(unittest.TestCase):
    def patches(self):
        return [patch for patch in PATCHES if patch[0] == TARGET]

    def test_single_patch_replaces_unconditional_restore(self):
        patches = self.patches()
        self.assertEqual(1, len(patches))
        _, old, new = patches[0]
        self.assertIn(OLD_RESTORE, old)
        self.assertNotIn(OLD_RESTORE, new)
        self.assertIn("PreviewOrientationRestore.forceLandscapeOnEnter(", new)
        self.assertIn("PreviewOrientationRestore.exitSequence(", new)

    def test_patch_is_idempotent_and_ambiguous_source_rejects(self):
        for path, old, new in self.patches():
            self.assertEqual(new, patched_text(old, path, old, new))
            self.assertEqual(new, patched_text(new, path, old, new))
            for ambiguous in (old + old, new + new, old + new):
                with self.assertRaises(ValueError):
                    patched_text(ambiguous, path, old, new)

    def test_portrait_is_forced_before_direction_is_handed_back(self):
        for _, _, new in self.patches():
            body = new.index("onDispose {")
            portrait = new.index("target.requestedOrientation = restore.first", body)
            delayed_restore = new.index("postDelayed(", portrait)
            hand_back = new.index("target.requestedOrientation = delayed", delayed_restore)
            self.assertLess(portrait, delayed_restore)
            self.assertLess(delayed_restore, hand_back)

    def test_overlay_helper_and_jvm_test_are_shipped(self):
        self.assertIn("fun exitSequence(", OVERLAY_MAIN.read_text(encoding="utf-8"))
        self.assertIn("fun forceLandscapeOnEnter(", OVERLAY_MAIN.read_text(encoding="utf-8"))
        self.assertIn(
            "PreviewOrientationRestore.exitSequence(",
            OVERLAY_TEST.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
