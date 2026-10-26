"""The generated combat map is a finite source exception, not agent JSON access."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import android_debug as debug
from ci.check_android_pi_profile import glob_to_regex, parse_profile_lists


MAPPING_PATH = "agent/action/basics/InterfaceZone/combat_roi_zone_map.json"


class SourceMapPolicyTest(unittest.TestCase):
    def setUp(self):
        parent = debug.REPO_ROOT / "build-debug" / "pc-map-policy-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = debug.SourcePolicy()
        self.write("interface.json")
        self.write(MAPPING_PATH)

    def write(self, relative, data=b"{}"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    @staticmethod
    def entry(relative):
        return {"path": relative, "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}

    def test_exact_generated_map_is_collected_and_hashed_once(self):
        with patch.object(debug, "sha256_file", wraps=debug.sha256_file) as digest:
            files = self.policy.collect(self.root)
        self.assertEqual([MAPPING_PATH, "interface.json"], [item["path"] for item in files])
        self.assertEqual(1, sum(call.args[0] == self.root / MAPPING_PATH for call in digest.call_args_list))
        self.assertEqual(1, self.policy.spec["version"])
        self.assertEqual([".py"], self.policy.spec["roots"]["agent"])

    def test_neighbor_json_runtime_cache_and_user_configuration_remain_protected(self):
        protected = ["agent/action/basics/InterfaceZone/other.json",
                     "agent/action/basics/InterfaceZone/combat_roi_zone_map.JSON",
                     "agent/action/combat/config/profile.json",
                     "agent/config/user.json", "agent/debug/adb_zone_offset.json",
                     "debug/adb_zone_offset.json", "config/user.json"]
        for relative in protected:
            with self.subTest(path=relative):
                self.write(relative)
                self.assertFalse(self.policy.allowed(relative))
                with self.assertRaises(debug.DebugError):
                    self.policy.entries([self.entry(relative)])
        self.assertEqual([MAPPING_PATH, "interface.json"],
                         [item["path"] for item in self.policy.collect(self.root)])

    def test_external_duplicate_manifest_and_case_collisions_still_fail(self):
        for values in ([self.entry(MAPPING_PATH), self.entry(MAPPING_PATH)],
                       [self.entry("agent/A.py"), self.entry("agent/a.py")]):
            with self.subTest(values=values), self.assertRaisesRegex(debug.DebugError, "collision or duplicate"):
                self.policy.entries(values)

    def test_generated_map_and_all_collected_source_have_real_pi_glob_coverage(self):
        include, exclude = parse_profile_lists((debug.REPO_ROOT / ".github/android/pi-profile.yaml").read_text(encoding="utf-8"))
        includes = [glob_to_regex(pattern) for pattern in include]
        excludes = [glob_to_regex(pattern) for pattern in exclude]
        files = self.policy.collect(debug.REPO_ROOT)
        self.assertIn(MAPPING_PATH, [item["path"] for item in files])
        for item in files:
            path = item["path"]
            with self.subTest(path=path):
                self.assertTrue(any(pattern.fullmatch(path) for pattern in includes))
                self.assertFalse(any(pattern.fullmatch(path) for pattern in excludes))

    def test_old_baseline_can_add_map_without_deleting_other_source(self):
        old = [self.entry("interface.json")]
        baseline = {"revision": debug.source_revision(old), "files": old}
        files = self.policy.collect(self.root)
        manifest = debug.build_manifest(self.policy, "example.test", 2208, "a" * 32, "b" * 32, files, baseline)
        self.assertEqual(baseline["revision"], manifest["base_revision"])
        self.assertEqual([MAPPING_PATH], [item["path"] for item in manifest["files"]])
        self.assertFalse(manifest["files"][0]["delete"])
        self.assertEqual(files, manifest["managed_files"])


if __name__ == "__main__":
    unittest.main()
