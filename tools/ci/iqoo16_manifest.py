#!/usr/bin/env python3
"""从已上传的 Release 元数据生成 Pages 更新清单；不会请求 API 或接触凭据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

REPOSITORY = "nivranic/MAA_Punish"
APPLICATION_ID = "com.overflow65537.maafw.punish.iqoo16"
LEGACY_APPLICATION_ID = "com.overflow65537.maafw.punish"
FEEDS = {
    "standalone": (APPLICATION_ID, "MPA-android-iqoo16-"),
    "legacy": (LEGACY_APPLICATION_ID, "MPA-android-"),
}
TAG = re.compile(r"mine-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-iqoo\.([1-9][0-9]*)\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def version_key(tag: str) -> tuple[int, int, int, int]:
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("不是严格的 iQOO 16 Release tag")
    key = tuple(map(int, match.groups()))
    if key[3] > 2_100_000_000:
        raise ValueError("版本码超出 Android 发布范围")
    return key


def feed_identity(feed: str) -> tuple[str, str]:
    if feed not in FEEDS:
        raise ValueError("未知的 iQOO 16 清单身份")
    return FEEDS[feed]


def validate_manifest(raw: dict, feed: str = "standalone") -> dict:
    application_id, asset_prefix = feed_identity(feed)
    if not isinstance(raw, dict):
        raise ValueError("清单必须是 JSON 对象")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("不支持的清单协议")
    if raw.get("channel") != "IQOO16" or raw.get("application_id") != application_id:
        raise ValueError("清单渠道或包名不匹配")
    tag = raw.get("release_tag")
    if not isinstance(tag, str):
        raise ValueError("缺少 Release tag")
    key = version_key(tag)
    if raw.get("version_name") != tag.removeprefix("mine-v"):
        raise ValueError("版名与 Release tag 不匹配")
    if type(raw.get("version_code")) is not int or raw["version_code"] != key[3]:
        raise ValueError("版本码与 Release tag 不匹配")
    expected_apk = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_prefix}{tag.removeprefix('mine-')}.apk"
    if raw.get("apk_url") != expected_apk:
        raise ValueError("APK 必须是本 fork 该 Release 的精确 release 签名文件")
    if raw.get("release_url") != f"https://github.com/{REPOSITORY}/releases/tag/{tag}":
        raise ValueError("Release 页面地址不匹配")
    if not isinstance(raw.get("sha256"), str) or not SHA256.fullmatch(raw["sha256"]):
        raise ValueError("缺少合法 SHA-256")
    return raw


def release_manifest(release: dict, feed: str = "standalone") -> dict:
    application_id, asset_prefix = feed_identity(feed)
    if not isinstance(release, dict):
        raise ValueError("Release 元数据必须是对象")
    if release.get("draft") is not False:
        raise ValueError("Release 尚未发布")
    tag = release.get("tag_name", "")
    if not isinstance(tag, str):
        raise ValueError("Release tag 必须是字符串")
    key = version_key(tag)
    name = f"{asset_prefix}{tag.removeprefix('mine-')}.apk"
    assets = release.get("assets", [])
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("Release 资产列表结构无效")
    # Both package identities may coexist in one Release; only this feed's exact asset is eligible.
    apks = [asset for asset in assets if asset.get("name") == name]
    if len(apks) != 1:
        raise ValueError("Release 缺少唯一的精确 release APK")
    asset = apks[0]
    digest = asset.get("digest", "")
    if asset.get("state") != "uploaded" or type(asset.get("size")) is not int or asset["size"] <= 0:
        raise ValueError("APK 尚未完整上传")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("GitHub 尚未提供 APK SHA-256")
    return validate_manifest({
        "schema_version": 1,
        "channel": "IQOO16",
        "application_id": application_id,
        "version_name": tag.removeprefix("mine-v"),
        "version_code": key[3],
        "release_tag": tag,
        "apk_url": asset.get("browser_download_url"),
        "sha256": digest.removeprefix("sha256:").lower(),
        "release_url": release.get("html_url"),
    }, feed)


def generate_manifest(
    releases: list[dict], required_tag: str | None, previous: dict | None = None, feed: str = "standalone",
) -> dict:
    feed_identity(feed)
    if required_tag is not None:
        version_key(required_tag)
    elif feed != "legacy":
        raise ValueError("独立版必须指定本次成功发布的 tag")
    eligible = []
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
            raise ValueError("Release API 响应结构无效")
        if not TAG.fullmatch(release["tag_name"]):
            continue  # Official releases and historical SHA/debug releases are outside this channel.
        try:
            eligible.append(release_manifest(release, feed))
        except ValueError:
            if release["tag_name"] == required_tag:
                raise  # The publishing run itself must have uploaded a valid APK before deploying.
    if not eligible or (required_tag is not None and not any(item["release_tag"] == required_tag for item in eligible)):
        raise ValueError("找不到本次已成功发布的完整 APK")
    tags_by_code = {}
    manifests_by_tag = {}
    for item in eligible:
        if manifests_by_tag.setdefault(item["release_tag"], item) != item:
            raise ValueError("同 tag 的 Release 响应包含相互冲突的资产元数据")
        previous_tag = tags_by_code.setdefault(item["version_code"], item["release_tag"])
        if previous_tag != item["release_tag"]:
            raise ValueError("不同 Release tag 复用了相同版本码；需要递增版本码后重新发布")
    latest = max(eligible, key=lambda item: version_key(item["release_tag"]))
    if latest["version_code"] < max(item["version_code"] for item in eligible):
        raise ValueError("SemVer 最新版本的版本码回退；需要审查分支历史")
    if previous is not None:
        validate_manifest(previous, feed)
        if version_key(latest["release_tag"]) < version_key(previous["release_tag"]) or latest["version_code"] < previous["version_code"]:
            raise ValueError("拒绝用旧 Release 覆盖现有 Pages 清单")
        if latest["release_tag"] != previous["release_tag"] and latest["version_code"] <= previous["version_code"]:
            raise ValueError("新 Release 的版本码必须严格递增")
        if latest["release_tag"] == previous["release_tag"] and latest != previous:
            raise ValueError("同一版本 APK 元数据变化；已发布资产必须保持不变")
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--releases", required=True, type=Path, help="gh api --paginate --slurp 的 JSON 输出")
    parser.add_argument("--required-tag")
    parser.add_argument("--feed", choices=tuple(FEEDS), default="standalone")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        pages = json.loads(args.releases.read_text(encoding="utf-8"))
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError("Release API 分页响应必须是数组列表")
        previous = None
        if args.previous is not None:
            previous = validate_manifest(json.loads(args.previous.read_text(encoding="utf-8")), args.feed)
        manifest = generate_manifest([release for page in pages for release in page], args.required_tag, previous, args.feed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (ValueError, OSError) as error:
        parser.exit(1, f"iQOO 16 清单生成失败: {error}\n")
    print(f"iQOO 16 manifest: {manifest['release_tag']} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
