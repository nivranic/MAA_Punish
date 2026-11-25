"""Native JumpBack routing after a dialogue appears inside a combat callback."""
from copy import deepcopy
import json
from threading import Timer

import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.combat.core.session import CombatTask
from action.combat.entry.runner import CombatRunner
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle

DIALOGUE = "跳过战斗对话"
BOSS = "BOSS战_矩阵循生"
NARRATIVE = "矩阵循生_叙事右下按钮"
ADVANCE = "矩阵循生_战斗对话推进"
READY = "矩阵循生_战斗就绪"
END = "战斗完成_矩阵循生"
SINK = "矩阵循生_身份拒绝停止"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["战斗事件_矩阵循生", BOSS, "厄运连锁_矩阵循生"])
def test_mid_fight_dialogue_returns_to_skip_and_continues_without_identity_stop(
    tmp_path, monkeypatch, language, entry,
):
    state = {"page": "combat"}
    clicks, decisions, expired = [], [], []

    class Capture(Controller):
        def click(self, x, y):
            assert state["page"] == "dialogue"
            assert 811 <= x < 911 and 550 <= y < 580
            clicks.append((x, y))
            state["page"] = "reward"
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == READY and state["page"] == "combat":
                return (1100, 600, 50, 50)
            # A partial HUD can remain visible behind dialogue, as on device.
            if argv.node_name == "战斗中" and state["page"] in {"combat", "dialogue"}:
                return (1100, 600, 50, 50)
            if state["page"] == "dialogue":
                if argv.node_name == DIALOGUE or (entry == BOSS and argv.node_name == NARRATIVE):
                    # On the archived frame both button recognitions hit the same visible skip.
                    return (811, 550, 100, 30)
                if entry == BOSS and argv.node_name == ADVANCE:
                    return (400, 600, 200, 20)
            if argv.node_name == END and state["page"] == "reward":
                return (600, 230, 100, 30)
            return None

    def observe_mid_fight_dialogue(combat):
        # Only replace the battle's timing/output. Run the actual current-task
        # checker before returning through the production native parent chain.
        assert combat.roguelike3_identity_required
        state["page"] = "dialogue"
        combat.frame = np.zeros((720, 1280, 3), np.uint8)
        reason = combat._check_combat_presence()
        decisions.append(reason)
        assert reason == "outer_interface"
        combat.out_of_combat_reason = reason

    monkeypatch.setattr(CombatTask, "combat_once", observe_mid_fight_dialogue)
    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R03DialoguePage", Page())
    assert resource.register_custom_action("CombatRunner", CombatRunner())
    task = json.loads((ROOT / "tasks/矩阵循生.json").read_bytes())["task"][0]
    patch = deepcopy(task["pipeline_override"])
    parent = resource.get_node_data(entry)
    candidates = {
        (n if isinstance(n, str) else n["name"]).removeprefix("[JumpBack]")
        for n in parent["next"]
    }
    candidates.update({READY, "战斗中", DIALOGUE, END})
    for name in candidates:
        patch.setdefault(name, {}).update(
            recognition={"type": "Custom", "param": {"custom_recognition": "R03DialoguePage"}},
            pre_delay=0, post_delay=0, rate_limit=1, timeout=1000,
        )
    patch[entry] = {"recognition": "DirectHit", "action": "DoNothing",
                    "pre_delay": 0, "post_delay": 0, "rate_limit": 1, "timeout": 1000,
                    "on_error": []}
    patch[END].update(action="StopTask", next=[], on_error=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(15, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, patch).wait()
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(node).name for node in detail.node_id_list]
        assert job.succeeded and not expired
        assert names == [entry, READY, "战斗中", DIALOGUE, END]
        assert SINK not in names and decisions == ["outer_interface"]
        assert len(clicks) == 1 and state["page"] == "reward"
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
