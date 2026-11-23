"""Exercise Cage's production sweep and layer-routing nodes without a device."""

from copy import deepcopy
from pathlib import Path
import re

import jsonc
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
CAGE = ROOT / "resource/base/pipeline/Phantom_Pain_Cage.jsonc"
NODES = jsonc.loads(CAGE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("zone", "ABC")
def test_each_room_checks_lower_layers_before_final_score_or_manual_battle(zone):
    branches = NODES[f"执行{zone}区"]["next"]
    expected = [
        f"[JumpBack]囚笼_{zone}区从骑士检查",
        "[JumpBack]囚笼_骑士完成切混沌",
        "[JumpBack]囚笼_混沌完成切地狱",
        "[JumpBack]囚笼_自动作战",
    ]
    assert branches[:4] == expected
    assert branches.index(expected[-1]) < branches.index("[JumpBack]没打满")
    assert NODES[expected[0].replace("[JumpBack]", "")]["max_hit"] == 1
    assert NODES["囚笼_自动作战"]["max_hit"] == 6
    assert NODES["囚笼_确认扫荡"]["max_hit"] == 6
    # 断点续接接线后入口工作链在守卫节点 next 下。
    entry_next = NODES["幻痛囚笼"]["next"]
    if str(entry_next[0]).startswith("日常断点_"):
        entry_next = NODES[str(entry_next[0])]["next"]
    assert entry_next.index("挑战次数不足") < entry_next.index("检查A区")


@pytest.mark.parametrize(
    ("text", "knight", "chaos", "zero"),
    [
        ("讨伐值0/93000", False, False, True),
        ("讨伐值0/186000", False, False, True),
        ("讨伐值20728/93000", True, False, False),
        ("讨伐值20000/93000", True, False, False),
        ("讨伐值21838/186000", False, True, False),
        ("讨伐值0/372000", False, False, False),
        ("讨伐值346568/372000", False, False, False),
    ],
)
def test_selected_layer_scores_exclude_hell_and_positive_numbers_ending_in_zero(text, knight, chaos, zero):
    for name, expected in [
        ("囚笼_骑士已有分数", knight),
        ("囚笼_混沌已有分数", chaos),
        ("囚笼_低层无分数", zero),
    ]:
        pattern = NODES[name]["recognition"]["param"]["expected"]
        assert bool(re.search(pattern, text)) is expected


@pytest.mark.parametrize("text,expected", [("6/6", True), ("1/6", True), ("0/6", False), ("16/6", False), ("", False)])
def test_sweep_quota_requires_a_positive_count(text, expected):
    for name in ("囚笼_扫荡可用次数", "囚笼_扫荡确认次数"):
        assert bool(re.search(NODES[name]["recognition"]["param"]["expected"], text)) is expected


@pytest.mark.parametrize("language", ["zh_TW", "ja_JP", "en_US"])
def test_sweep_labels_have_localized_overrides(language):
    localized = jsonc.loads((ROOT / "resource" / language / "pipeline/Phantom_Pain_Cage.jsonc").read_text(encoding="utf-8"))
    for name in (
        "囚笼_骑士层入口", "囚笼_混沌层入口", "囚笼_地狱层入口",
        "囚笼_自动作战文字", "囚笼_自动战斗标题", "囚笼_执行扫荡文字", "囚笼_手动作战文字",
    ):
        assert localized[name]["recognition"]["param"]["expected"]


class MemoryController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "cage-sweep-native"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), np.uint8)
    def deny(self, *args): raise AssertionError("Synthetic Cage tests must not send input")
    click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = deny


@pytest.mark.parametrize("zone", "ABC")
@pytest.mark.parametrize(
    ("history", "quota", "initial_scores", "expected_sweeps", "expected_manual"),
    [
        (True, 6, (0, 0, 0), 2, [2]),
        (False, 6, (0, 0, 0), 0, [0, 1, 2]),
        (True, 0, (0, 0, 0), 0, [0, 1, 2]),
        (True, 1, (0, 0, 0), 1, [1, 2]),
        (True, 6, (100, 0, 0), 1, [2]),
        (True, 6, (100, 100, 100), 0, []),
        (True, 6, (0, 0, 100), 2, []),
    ],
)
def test_native_room_routes_sweeps_manual_battles_and_existing_scores(
    tmp_path, zone, history, quota, initial_scores, expected_sweeps, expected_manual
):
    run_native_rooms(tmp_path, zone, history, quota, initial_scores, expected_sweeps, expected_manual)


@pytest.mark.parametrize(
    ("quota", "expected_sweeps", "expected_manual"),
    [(6, 6, [2, 2, 2]), (1, 1, [1, 2, 0, 1, 2, 0, 1, 2])],
)
def test_native_three_rooms_share_the_sweep_budget(tmp_path, quota, expected_sweeps, expected_manual):
    run_native_rooms(tmp_path, "ABC", True, quota, (0, 0, 0), expected_sweeps, expected_manual)


