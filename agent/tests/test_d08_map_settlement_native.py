"""D08 map settlement uses real Maa history; frames are drawn in memory."""

import json

import cv2
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.tasker import Tasker

from action.basics.combat_start_failure import CombatStartFailure
from recognition.exclusives.D08MapSettlement import D08MapSettlement
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.daily_progress import register_daily_progress
from test_support.native_lifecycle import stop_and_wait_idle


MAP = "开始挑战"
HUD = "战斗中"
SETTLEMENT = "结算"
GATE = "历战映射_地图本轮结算准入"
TERMINATE = "作战启动_终止"
FAILED = [TERMINATE, "任务失败优雅退出", "停止任务"]


def draw_text(frame, text, x, y):
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                1.3, (255, 255, 255), 2, cv2.LINE_AA)


def draw_hud(frame):
    data = (ROOT / "resource/base/image/战斗/战斗中.png").read_bytes()
    template = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert template is not None
    h, w = template.shape[:2]
    assert h <= 85 and w <= 175
    frame[10:10 + h, 1100:1100 + w] = template


@pytest.mark.parametrize("scene", [
    "zero_clock", "nonzero_clock", "currency", "real_hud", "wrong_callback", "vanished", "second_start",
])
def test_d08_map_settles_only_with_current_native_hud(tmp_path, monkeypatch, scene):
    blank = np.zeros((720, 1280, 3), np.uint8)
    start, stale, battle, next_map, exit_page = (blank.copy() for _ in range(5))
    draw_text(start, "START", 1001, 658)
    next_map[:] = start
    for frame in (next_map, exit_page):
        draw_text(frame, "DONE", 72, 145)
    if scene != "currency":
        draw_text(stale, "DONE", 1041, 157)
        draw_text(stale, "00:00:00" if scene == "zero_clock" else "00:01:34", 824, 138)
    else:
        draw_text(stale, "0", 756, 633)
    draw_hud(battle)
    frame = start
    clicks = []
    had_hud = scene in {"real_hud", "wrong_callback", "vanished", "second_start"}

    class MemoryController(Controller):
        def screencap(self):
            return frame

        def click(self, x, y):
            nonlocal frame
            clicks.append((x, y))
            if len(clicks) == 1:
                frame = battle if had_hud else stale
            elif len(clicks) == 2:
                frame = next_map if scene == "second_start" else exit_page
            else:
                frame = stale if len(clicks) == 3 and scene == "second_start" else blank
            return True

    class HudHandoff(CustomAction):
        def run(self, context, argv):
            nonlocal frame
            assert frame is battle and argv.node_name == HUD
            frame = stale
            return True  # No gameplay; record a completed real TemplateMatch + Custom handoff.

    class ChangeFrameAfterGrant(D08MapSettlement):
        def analyze(self, context, argv):
            nonlocal frame
            result = super().analyze(context, argv)
            if result is not None:
                frame = blank
            return result

    gate = ChangeFrameAfterGrant() if scene == "vanished" else D08MapSettlement()
    resource, controller, tasker = load_resource(tmp_path), MemoryController(), Tasker()
    ledger = tmp_path / "ledger.json"
    register_daily_progress(resource, monkeypatch, ledger)
    owner = "TestWrongCombat" if scene == "wrong_callback" else "CombatRunner"
    assert resource.register_custom_action(owner, HudHandoff())
    assert resource.register_custom_action("CombatStartFailure", CombatStartFailure())
    assert resource.register_custom_recognition("D08MapSettlement", gate)
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    config = json.loads((ROOT / "tasks/历战映射.json").read_text(encoding="utf-8"))
    task = config["task"][0]
    patch = task["pipeline_override"]
    patch[MAP] = {"recognition": {"param": {"expected": "^START$", "only_rec": True}},
                  "max_hit": int(config["option"]["刷取次数_历战映射"]["inputs"][0]["default"])}
    patch[SETTLEMENT] = {"recognition": {"param": {"expected": "^DONE$", "only_rec": True}}}
    patch["历战映射主界面"] = {"recognition": {"param": {
        "expected": "^DONE$", "roi": [70, 109, 159, 54], "only_rec": True,
    }}}
    if scene == "wrong_callback":
        patch[HUD]["action"] = {"type": "Custom", "param": {"custom_action": owner}}
    for name in (task["entry"], "日常断点_历战映射", MAP, HUD, SETTLEMENT, "历战映射主界面", *FAILED):
        patch[name] = {**patch.get(name, {}), "pre_delay": 0, "post_delay": 0,
                       "post_wait_freezes": 0, "rate_limit": 10, "timeout": 850}
    patch[GATE] = {"timeout": 850}
    try:
        job = tasker.post_task(task["entry"], patch).wait()
        nodes = [tasker.get_node_detail(i) for i in job.get().node_id_list]
    finally:
        stop_and_wait_idle(tasker)

    names = [n.name for n in nodes]
    assert len(clicks) == {"real_hud": 2, "second_start": 3}.get(scene, 1), (clicks, names)
    assert 976 <= clicks[0][0] < 1232 and 564 <= clicks[0][1] < 707
    assert not ledger.exists()
    # 2026-11-26 16:32 全量回归里 nonzero_clock 场景偶发过一次 job.succeeded 为假，当时的断言消息
    # 只有被截断的节点名；把每个节点的完成态、识别算法与动作一并留下，下次复现才有可定位的现场。
    evidence = [(n.name, n.completed,
                 n.recognition.algorithm if n.recognition else None,
                 n.action.action if n.action else None,
                 n.action.success if n.action else None) for n in nodes]
    assert job.succeeded and names[-3:] == FAILED, (job.status, evidence)
    assert names.count(HUD) == int(had_hud)
    for n in nodes:
        if n.name == MAP:
            assert n.completed and n.recognition.algorithm == "OCR" and n.action.success
        if n.name == HUD:
            assert n.completed and n.recognition.algorithm == "TemplateMatch"
            assert n.action.action == "Custom" and n.action.success
    if scene in {"real_hud", "second_start"}:
        assert names[3:8] == [MAP, HUD, GATE, SETTLEMENT, "历战映射主界面"], names
        assert nodes[names.index(GATE)].action.action == "DoNothing"
        assert 1024 <= clicks[1][0] < 1183 and 79 <= clicks[1][1] < 217
        # The memory scene never reaches the score tail. In the second-map case
        # a new Start happened, but the previous HUD cannot grant another tap.
        assert names.count(MAP) == (2 if scene == "second_start" else 1)
        assert names.count(SETTLEMENT) == 1
    else:
        assert names.count(MAP) == 1 and SETTLEMENT not in names
        assert (GATE in names) is (scene == "vanished"), names
