"""静态清单协议、已发布资产与并发晚到部署的回归。"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("iqoo16_manifest", ROOT / "tools/ci/iqoo16_manifest.py")
manifest_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest_module)


def release(version="3.12.6-iqoo.2190", digest="a" * 64, feed="standalone"):
    tag = "mine-v" + version
    prefix = "MPA-android-iqoo16-" if feed == "standalone" else "MPA-android-"
    name = f"{prefix}v{version}.apk"
    return {
        "tag_name": tag, "draft": False, "prerelease": True,
        "html_url": f"https://github.com/nivranic/MAA_Punish/releases/tag/{tag}",
        "assets": [{
            "name": name, "state": "uploaded", "size": 243701481,
            "browser_download_url": f"https://github.com/nivranic/MAA_Punish/releases/download/{tag}/{name}",
            "digest": "sha256:" + digest,
        }],
    }


class Iqoo16ManifestTest(unittest.TestCase):
    def test_published_release_generates_exact_schema_and_required_tag(self):
        candidate = release()
        manifest = manifest_module.generate_manifest([candidate], candidate["tag_name"])
        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("IQOO16", manifest["channel"])
        self.assertEqual("com.overflow65537.maafw.punish.iqoo16", manifest["application_id"])
        self.assertEqual("3.12.6-iqoo.2190", manifest["version_name"])
        self.assertEqual(2190, manifest["version_code"])
        self.assertEqual("a" * 64, manifest["sha256"])
        self.assertEqual(candidate["assets"][0]["browser_download_url"], manifest["apk_url"])
        manifest_module.validate_manifest(manifest)

    def test_old_run_selects_latest_semver_instead_of_its_own_release(self):
        old = release("3.12.6-iqoo.2190")
        new = release("3.12.6-iqoo.2191")
        core = release("3.12.7-iqoo.2192")
        for candidates in ([old, new, core], [core, old, new]):
            latest = manifest_module.generate_manifest(candidates, old["tag_name"])
            self.assertEqual(core["tag_name"], latest["release_tag"])

    def test_historical_and_official_tags_are_excluded(self):
        valid = release()
        unrelated = [dict(release(), tag_name=tag) for tag in (
            "v999.0.0", "mine-v0.0.0-ci.261020-cb3acf9c", "mine-v03.12.6-iqoo.2191", "mine-v3.12.6-iqoo.02191",
            "mine-v3.12.6-iqoo.2٢",
        )]
        result = manifest_module.generate_manifest(unrelated + [valid], valid["tag_name"])
        self.assertEqual(valid["tag_name"], result["release_tag"])
        with self.assertRaises(ValueError):
            manifest_module.version_key("mine-v3.12.6-iqoo.2٢")

    def test_required_release_needs_one_complete_exact_signed_apk_and_digest(self):
        original = release()
        mutations = (
            lambda r: r.update(draft=True),
            lambda r: r.update(assets=[]),
            lambda r: r.update(assets=None),
            lambda r: r.update(assets=[None]),
            lambda r: r["assets"].append(copy.deepcopy(r["assets"][0])),
            lambda r: r["assets"][0].update(name="other.apk"),
            lambda r: r["assets"][0].update(name=r["assets"][0]["name"].replace(".apk", "-debug.apk")),
            lambda r: r["assets"][0].update(state="starter"),
            lambda r: r["assets"][0].update(size=0),
            lambda r: r["assets"][0].update(digest=None),
            lambda r: r["assets"][0].update(digest="sha256:missing"),
            lambda r: r["assets"][0].update(browser_download_url="https://example.com/app.apk"),
            lambda r: r.update(html_url="http://github.com/nivranic/MAA_Punish/releases"),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(original)
            mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                manifest_module.generate_manifest([candidate], original["tag_name"])
        with self.assertRaises(ValueError):
            manifest_module.generate_manifest([], original["tag_name"])

    def test_previous_manifest_prevents_rollback_and_same_version_asset_mutation(self):
        old = release()
        new = release("3.12.6-iqoo.2191")
        previous = manifest_module.release_manifest(new)
        with self.assertRaisesRegex(ValueError, "旧 Release"):
            manifest_module.generate_manifest([old], old["tag_name"], previous)
        previous = manifest_module.release_manifest(old)
        self.assertEqual(previous, manifest_module.generate_manifest([old], old["tag_name"], previous))
        changed = release(digest="b" * 64)
        with self.assertRaisesRegex(ValueError, "同一版本"):
            manifest_module.generate_manifest([changed], old["tag_name"], previous)

    def test_semver_progress_cannot_hide_android_version_code_regression(self):
        old = release("3.12.6-iqoo.2192")
        high_core_lower_code = release("3.12.7-iqoo.2191")
        with self.assertRaisesRegex(ValueError, "版本码回退"):
            manifest_module.generate_manifest([old, high_core_lower_code], old["tag_name"])

    def test_different_release_tags_cannot_reuse_one_android_version_code(self):
        old = release("3.12.6-iqoo.2190")
        same_code = release("3.12.7-iqoo.2190")
        for candidates in ([old, same_code], [same_code, old]):
            with self.subTest(candidates=candidates), self.assertRaisesRegex(ValueError, "相同版本码"):
                manifest_module.generate_manifest(candidates, same_code["tag_name"])

    def test_different_tag_requires_strictly_larger_code_than_previous_manifest(self):
        previous = manifest_module.release_manifest(release("3.12.6-iqoo.2190"))
        same_code = release("3.12.7-iqoo.2190")
        with self.assertRaisesRegex(ValueError, "严格递增"):
            manifest_module.generate_manifest([same_code], same_code["tag_name"], previous)
        advanced = release("3.12.7-iqoo.2191")
        result = manifest_module.generate_manifest([advanced], advanced["tag_name"], previous)
        self.assertEqual(2191, result["version_code"])

    def test_cli_rejects_supplied_nonobject_previous_and_preserves_existing_output(self):
        temporary_root = ROOT / "build-debug" / "android-patch-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            root = Path(directory)
            candidate = release()
            releases = root / "releases.json"
            releases.write_text(json.dumps([[candidate]]), encoding="utf-8")
            previous = root / "previous.json"
            output = root / "latest.json"
            original = (json.dumps(manifest_module.release_manifest(candidate)) + "\n").encode("utf-8")
            output.write_bytes(original)
            for invalid in (None, [], 1, "previous", True):
                previous.write_text(json.dumps(invalid), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools/ci/iqoo16_manifest.py"),
                     "--releases", str(releases), "--required-tag", candidate["tag_name"],
                     "--previous", str(previous), "--output", str(output)],
                    capture_output=True, text=True, encoding="utf-8",
                )
                with self.subTest(previous=invalid):
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("清单必须是 JSON 对象", result.stderr)
                    self.assertEqual(original, output.read_bytes())

    def test_protocol_rejects_wrong_types_versions_packages_urls_and_digest(self):
        good = manifest_module.release_manifest(release())
        for field, value in (
            ("schema_version", True), ("schema_version", "1"), ("schema_version", 2),
            ("channel", "BETA"), ("application_id", "wrong.package"),
            ("version_code", True), ("version_code", "2190"), ("version_code", 2189),
            ("version_name", "3.12.7-iqoo.2190"), ("release_tag", "mine-v3.12.6-iqoo.02190"),
            ("apk_url", good["apk_url"] + "?token=secret"), ("apk_url", good["apk_url"].replace("https:", "http:")),
            ("sha256", ""), ("sha256", "z" * 64), ("sha256", "sha256:" + "a" * 64),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                manifest_module.validate_manifest({**good, field: value})

    def test_incomplete_other_release_cannot_be_selected_but_current_must_be_valid(self):
        valid = release()
        pending = release("3.12.6-iqoo.2191")
        pending["assets"][0]["digest"] = None
        result = manifest_module.generate_manifest([pending, valid], valid["tag_name"])
        self.assertEqual(valid["tag_name"], result["release_tag"])
        with self.assertRaises(ValueError):
            manifest_module.generate_manifest([pending, valid], pending["tag_name"])

    def test_two_package_feeds_select_only_their_own_asset_and_preserve_legacy(self):
        legacy = release("3.12.6-iqoo.2190", feed="legacy")
        standalone = release("3.12.6-iqoo.2195")
        later = release("3.12.6-iqoo.2196")
        previous_legacy = manifest_module.release_manifest(legacy, "legacy")
        candidates = [later, legacy, standalone]
        retained = manifest_module.generate_manifest(candidates, None, previous_legacy, "legacy")
        selected = manifest_module.generate_manifest(candidates, standalone["tag_name"])
        self.assertEqual(previous_legacy, retained)
        self.assertEqual("com.overflow65537.maafw.punish", retained["application_id"])
        self.assertEqual("mine-v3.12.6-iqoo.2196", selected["release_tag"])
        self.assertIn("/MPA-android-iqoo16-", selected["apk_url"])
        self.assertNotIn("/MPA-android-iqoo16-", retained["apk_url"])
        for document, wrong_feed in ((retained, "standalone"), (selected, "legacy")):
            with self.subTest(feed=wrong_feed), self.assertRaisesRegex(ValueError, "包名"):
                manifest_module.validate_manifest(document, wrong_feed)
        with self.assertRaisesRegex(ValueError, "精确 release APK"):
            manifest_module.generate_manifest([legacy], legacy["tag_name"])

    def test_asset_name_and_application_id_must_both_match_each_feed(self):
        legacy = release(feed="legacy")
        standalone = release()
        for candidate, feed in ((standalone, "standalone"), (legacy, "legacy")):
            document = manifest_module.release_manifest(candidate, feed)
            other = manifest_module.release_manifest(legacy if feed == "standalone" else standalone, "legacy" if feed == "standalone" else "standalone")
            # Relabeling a document cannot make the other package's APK eligible.
            with self.assertRaisesRegex(ValueError, "精确"):
                manifest_module.validate_manifest({**document, "apk_url": other["apk_url"]}, feed)
        combined = copy.deepcopy(standalone)
        combined["assets"].extend(legacy["assets"])
        self.assertEqual(manifest_module.release_manifest(standalone), manifest_module.release_manifest(combined))
        self.assertEqual(manifest_module.release_manifest(legacy, "legacy"), manifest_module.release_manifest(combined, "legacy"))

    def test_legacy_feed_also_rejects_downgrade_mutation_and_bad_previous_types(self):
        old = release("3.12.6-iqoo.2189", feed="legacy")
        current = release(feed="legacy")
        previous = manifest_module.release_manifest(current, "legacy")
        with self.assertRaisesRegex(ValueError, "旧 Release"):
            manifest_module.generate_manifest([old], None, previous, "legacy")
        changed = release(digest="b" * 64, feed="legacy")
        with self.assertRaisesRegex(ValueError, "同一版本"):
            manifest_module.generate_manifest([changed], None, previous, "legacy")
        for invalid in (None, [], True):
            if invalid is None:
                continue  # No previous document is the supported first-deployment state.
            with self.subTest(previous=invalid), self.assertRaises(ValueError):
                manifest_module.generate_manifest([current], None, invalid, "legacy")
        with self.assertRaisesRegex(ValueError, "独立版必须"):
            manifest_module.generate_manifest([release()], None)

    def test_cli_generates_both_site_documents_from_one_release_snapshot(self):
        temporary_root = ROOT / "build-debug" / "android-patch-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            root = Path(directory)
            legacy = release(feed="legacy")
            standalone = release("3.12.6-iqoo.2195")
            releases = root / "releases.json"
            releases.write_text(json.dumps([[legacy, standalone]]), encoding="utf-8")
            for feed, endpoint in (("legacy", "iqoo16/latest.json"), ("standalone", "iqoo16/standalone/latest.json")):
                output = root / "site" / endpoint
                required = [] if feed == "legacy" else ["--required-tag", standalone["tag_name"]]
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools/ci/iqoo16_manifest.py"), "--feed", feed,
                     "--releases", str(releases), *required, "--output", str(output)],
                    capture_output=True, text=True, encoding="utf-8",
                )
                self.assertEqual(0, result.returncode, result.stderr)
                manifest_module.validate_manifest(json.loads(output.read_text(encoding="utf-8")), feed)
            self.assertEqual({"iqoo16/latest.json", "iqoo16/standalone/latest.json"}, {path.relative_to(root / "site").as_posix() for path in (root / "site").rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
