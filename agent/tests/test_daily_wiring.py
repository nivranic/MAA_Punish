"""日常断点续接：管线接线结构回归。

全局扫描 resource/base/pipeline 下的 日常断点_/日常完成_ 节点，
校验守卫/落账的形态与 key 声明，防止后续改动破坏接线约定。
"""

import jsonc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPE = ROOT / "resource" / "base" / "pipeline"


def _load_all_nodes() -> dict:
    nodes = {}
    for path in PIPE.rglob("*"):
        if path.suffix in (".jsonc", ".json"):
            nodes.update(jsonc.loads(path.read_text(encoding="utf-8")))
    return nodes


def test_guard_nodes_use_check_and_declare_key():
    nodes = _load_all_nodes()
    guards = {name: data for name, data in nodes.items() if name.startswith("日常断点_")}
    assert guards, "未发现任何守卫节点，接线可能被整体回退"
    for name, data in guards.items():
        reco = data.get("recognition", {})
        assert reco.get("type") == "Custom", name
        assert reco.get("param", {}).get("custom_recognition") == "DailyProgressCheck", name
        key = data.get("attach", {}).get("daily_progress_key")
        assert isinstance(key, str) and key.strip(), name
        assert data.get("next"), f"{name} 守卫节点必须承接工作链"


def test_mark_nodes_use_mark_and_declare_key():
    nodes = _load_all_nodes()
    marks = {name: data for name, data in nodes.items() if name.startswith("日常完成_")}
    for name, data in marks.items():
        action = data.get("action", {})
        assert action.get("type") == "Custom", name
        assert action.get("param", {}).get("custom_action") == "DailyProgressMark", name
        key = data.get("attach", {}).get("daily_progress_key")
        assert isinstance(key, str) and key.strip(), name


def test_wired_entries_route_through_guard():
    nodes = _load_all_nodes()
    wired = [
        name
        for name, data in nodes.items()
        if isinstance(data.get("next"), list)
        and any(str(item).startswith("日常断点_") for item in data["next"])
    ]
    assert wired, "未发现任何入口接线"
    for name in wired:
        entries = nodes[name]["next"]
        first = str(entries[0])
        assert first.startswith("日常断点_"), f"{name} 的 next 首位应为守卫节点"
        assert "空任务" in [str(item) for item in entries], f"{name} 的 next 应含 空任务 兜底"
