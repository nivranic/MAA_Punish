"""验证补丁安全失败、幂等与 CI 版本契约；实际 Kotlin 行为由上游 JVM 测试运行。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github" / "android"))

from patch_maafwapp import OVERLAYS, PATCHES, apply_patches, patched_text

spec = importlib.util.spec_from_file_location("mine_apk_version", ROOT / "tools/ci/mine_apk_version.py")
version_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version_module)


class PatchSafetyTest(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / "build-debug" / "android-patch-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        contents: dict[str, list[str]] = {}
        for path, old, _ in PATCHES:
            contents.setdefault(path, []).append(old)
        for path, blocks in contents.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n\n".join(blocks), encoding="utf-8")

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_apply_is_idempotent_and_installs_tracked_kotlin_tests(self):
        self.assertGreater(apply_patches(self.root), 0)
        after = self.snapshot()
        self.assertEqual(0, apply_patches(self.root))
        self.assertEqual(after, self.snapshot())
        tests = list(OVERLAYS.rglob("*Test.kt"))
        self.assertGreaterEqual(len(tests), 2)
        for source in tests:
            self.assertEqual(source.read_bytes(), (self.root / source.relative_to(OVERLAYS)).read_bytes())

    def test_late_anchor_drift_does_not_modify_earlier_files(self):
        path, old, _ = PATCHES[-1]
        target = self.root / path
        target.write_text(target.read_text(encoding="utf-8").replace(old, "upstream drift"), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "无法安全补丁"):
            apply_patches(self.root)
        self.assertEqual(before, self.snapshot())

    def test_overlay_conflict_does_not_apply_partial_patch(self):
        source = next(path for path in OVERLAYS.rglob("*.kt") if path.is_file())
        target = self.root / source.relative_to(OVERLAYS)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unexpected upstream file", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "已有不同内容"):
            apply_patches(self.root)
        self.assertEqual(before, self.snapshot())

    def test_duplicate_and_mixed_anchors_are_rejected(self):
        for _, old, new in PATCHES:
            for text in (old + old, new + new, old + new):
                with self.subTest(old=old[:60], text=text[:60]), self.assertRaises(ValueError):
                    patched_text(text, "test.kt", old, new)


class MineVersionTest(unittest.TestCase):
    def test_commit_count_is_numeric_and_rerun_is_stable(self):
        self.assertEqual("v3.12.6-iqoo.2185", version_module.mine_version(["v3.12.5"], 2185))
        self.assertEqual("v3.12.6-iqoo.2186", version_module.mine_version(["v3.12.5"], 2186))
        self.assertEqual("v3.12.6-iqoo.2185", version_module.mine_version(["v3.12.5"], 2185))

    def test_upstream_progress_and_numeric_version_sort(self):
        tags = ["v3.9.99", "v3.12.5", "v3.12.6", "mine-v99.0.0-iqoo.9999", "v4.0.0-beta.1"]
        self.assertEqual("v3.12.7-iqoo.2190", version_module.mine_version(tags, 2190))

    def test_missing_stable_baseline_and_invalid_count_fail_explicitly(self):
        for tags, count in (([], 2185), (["v3.12.6-beta.1"], 2185), (["v03.12.5"], 2185), (["v3.12.5"], 0)):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                version_module.mine_version(tags, count)

    def test_apk_metadata_must_match_package_name_version_and_commit_count(self):
        expected = "3.12.6-iqoo.2185"
        metadata = {"applicationId": "com.overflow65537.maafw.punish", "elements": [{"versionName": expected, "versionCode": 2185}]}
        version_module.check_metadata(metadata, expected, 2185)
        for invalid in (
            {**metadata, "applicationId": "other.package"},
            {**metadata, "elements": [{"versionName": "cb3acf9c", "versionCode": 2185}]},
            {**metadata, "elements": [{"versionName": expected, "versionCode": 2184}]},
            {**metadata, "elements": []},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                version_module.check_metadata(invalid, expected, 2185)

    def test_apk_badging_checks_actual_manifest_and_rejects_hash_version(self):
        expected = "3.12.6-iqoo.2185"
        good = "package: name='com.overflow65537.maafw.punish' versionCode='2185' versionName='3.12.6-iqoo.2185' platformBuildVersionName='16'"
        version_module.check_badging(good, expected, 2185)
        for invalid in ("invalid", good.replace(expected, "cb3acf9c"), good.replace("2185'", "2184'")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                version_module.check_badging(invalid, expected, 2185)

    def test_signer_output_accepts_numbered_and_sdk_range_certificates(self):
        digest = "a" * 64
        for prefix in (
            "Signer #1",
            "Signer (minSdkVersion=28, maxSdkVersion=2147483647)",
            "Signer (minSdkVersion=33 (dev release=true), maxSdkVersion=2147483647)",
            "Signer (minSdkVersion=28, maxSdkVersion=32) #1",
        ):
            output = f"{prefix} certificate SHA-256 digest: {digest.upper()}\n"
            with self.subTest(prefix=prefix):
                self.assertEqual(digest, version_module.check_signer_output(output, digest))
        repeated = (
            f"Signer (minSdkVersion=28, maxSdkVersion=32) certificate SHA-256 digest: {digest}\n"
            f"Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: {digest}\n"
            f"Signer #1 public key SHA-256 digest: {'b' * 64}\n"
            f"Source Stamp Signer certificate SHA-256 digest: {'b' * 64}\n"
        )
        self.assertEqual(digest, version_module.check_signer_output(repeated, digest))

    def test_signer_output_rejects_missing_malformed_other_or_multiple_certificates(self):
        digest = "a" * 64
        good = f"Signer #1 certificate SHA-256 digest: {digest}\n"
        for output in (
            "",
            f"Signer #1 public key SHA-256 digest: {digest}\n",
            good.replace(digest, "b" * 64),
            good + f"Signer #2 certificate SHA-256 digest: {'b' * 64}\n",
            good + "Signer #2 certificate SHA-256 digest: malformed\n",
            good + f"Signer unknown certificate SHA-256 digest: {digest}\n",
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                version_module.check_signer_output(output, digest)


if __name__ == "__main__":
    unittest.main()
