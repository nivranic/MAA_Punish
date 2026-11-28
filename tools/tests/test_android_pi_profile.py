"""Android 打包配方不得把开发机上的本地状态打进 APK（2026-11-27 的正式包里带过 .mimosa 等残留）。"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci.check_android_pi_profile import glob_to_regex, packaged_by, parse_profile_lists, residue_errors


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / ".github/android/pi-profile.yaml"


def packaged_by_real_profile(include: list[str], exclude: list[str], relative: str) -> bool:
    return packaged_by([glob_to_regex(pattern) for pattern in include],
                       [glob_to_regex(pattern) for pattern in exclude], relative)


class RealProfileTest(unittest.TestCase):
    def setUp(self):
        self.include, self.exclude = parse_profile_lists(PROFILE.read_text(encoding="utf-8"))

    def packaged(self, relative: str) -> bool:
        return packaged_by_real_profile(self.include, self.exclude, relative)

    def test_local_state_and_test_trees_are_excluded(self):
        residue = (
            "agent/.mimosa/hook-state/sess.json",
            "agent/.mimosa/hook-state/sess.baseline/abc.source",
            "agent/pytest-cache/v/cache/nodeids",
            "agent/build-debug/pc-map-policy-tests/case/x.json",
            "agent/.pytest_cache/x",
            "agent/debug/x.log",
            "agent/tests/x.py",
            "agent/action/__pycache__/x.pyc",
            "agent/pytest.ini",
        )
        for relative in residue:
            with self.subTest(relative=relative):
                self.assertFalse(self.packaged(relative))

    def test_runtime_sources_are_still_packaged(self):
        sources = ("agent/main.py", "agent/agent_file.py", "agent/action/basics/RoleSelection.py", "agent/sink/x.py")
        for relative in sources:
            with self.subTest(relative=relative):
                self.assertTrue(self.packaged(relative))

    def test_this_workspace_has_no_packaged_residue(self):
        self.assertEqual(residue_errors(REPO_ROOT / "agent", self.packaged), [])


class ResidueGuardTest(unittest.TestCase):
    def make_agent_dir(self, names: list[str]):
        """名字以 `/` 结尾的建目录，其余建文件；返回 agent 目录。"""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "agent"
        root.mkdir()
        for name in names:
            if name.endswith("/"):
                (root / name).mkdir()
            else:
                (root / name).write_text("x", encoding="utf-8")
        return root

    def test_dot_entries_and_cache_dirs_are_reported_when_packaged(self):
        root = self.make_agent_dir([".mimosa/", "pytest-cache/", "build-debug/", ".coverage"])
        self.assertEqual(residue_errors(root, lambda relative: True), [
            "开发残留会被打进 APK：agent/.coverage（补进配方 exclude）",
            "开发残留会被打进 APK：agent/.mimosa/（补进配方 exclude）",
            "开发残留会被打进 APK：agent/build-debug/（补进配方 exclude）",
            "开发残留会被打进 APK：agent/pytest-cache/（补进配方 exclude）",
        ])

    def test_excluded_residue_and_ordinary_entries_are_quiet(self):
        root = self.make_agent_dir([".mimosa/", "pytest-cache/", "action/", "tests/", "__pycache__/", "main.py"])
        probes = []

        def packaged(relative: str) -> bool:
            probes.append(relative)
            return relative == "agent/action/probe"

        self.assertEqual(residue_errors(root, packaged), [])
        self.assertEqual(probes, ["agent/.mimosa/probe", "agent/pytest-cache/probe"], "只探残留目录，不探普通目录")

    def test_decision_follows_the_profile_not_the_directory_name(self):
        root = self.make_agent_dir([".mimosa/"])
        quiet = residue_errors(root, lambda relative: relative != "agent/.mimosa/probe")
        loud = residue_errors(root, lambda relative: True)
        self.assertEqual(quiet, [])
        self.assertEqual(loud, ["开发残留会被打进 APK：agent/.mimosa/（补进配方 exclude）"])

    def test_missing_agent_dir_is_quiet(self):
        self.assertEqual(residue_errors(Path("no-such-dir") / "agent", lambda relative: True), [])


if __name__ == "__main__":
    unittest.main()