def run_native_rooms(tmp_path, zones, history, quota, initial_scores, expected_sweeps, expected_manual):
    state = {"page": "room", "layer": 2, "scores": list(initial_scores), "quota": quota, "room_index": 0}
    actions, manual_layers, errors = [], [], []

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            room = state["page"] == "room"
            modal = state["page"] == "modal"
            layer = state["layer"]
            matched = {
                "作战准备": room,
                "不是最终难度": room and layer < 2,
                "检查是否是最终难度": room and layer == 2,
                "检查是否存在分数": room and layer == 2 and state["scores"][2] > 0,
                "囚笼_骑士层入口": room,
                "囚笼_混沌层入口": room,
                "囚笼_地狱层入口": room,
                "囚笼_骑士已有分数": room and layer == 0 and state["scores"][0] > 0,
                "囚笼_混沌已有分数": room and layer == 1 and state["scores"][1] > 0,
                "囚笼_低层无分数": room and layer < 2 and state["scores"][layer] == 0,
                "囚笼_自动作战文字": room and layer < 2,
                "囚笼_扫荡可用次数": room and state["quota"] > 0,
                "囚笼_自动战斗标题": modal,
                "囚笼_执行扫荡文字": modal and history,
                "囚笼_扫荡历史分数": modal and history,
                "囚笼_扫荡确认次数": modal and state["quota"] > 0,
                "囚笼_手动作战文字": modal,
                "没打满": room and state["scores"][layer] == 0,
                "囚笼作战程序": state["page"] == "manual",
            }
            return (10, 10, 20, 20) if matched.get(argv.node_name, False) else None

    class Act(CustomAction):
        def run(self, context, argv):
            try:
                name = argv.node_name
                actions.append(name)
                zone = zones[state["room_index"]]
                if name == f"囚笼_{zone}区从骑士检查":
                    state["layer"] = 0
                elif name == "囚笼_骑士完成切混沌":
                    state["layer"] = 1
                elif name == "囚笼_混沌完成切地狱":
                    state["layer"] = 2
                elif name == "囚笼_自动作战":
                    state["page"] = "modal"
                elif name == "囚笼_确认扫荡":
                    assert state["layer"] < 2 and state["quota"] > 0 and history
                    state["quota"] -= 1
                    state["scores"][state["layer"]] = 100
                    state["page"] = "room"
                elif name in {"没打满", "囚笼_扫荡转手动作战"}:
                    state["page"] = "manual"
                elif name == "囚笼作战程序":
                    manual_layers.append(state["layer"])
                    state["scores"][state["layer"]] = 100
                    state["page"] = "room"
                elif name == f"{zone}区退出" and state["room_index"] < len(zones) - 1:
                    assert all(state["scores"])
                    state["room_index"] += 1
                    state["scores"] = list(initial_scores)
                    state["layer"] = 2
                return True
            except Exception as error:
                errors.append(repr(error))
                return False

    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), MemoryController(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert resource.register_custom_recognition("CageSweepPage", Page())
    assert resource.register_custom_action("CageSweepAct", Act())
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    override = deepcopy(NODES)
    leaves = [
        "作战准备", "不是最终难度", "检查是否是最终难度", "检查是否存在分数",
        "囚笼_骑士层入口", "囚笼_混沌层入口", "囚笼_地狱层入口",
        "囚笼_骑士已有分数", "囚笼_混沌已有分数", "囚笼_低层无分数",
        "囚笼_自动作战文字", "囚笼_扫荡可用次数", "囚笼_自动战斗标题",
        "囚笼_执行扫荡文字", "囚笼_扫荡历史分数", "囚笼_扫荡确认次数",
        "囚笼_手动作战文字", "没打满", "囚笼作战程序",
        "检查主界面2", "关闭难度说明", "战斗结束", "意外处理_进入作战界面", "挑战次数不足_弹窗",
    ]
    for name in leaves:
        override.setdefault(name, {})["recognition"] = {
            "type": "Custom", "param": {"custom_recognition": "CageSweepPage"}
        }
    for name, node in override.items():
        node.update({
            "pre_delay": 0, "post_delay": 0, "timeout": 200, "rate_limit": 0,
            "pre_wait_freezes": 0, "post_wait_freezes": 0,
        })
    for name in [
        "囚笼_骑士完成切混沌", "囚笼_混沌完成切地狱",
        "囚笼_自动作战", "囚笼_确认扫荡", "囚笼_扫荡转手动作战", "没打满", "囚笼作战程序",
    ]:
        override[name]["action"] = {"type": "Custom", "param": {"custom_action": "CageSweepAct"}}
    for index, zone in enumerate(zones):
        for name in (f"囚笼_{zone}区从骑士检查", f"{zone}区退出"):
            override[name]["action"] = {"type": "Custom", "param": {"custom_action": "CageSweepAct"}}
        override[f"执行{zone}区"]["action"] = "DoNothing"
        override[f"{zone}区退出"]["next"] = [f"执行{zones[index + 1]}区"] if index + 1 < len(zones) else []
    override["囚笼作战程序"]["next"] = []
    try:
        job = tasker.post_task(f"执行{zones[0]}区", override).wait()
        assert errors == []
        assert job.succeeded
        assert actions.count("囚笼_确认扫荡") == expected_sweeps
        assert manual_layers == expected_manual
        assert all(state["scores"])
        assert state["layer"] == 2
        names = [tasker.get_node_detail(node_id).name for node_id in job.get().node_id_list]
        assert all(f"{zone}区退出" in names for zone in zones)
        assert "任务失败优雅退出" not in names
    finally:
        stop_and_wait_idle(tasker)
