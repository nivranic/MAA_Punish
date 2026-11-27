"""Freeze the daily admission, option and language/resource assembly contract."""

from pathlib import Path
import json
import re

import jsonc
import pytest

from action.basics.daily_progress import ADMIT_NODE, GUARD_POINTER, REJECT_NODE

ROOT = Path(__file__).resolve().parents[2]
PIPE = ROOT / "resource" / "base" / "pipeline"


def _load_nodes(pipe):
    nodes = {}
    for path in pipe.rglob("*"):
        if path.suffix in (".jsonc", ".json"):
            nodes.update(jsonc.loads(path.read_text(encoding="utf-8")))
    return nodes


def _admitted_roots(nodes):
    return {name: body["attach"][GUARD_POINTER] for name, body in nodes.items()
            if isinstance(body.get("attach"), dict) and GUARD_POINTER in body["attach"]}


def _interface_sources():
    """Read root and imported interface/task/option fragments."""
    pending, seen = [ROOT / "interface.json"], set()
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        data = jsonc.loads(path.read_text(encoding="utf-8"))
        yield path.relative_to(ROOT).as_posix(), data
        pending.extend(path.parent / name for name in data.get("import", []))


def _pipeline_overrides(value, where):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "pipeline_override":
                yield where, item
            else:
                yield from _pipeline_overrides(item, f"{where}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            label = item.get("name", index) if isinstance(item, dict) else index
            yield from _pipeline_overrides(item, f"{where}/{label}")


def test_guard_and_mark_nodes_resolve_the_same_declared_keys():
    nodes = _load_nodes(PIPE)
    guards = {name: data for name, data in nodes.items() if name.startswith("日常断点_")}
    marks = {name: data for name, data in nodes.items() if name.startswith("日常完成_")}
    assert len(guards) == 17 and len(marks) == 14
    keys = set()
    for name, data in guards.items():
        reco = data["recognition"]
        assert reco["type"] == "Custom" and reco["param"]["custom_recognition"] == "DailyProgressCheck", name
        key = data["attach"]["daily_progress_key"]
        assert isinstance(key, str) and key.strip() and data["next"], name
        keys.add(key)
    for name, data in marks.items():
        action = data["action"]
        assert action["type"] == "Custom" and action["param"]["custom_action"] == "DailyProgressMark", name
        assert data["attach"]["daily_progress_key"] in keys and "on_error" not in data, name


@pytest.mark.parametrize("overlay", ["base", "zh_TW", "ja_JP", "en_US", "pgr_win32", "playcover"])
def test_task_roots_do_not_bypass_admission_or_use_a_catchall_skip(overlay):
    nodes = _load_nodes(PIPE)
    admitted = _admitted_roots(nodes)
    sources = dict(_interface_sources())
    entries = {task["name"]: task["entry"] for data in sources.values() for task in data.get("task", [])}
    daily = next(preset["task"] for preset in sources["interface.json"]["preset"]
                 if preset["name"] == "preset_daily")
    expected = {entries[task["name"]] for task in daily} - {"启动", "关闭"}
    assert set(admitted) == expected and len(admitted) == 16
    if overlay != "base":
        patches = _load_nodes(ROOT / "resource" / overlay / "pipeline")
        for name, patch in patches.items():
            nodes.setdefault(name, {}).update(patch)
    for name in (ADMIT_NODE, REJECT_NODE, "重置日常断点"):
        gate = nodes[name]
        reset = name == "重置日常断点"
        callback = "DailyProgressReset" if reset else "DailyProgressAdmit"
        assert gate["recognition"]["type"] == "DirectHit" and gate.get("enabled", True), (overlay, name)
        assert gate["action"] == {"type": "Custom", "param": {"custom_action": callback}}, (overlay, name)
        assert gate["next"] == (["空任务"] if reset else []) and gate["on_error"] == [], (overlay, name)
    skip = nodes["空任务"]
    assert skip.get("action", {"type": "DoNothing"}) == {"type": "DoNothing"} and not skip.get("next"), overlay
    assert skip.get("recognition", {"type": "DirectHit"}) == {"type": "DirectHit"} and skip.get("enabled", True), overlay
    for entry, guard in admitted.items():
        root, work = nodes[entry], nodes[guard]
        assert root["next"] == [ADMIT_NODE] and root["on_error"] == [], (overlay, entry)
        assert root.get("action", {"type": "DoNothing"}) == {"type": "DoNothing"}, (overlay, entry)
        assert root["attach"][GUARD_POINTER] == guard and root.get("enabled", True), (overlay, entry)
        assert work["recognition"]["type"] == "Custom", (overlay, guard)
        assert work["recognition"]["param"]["custom_recognition"] == "DailyProgressCheck", (overlay, guard)
        assert work.get("action", {"type": "DoNothing"}) == {"type": "DoNothing"}, (overlay, guard)
        assert work.get("enabled", True), (overlay, guard)


def test_external_task_and_option_overrides_cannot_bypass_admission():
    nodes = _load_nodes(PIPE)
    roots = _admitted_roots(nodes)
    guards = {name for name in nodes if name.startswith("日常断点_")}
    marks = {name for name in nodes if name.startswith("日常完成_")}
    for path, source in _interface_sources():
        for where, patch in _pipeline_overrides(source, path):
            assert {ADMIT_NODE, REJECT_NODE, "重置日常断点", "空任务"}.isdisjoint(patch), where
            for name in roots.keys() & patch.keys():
                assert (path, name) == ("tasks/购买物品.json", "购买物品"), where
                assert patch[name] == {"attach": {GUARD_POINTER: "日常断点_购买物品_宿舍"}}, where
            for name in (guards | marks) & patch.keys():
                # Options can choose work routes, but cannot replace the
                # callbacks or introduce alternate success/error ownership.
                allowed = {"next"} if name in guards else set()
                if path == "tasks/购买物品.json" and name in {"日常断点_购买物品_宿舍", "日常完成_购买物品_宿舍"}:
                    allowed.add("attach")
                assert set(patch[name]) <= allowed, (where, name)
                assert {"空任务", ADMIT_NODE}.isdisjoint(patch[name].get("next", [])), (where, name)


def test_purchase_root_pointer_and_mark_agree_in_every_case():
    nodes = _load_nodes(PIPE)
    buys = json.loads((ROOT / "tasks/购买物品.json").read_text(encoding="utf-8"))
    cases = buys["option"]["选择物品"]["cases"]
    assert len(cases) == 3
    for index, case in enumerate(cases):
        patch = case.get("pipeline_override", {})
        key = "购买物品/" + case["name"]
        root = patch.get("购买物品", nodes["购买物品"])
        suffix = "逆元碎片" if index == 0 else "宿舍"
        assert root["attach"][GUARD_POINTER] == f"日常断点_购买物品_{suffix}"
        for name in (f"日常断点_购买物品_{suffix}", f"日常完成_购买物品_{suffix}"):
            effective = patch.get(name, nodes[name])
            assert effective["attach"]["daily_progress_key"] == key


def test_norman_checkpoint_has_plain_return_as_last_resort():
    """诺曼断点最末端保留左上角「返回」兜底。

    2026-11-26 13:08 的失败段停在深巢作战区域页（南部/库尼/层断 三个矿区都已 3/3、只有左上角
    「返回」），`返回_诺曼复兴战` 只看右下角、其它候选也都不匹配，链路因此 28 秒后走任务失败。
    最后一条 `[JumpBack]返回` 就是这种页面状态下的唯一出口。
    """
    nodes = _load_nodes(PIPE)
    nxt = nodes["日常断点_诺曼复兴战"]["next"]
    assert nxt[-1] == "[JumpBack]返回"
    assert nodes["返回"]["recognition"]["param"]["all_of"] == ["返回_文字"]
    assert nodes["返回"]["action"]["param"]["custom_action"] == "ClickAnchor"
    text = nodes["返回_文字"]["recognition"]["param"]
    assert "返回" in text["expected"]
    left, top, width, height = text["roi"]
    assert left + width <= 300 and top + height <= 110, "兜底返回必须落在左上角返回键上"
    missing = [ref for ref in (re.sub(r"^\[[^\]]*\]", "", item) for item in nxt) if ref not in nodes]
    assert not missing, f"诺曼断点存在悬空引用: {missing}"
