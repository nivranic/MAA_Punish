"""The roguelike preset must also work when the game is initially closed."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_roguelike_preset_starts_game_before_any_mode():
    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    preset = next(
        item for item in interface["preset"] if item["name"] == "preset_roguelike"
    )
    definitions = {}
    for path in (ROOT / "tasks").glob("*.json"):
        tasks = json.loads(path.read_text(encoding="utf-8"))["task"]
        definitions.update({task["name"]: task for task in tasks})

    startup, *modes = preset["task"]
    assert startup["enabled"] is True
    assert definitions[startup["name"]]["entry"] == "启动"
    expected_modes = {
        name for name, task in definitions.items()
        if "roguelike" in task.get("group", [])
    }
    assert {item["name"] for item in modes} == expected_modes
    # Each mode is opt-in; choosing any one keeps the startup prerequisite ahead.
    assert all(item["enabled"] is False for item in modes)
