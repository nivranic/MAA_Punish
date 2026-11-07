"""Real owned Oblivion/Flow switch HUDs; native identity checks deny all input."""
import json
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import jsonc
import pytest
from maa.custom_action import CustomAction

from action.combat.core.provider import CombatCheck, Roguelike3IdentityError
from action.combat.core.role_detect import detect_current_role, is_switch_arrived
from action.combat.core.role_factory import create_role
from action.combat.core.session import CombatTask
from action.combat.core.switch import detect_visible_team_colors
from action.combat.core.team import TeamSnapshot, publish_team_roster
from test_r03_recruitment_native import native_hub
from test_r03_recruitment_budget_native import native_probe


ROOT = Path(__file__).resolve().parents[2]
ROSTER = {"R": "Oblivion", "B": "GeneralFight", "Y": ""}


def test_real_owned_pair_confirms_entry_and_both_field_roles_without_input(tmp_path):
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations = []
        callback_errors = []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    return self.observe(context, argv)
                except Exception as exc:
                    callback_errors.append(exc)
                    return False

            def observe(self, context, argv):
                publish_team_roster(context, ROSTER)
                item = CombatTask(context, CombatCheck(), roguelike3_identity_required=True)
                item.team = TeamSnapshot.from_dict({**ROSTER, "current": "R"})
                item.roles = {color: create_role(item, color, cls) for color, cls in ROSTER.items() if cls}
                for name, color, expected_hp, expected_qte in (
                    ("oblivion", "R", (8035, 9832), ["B"]),
                    ("flow_transition", "B", (602, 602), ["R"]),
                    ("flow", "B", (602, 602), ["R"]),
                ):
                    image = cv2.imread(str(EVIDENCE_DIR / f"r03_multi_{name}_hud.png"))
                    assert image is not None and image.shape == frame.shape
                    frame[:] = image
                    item.frame = frame
                    item.team.current = color
                    hp = item.combat_check.read_hp_from_frame(context, frame)
                    assert (hp.current, hp.maximum) == expected_hp and hp.alive
                    assert detect_visible_team_colors(context, frame) == expected_qte
                    if color == "R":
                        entry = item.combat_check.detect_team(context, item)
                        assert (entry.R, entry.B, entry.Y, entry.current) == ("Oblivion", "GeneralFight", "", "R")
                    role = item.roles[color]
                    if name == "flow_transition":
                        assert detect_current_role(context, frame, on_tick=None)[0] == "未知"
                        assert item.refresh_field_role(role) is True
                        assert item.r3_identity_grace_until is not None
                    else:
                        assert detect_current_role(context, frame, prefer_cls=[role.cls_name], on_tick=None)[1] == role.cls_name
                        assert is_switch_arrived(context, frame, role.cls_name)
                        assert item.refresh_field_role(role) is False
                        assert item.field_identity == "confirmed" and item.r3_identity_grace_until is None
                    observations.append(name)
                # A positive generic support frame cannot authorize the dedicated attacker strategy.
                with pytest.raises(Roguelike3IdentityError, match="r3_strategy_identity_mismatch"):
                    item.refresh_field_role(item.roles["R"])
                assert not is_switch_arrived(context, frame, "Oblivion")
                return True

        assert resource.register_custom_action("R03MultiHudProbe", Probe())
        job = tasker.post_task("R03MultiHudProbe", {
            "R03MultiHudProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "R03MultiHudProbe"}},
                "next": [], "on_error": [],
            },
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not callback_errors, repr(callback_errors)
        assert job.succeeded and not expired and not inputs
        assert observations == ["oblivion", "flow_transition", "flow"]


def test_all_r03_selection_entries_enable_confirmed_color_roster():
    task = json.loads((ROOT / "tasks/矩阵循生.json").read_bytes())["task"][0]
    for name in ("选择角色程序", "选择角色程序_onlyNode", "选择角色程序_矩阵循生"):
        assert task["pipeline_override"][name]["attach"]["need_multi"] is True


def test_root_recovers_actual_recruitment_page_before_navigation_fallbacks(tmp_path):
    pipeline = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_3/Roguelike_3.jsonc").read_text(encoding="utf-8"))
    # The independent layout-cache Custom is not a game-page recognizer.
    names = [name.removeprefix("[JumpBack]") for name in pipeline["矩阵循生"]["next"]
             if name != "[JumpBack]覆盖布局菜单"]
    observations = []

    def probe(context, frames):
        for key in ("owned", "flow_owned"):
            result = context.run_recognition("Or", frames[key], {
                "Or": {"recognition": {"type": "Or", "param": {"any_of": names}}},
            })
            observations.append((result.hit if result else None, [
                row["name"] for row in result.raw_detail if row["box"] is not None
            ] if result else []))
        guard = context.run_recognition("矩阵循生_继续招募", frames["formation_two"])
        observations.append(guard.hit if guard else None)

    assert native_probe(tmp_path, probe) == []
    assert observations == [(True, ["矩阵循生_继续招募"])] * 2 + [False]
