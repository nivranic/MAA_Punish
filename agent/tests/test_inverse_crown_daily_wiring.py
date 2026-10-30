"""FG-INVERSE-TASK-WIRING daily option contract; device acceptance is separate.

The D06/D07 switches must hand a fixed scene intent to the shared 战斗中 node
without copying the 战斗辅助 override, whose next/on_error replacement would
truncate the daily settlement and return chains. Until the readiness gate is
replaced, an enabled profile still stops before input with strategy_not_ready.
"""

import json
from pathlib import Path

import jsonc


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "resource/base/pipeline"

EXPECTED_PROFILE = {
    "enabled": True,
    "scene": "single",
    "source": "unknown",
    "rank": "unknown",
    "signature_weapon_confirmed": False,
}


def _daily_task(filename, task_name, option_name, scene):
    data = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    task = data["task"][0]
    assert task["name"] == task_name
    option = data["option"][option_name]
    assert option_name in task["option"], (filename, option_name)
    assert option["type"] == "switch"
    assert option["default_case"] == "No"
    yes = next(c for c in option["cases"] if c["name"] == "Yes")
    no = next(c for c in option["cases"] if c["name"] == "No")
    yes_node = yes["pipeline_override"]["战斗中"]
    no_node = no["pipeline_override"]["战斗中"]
    # Only attach: the daily 战斗中 next/on_error must stay untouched.
    assert set(yes_node) == {"attach"} and set(no_node) == {"attach"}
    assert yes_node["attach"]["inverse_crown_profile"] == {**EXPECTED_PROFILE, "scene": scene}
    assert no_node["attach"]["inverse_crown_profile"]["enabled"] is False
    assert no_node["attach"]["inverse_crown_profile"]["scene"] == "unknown"
    return option


def test_cage_option_hands_single_scene_intent():
    _daily_task("tasks/幻痛囚笼.json", "幻痛囚笼", "逆冕站场_囚笼", "single")


def test_war_zone_option_hands_multi_scene_intent():
    _daily_task("tasks/纷争战区.json", "纷争战区", "逆冕站场_战区", "multi")


def test_daily_option_text_has_four_translations():
    keys = {f"option.逆冕站场_{name}.{field}"
            for name in ("囚笼", "战区") for field in ("label", "description")}
    for language in ("zh_cn", "zh_tw", "en_us", "ja_jp"):
        translations = json.loads((ROOT / f"i18n/{language}.json").read_text(encoding="utf-8"))
        assert keys <= set(translations), (language, keys - set(translations))
        assert all(isinstance(translations[key], str) and translations[key] for key in keys)


def test_wiring_target_is_the_shared_combat_runner_node():
    # The switches attach to the same 战斗中 node that fires CombatRunner;
    # renaming or re-targeting that node silently breaks the daily intent.
    auto_battle = jsonc.loads(
        (PIPELINE / "Auto_Battle/Auto_Battle.jsonc").read_text(encoding="utf-8")
    )
    node = auto_battle["战斗中"]
    assert node["action"]["param"]["custom_action"] == "CombatRunner"
    cage = json.loads((ROOT / "tasks/幻痛囚笼.json").read_text(encoding="utf-8"))
    war_zone = json.loads((ROOT / "tasks/纷争战区.json").read_text(encoding="utf-8"))
    for data in (cage, war_zone):
        assert "战斗中" in data["option"][
            next(k for k in data["option"] if k.startswith("逆冕站场_"))
        ]["cases"][0]["pipeline_override"]
