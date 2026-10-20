#!/usr/bin/env python3
"""对固定 MaaFwApp 版本应用包名与 iQOO 16 更新渠道补丁。

全部目标预检后再写入；锚点漂移时失败，重复执行允许相同的已应用状态。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from maafwapp_patches import PATCHES

OVERLAYS = Path(__file__).resolve().with_name("overlays")


def patched_text(content: str, path: str, old: str, new: str) -> str:
    old_count = content.count(old)
    new_count = content.count(new)
    if new_count == 1 and old not in content.replace(new, "", 1):
        return content
    if old_count == 1 and new_count == 0:
        return content.replace(old, new, 1)
    raise ValueError(
        f"无法安全补丁 {path}: 旧锚点 {old_count} 次，新内容 {new_count} 次。"
        "请核查固定 MaaFwApp 版本及上游实现是否变化。"
    )


def apply_patches(root: Path) -> int:
    root = root.resolve()
    pending: dict[Path, str] = {}
    for path, old, new in PATCHES:
        target = root / path
        if not target.is_file():
            raise ValueError(f"MaaFwApp 补丁目标不存在: {target}")
        content = pending.get(target)
        if content is None:
            content = target.read_text(encoding="utf-8")
        pending[target] = patched_text(content, path, old, new)

    for source in sorted(OVERLAYS.rglob("*")):
        if not source.is_file():
            continue
        target = root / source.relative_to(OVERLAYS)
        content = source.read_text(encoding="utf-8")
        if target.exists() and target.read_text(encoding="utf-8") != content:
            raise ValueError(f"MaaFwApp 覆盖目标已有不同内容: {target}")
        pending[target] = content

    changed = 0
    for target, content in pending.items():
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("maafwapp", type=Path, help="固定版本 MaaFwApp 仓库根目录")
    args = parser.parse_args()

    try:
        changed = apply_patches(args.maafwapp)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"Android package / iQOO 16 update channel patches: {changed} file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
