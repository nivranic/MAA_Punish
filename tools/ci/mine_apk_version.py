#!/usr/bin/env python3
"""生成 mine APK 的可比较版本；同一提交重跑版本不变。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

STABLE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


def mine_version(tags: list[str], commit_count: int) -> str:
    versions = [tuple(map(int, match.groups())) for tag in tags if (match := STABLE_TAG.fullmatch(tag))]
    if not versions:
        raise ValueError("mine APK 需要至少一个可达的正式 vX.Y.Z 标签，不能猜测版本或回落到 0.0.0")
    if commit_count <= 0:
        raise ValueError("Git 提交数必须是正整数")
    major, minor, patch = max(versions)
    return f"v{major}.{minor}.{patch + 1}-iqoo.{commit_count}"


def version_for_repo(root: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    return mine_version(
        git("tag", "--merged", "HEAD", "--list", "v*").splitlines(),
        int(git("rev-list", "--count", "HEAD")),
    )


def check_metadata(metadata: dict, expected_version: str, expected_code: int) -> None:
    if metadata.get("applicationId") != "com.overflow65537.maafw.punish":
        raise ValueError("APK applicationId 与更新目标不一致")
    elements = metadata.get("elements", [])
    if not elements or any(
        element.get("versionName") != expected_version or element.get("versionCode") != expected_code
        for element in elements
    ):
        raise ValueError("APK metadata 的 versionName/versionCode 与 mine 版本不一致")


def check_badging(badging: str, expected_version: str, expected_code: int) -> None:
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", badging, re.MULTILINE)
    if not package:
        raise ValueError("无法读取 APK 实物的 aapt package 元数据")
    application_id, code, version = package.groups()
    check_metadata(
        {"applicationId": application_id, "elements": [{"versionName": version, "versionCode": int(code)}]},
        expected_version,
        expected_code,
    )


def check_signer_output(output: str, expected_digest: str) -> str:
    """校验 apksigner 已验证的全部签名者；不把公钥或 Source Stamp 摘要当作包签名。"""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_digest):
        raise ValueError("期望签名证书 SHA-256 必须是 64 位十六进制")
    # ApkSignerTool.verify: v3/v3.1 names signers by SDK range; older schemes use #N.
    # https://android.googlesource.com/platform/tools/apksig/+/refs/heads/main/src/apksigner/java/com/android/apksigner/ApkSignerTool.java
    signer_line = re.compile(
        r"Signer (?:#[1-9]\d*|\(minSdkVersion=\d+(?: \(dev release=true\))?, maxSdkVersion=\d+\)(?: #[1-9]\d*)?)"
        r" certificate SHA-256 digest: ([0-9a-fA-F]{64})"
    )
    digests: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Signer ") or " certificate SHA-256 digest:" not in line:
            continue
        match = signer_line.fullmatch(line)
        if not match:
            raise ValueError(f"无法严格解析 APK 签名证书摘要: {line}")
        digests.add(match[1].lower())
    if not digests:
        raise ValueError("apksigner 未输出可校验的 APK 签名证书 SHA-256")
    if digests != {expected_digest.lower()}:
        raise ValueError(f"APK 签名证书与专用更新密钥不一致: {', '.join(sorted(digests))}")
    return next(iter(digests))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--check-metadata", type=Path)
    parser.add_argument("--check-badging", type=Path)
    parser.add_argument("--check-signer", type=Path)
    parser.add_argument("--expected-signer")
    args = parser.parse_args()
    try:
        if args.check_signer:
            if not args.expected_signer:
                parser.error("--check-signer 需要 --expected-signer")
            signer = check_signer_output(args.check_signer.read_text(encoding="utf-8"), args.expected_signer)
            print(f"Verified APK signer SHA-256: {signer}")
            return 0
        version = version_for_repo(args.repo)
        if args.check_metadata or args.check_badging:
            expected_code = int(version.rsplit(".", 1)[1])
            if args.check_metadata:
                check_metadata(json.loads(args.check_metadata.read_text(encoding="utf-8")), version.removeprefix("v"), expected_code)
            if args.check_badging:
                check_badging(args.check_badging.read_text(encoding="utf-8"), version.removeprefix("v"), expected_code)
            print(f"APK identity matches {version} ({expected_code})")
        else:
            print(version)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"mine APK version: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
