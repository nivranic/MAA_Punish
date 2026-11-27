"""Native Start routing, fresh targets, and the one-click failure boundary."""

import json
from threading import Event
from time import monotonic

import jsonc
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics import daily_progress
from action.basics.combat_start_failure import CombatStartFailure
from recognition.exclusives.D08MapSettlement import D08MapSettlement
from test_d08_map_settlement_native import draw_hud, draw_text
from test_r01_zero_refresh_native import ROOT, Controller, load_resource as load_native_resource
from test_support.daily_progress import register_daily_progress
from test_support.native_lifecycle import stop_and_wait_idle


START = "作战开始"
CLICK = "点击作战开始"
TEXT = "作战开始识别"
COLOR = "作战开始背景颜色识别"
TOAST = "作战启动_阻塞提示"
HANDOFF = "作战启动_确认交接"
POPUP = "通用处理-未下载弹窗-角色皮肤没下载-检测层"
GRACEFUL = "任务失败优雅退出"
STOP = "停止任务"
LOADING = "载入中"
HUD = "战斗中"
LEFT = (971, 638, 40, 28)
RIGHT = (1091, 642, 43, 25)


def load_resource(tmp_path, language="base"):
    resource = load_native_resource(tmp_path, language)
    assert resource.register_custom_action("CombatStartFailure", CombatStartFailure())
    return resource


def inside(point, box):
    x, y = point
    bx, by, w, h = box
    return bx <= x < bx + w and by <= y < by + h


@pytest.mark.parametrize("scenario", [
    "moved", "vanished", "stalled", "unknown", "blocking", "false_loading", "bounce",
    "preclick_loading", "preclick_blocking", "loaded", "hud", "popup", "click_failed",
    "slow_loading", "cancel",
])
def test_one_native_start_is_accepted_only_on_confirmed_transition(tmp_path, scenario):
    idle, left, right, loading, hud, popup, toast = range(7)
    frames = [np.full((720, 1280, 3), state, np.uint8) for state in range(7)]
    inputs = []
    captures = []
    tapped = Event()
    tap_time = None

    class Screen(Controller):
        def request_uuid(self):
            return "shared-combat-start"

        def screencap(self):
            if not captures:
                state = left
            elif not inputs:
                state = {"vanished": idle, "preclick_loading": loading, "preclick_blocking": toast}.get(scenario, right)
            elif scenario == "slow_loading":
                state = loading if monotonic() - tap_time >= 8.7 else right
            else:
                state = {
                    "moved": hud, "stalled": right, "unknown": idle,
                    "blocking": toast, "preclick_loading": loading, "preclick_blocking": toast,
                    "loaded": loading, "hud": hud, "popup": popup, "click_failed": idle, "cancel": right,
                    "false_loading": loading if captures[-1] == right else idle,
                    "bounce": loading if loading not in captures else right,
                }[scenario]
            captures.append(state)
            return frames[state]

        def click(self, x, y):
            nonlocal tap_time
            inputs.append((x, y))
            tap_time = monotonic()
            tapped.set()
            return scenario != "click_failed"

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            state = int(argv.image[0, 0, 0])
            name = argv.node_name
            if name == TEXT:
                return {left: LEFT, right: RIGHT, toast: RIGHT}.get(state)
            if name == COLOR:
                return (1040, 641, 120, 40) if state in (left, right, toast) else None
            hits = {TOAST: toast, LOADING: loading, HUD: hud, POPUP: popup}
            return (90, 60, 60, 20) if hits.get(name) == state else None

    resource, controller, tasker = load_resource(tmp_path), Screen(), Tasker()
    assert resource.register_custom_recognition("TestSharedStartPage", Page())
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    detection = {"type": "Custom", "param": {"custom_recognition": "TestSharedStartPage"}}
    patch = {name: {"recognition": detection} for name in (TEXT, COLOR, TOAST, LOADING, HUD, POPUP, "未选择队长")}
    for name in (START, CLICK, HANDOFF, "作战启动_进入过渡", "作战启动_终止", GRACEFUL, STOP):
        patch.setdefault(name, {}).update(pre_delay=0, post_delay=0, rate_limit=25, timeout=380)
    # The old unscoped recovery's Return would produce a detectable extra input.
    patch["返回"] = {"recognition": "DirectHit", "action": {"type": "Click", "param": {"target": [100, 20]}},
                     "max_hit": 1, "pre_delay": 0, "post_delay": 0}
    if scenario == "slow_loading":
        # Exercise the production post-click budget, not the shortened unit fixture.
        patch[CLICK].pop("timeout")
        patch[CLICK].pop("rate_limit")
    if scenario == "cancel":
        patch[CLICK].pop("timeout")
    try:
        job = tasker.post_task(START, patch)
        if scenario == "cancel":
            assert tapped.wait(4)
            assert tasker.post_stop().wait().succeeded
        job.wait()
        native_success = job.succeeded
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(i).name for i in detail.node_id_list]
    finally:
        stop_and_wait_idle(tasker)

    zero_clicks = scenario in ("vanished", "preclick_loading", "preclick_blocking")
    progressed = scenario in ("moved", "preclick_loading", "loaded", "hud", "popup", "slow_loading")
    assert len(inputs) == (0 if zero_clicks else 1), (scenario, inputs, names)
    assert all(inside(tap, RIGHT) for tap in inputs), (scenario, inputs, names)
    if progressed:
        assert native_success and HANDOFF in names and GRACEFUL not in names, names
    elif scenario == "cancel":
        # Native Stop may report the interrupted job as Succeeded. Inspect the
        # history for absence of any post-stop action, not that nominal status.
        assert names == [START, CLICK], names
    else:
        assert native_success and names[-2:] == [GRACEFUL, STOP], names
        assert HANDOFF not in names
        if scenario in ("blocking", "preclick_blocking"):
            assert TOAST in names
    assert not {LOADING, HUD, POPUP, "返回"}.intersection(names), names


@pytest.mark.parametrize("source,owner,settled,cap,language", [
    ("Simulated_Siege.jsonc", "作战界面", "保存分数", None, "base"),
    ("Simulated_Siege.jsonc", "作战界面", "保存分数", 20, "base"),
    ("Simulated_Siege.jsonc", "作战界面", "保存分数", 60, "base"),
    ("Roguelike_1/Roguelike_1.jsonc", "确认进入战斗冲突", "宣叙妄响内部_reco", None, "en_US"),
    ("Roguelike_4/Roguelike_4.jsonc", "肉鸽战斗_寒境曙光", "战斗完成_寒境曙光", None, "zh_TW"),
])
def test_existing_owner_sees_each_battle_and_start_cap_counts_physical_clicks(
    tmp_path, source, owner, settled, cap, language,
):
    graph = jsonc.loads((ROOT / "resource/base/pipeline" / source).read_text(encoding="utf-8"))
    preparation, battle, result = range(3)
    frames = [np.full((720, 1280, 3), stage, np.uint8) for stage in range(3)]
    state = preparation
    taps = []
    owned = 0

    class Screen(Controller):
        def screencap(self):
            return frames[state]

        def click(self, x, y):
            nonlocal state
            taps.append((x, y, state))
            state = battle
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            actual = int(argv.image[0, 0, 0])
            name = argv.node_name
            if name in (TEXT, COLOR) and actual == preparation:
                return RIGHT
            if (name, actual) in ((HUD, battle), (settled, result)):
                return (90, 60, 60, 20)
            return None

    class Battle(CustomAction):
        def run(self, context, argv):
            nonlocal state, owned
            owned += 1
            state = result if cap is None and owned == 2 else preparation
            return True

    resource, controller, tasker = load_resource(tmp_path, language), Screen(), Tasker()
    assert resource.register_custom_recognition("TestSharedStartPage", Page())
    assert resource.register_custom_action("TestExistingOwnerBattle", Battle())
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    detection = {"type": "Custom", "param": {"custom_recognition": "TestSharedStartPage"}}
    candidates = {node.removeprefix("[JumpBack]") for node in graph[owner]["next"]} - {START}
    patch = {name: {"recognition": detection} for name in candidates | {TEXT, COLOR, TOAST, "未选择队长"}}
    for name in (owner, START, CLICK, HANDOFF, "作战启动_进入过渡", "作战启动_终止", GRACEFUL, STOP, HUD, settled):
        patch.setdefault(name, {}).update(pre_delay=0, post_delay=0, rate_limit=10, timeout=650)
    patch[owner].update(recognition="DirectHit", action="DoNothing", post_wait_freezes=0)
    patch[HUD].update(action={"type": "Custom", "param": {"custom_action": "TestExistingOwnerBattle"}})
    patch[settled].update(action="StopTask")
    if cap == 20:
        # Use the actual cap override; owner topology is specifically D04 in this test.
        mapping = json.loads((ROOT / "tasks/历战映射.json").read_text(encoding="utf-8"))
        patch[CLICK].update(mapping["task"][0]["pipeline_override"][CLICK])
    try:
        job = tasker.post_task(owner, patch).wait()
        native_success = job.succeeded
        names = [tasker.get_node_detail(i).name for i in job.get().node_id_list]
    finally:
        stop_and_wait_idle(tasker)

    expected = cap if cap is not None else 2
    assert owned == len(taps) == names.count(CLICK) == names.count(HUD) == expected
    assert all(scene == preparation and inside((x, y), RIGHT) for x, y, scene in taps)
    assert native_success and (GRACEFUL in names) is (cap is not None)
    assert names[-1] == (STOP if cap is not None else settled)


@pytest.mark.parametrize("entry_route", [
    "map_to_loading", "map_to_preparation", "members", "members_delayed", "members_stalled",
    "foreign_formation", "unrelated_hud", "ledger_done", "ledger_unreadable",
])
def test_mapping_owner_keeps_combat_inside_a_proven_d08_branch(tmp_path, monkeypatch, entry_route):
    daily = "日常断点_历战映射"
    map_start = "开始挑战"
    members = "成员整备"
    clear = "清除队伍"
    formation_steps = [clear, "队伍净空_红", "队伍净空_黄", "队伍净空", "选择首发", "选择角色程序"]
    saved = "结算"
    terminate = "作战启动_终止"
    map_page, members_page, formation, loading, battle, results, foreign = range(7)
    next_scene = {
        "map_to_loading": map_page, "map_to_preparation": map_page,
        "members": members_page, "members_delayed": members_page, "members_stalled": members_page,
        "foreign_formation": foreign, "unrelated_hud": battle,
        "ledger_done": foreign, "ledger_unreadable": map_page,
    }
    frames = [np.full((720, 1280, 3), s, np.uint8) for s in range(7)]
    draw_text(frames[map_page], "START", 1001, 658)
    draw_hud(frames[battle])
    state = next_scene[entry_route]
    mapping = json.loads((ROOT / "tasks/历战映射.json").read_text(encoding="utf-8"))["task"][0]
    ledger = tmp_path / "daily-progress.json"
    if entry_route == "ledger_done":
        daily_progress.mark_done(mapping["entry"], path=ledger)
    elif entry_route == "ledger_unreadable":
        ledger.write_bytes(b"\xff broken")
    original_ledger = ledger.read_bytes() if ledger.exists() else None
    starts = []
    battle_calls = []
    stale_map_frames = 0

    class Screen(Controller):
        def screencap(self):
            nonlocal state, stale_map_frames
            if entry_route == "members_delayed" and state == members_page and starts:
                stale_map_frames += 1
                if stale_map_frames == 4:
                    state = formation
            return frames[state]

        def click(self, x, y):
            nonlocal state
            starts.append(state)
            if state == map_page:
                state = loading if entry_route == "map_to_loading" else formation
            elif state == members_page:
                if entry_route == "members":
                    state = formation
            elif state == formation:
                state = battle
            # Keep an unrelated page stable: if the graph clicks it, record the
            # attempted input and let the native failure path run to completion.
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            hit_page = {
                map_start: map_page, members: members_page, TEXT: formation, COLOR: formation,
                LOADING: loading, HUD: battle, saved: results,
                **{step: formation for step in formation_steps},
            }
            scene = int(argv.image[0, 0, 0])
            # The D08 map can already show a settlement action before combat.
            if argv.node_name == saved and scene == members_page:
                return (1045, 132, 46, 19)
            # A foreign formation can still satisfy the generic Start and Back
            # recognitions. Neither is evidence of D08 ownership.
            if scene == foreign and argv.node_name in {TEXT, COLOR, "返回", "返回主菜单"}:
                return RIGHT
            if hit_page.get(argv.node_name) == scene:
                return RIGHT
            return None

    class Progress(CustomAction):
        def run(self, context, argv):
            nonlocal state
            if argv.node_name == LOADING:
                state = battle
            elif argv.node_name == HUD:
                battle_calls.append(entry_route)
                state = results
            return True

    inspected = {}

    class NextTask(CustomAction):
        def run(self, context, argv):
            for name in (GRACEFUL, START, CLICK, HUD):
                inspected[name] = context.get_node_data(name)
            return True

    resource, controller, tasker = load_resource(tmp_path), Screen(), Tasker()
    register_daily_progress(resource, monkeypatch, ledger)
    assert resource.register_custom_recognition("TestMappingOwnerPage", Page())
    assert resource.register_custom_recognition("D08MapSettlement", D08MapSettlement())
    assert resource.register_custom_action("TestMappingOwnerProgress", Progress())
    assert resource.register_custom_action("CombatRunner", Progress())
    assert resource.register_custom_action("InspectNextTask", NextTask())
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    detection = {"type": "Custom", "param": {"custom_recognition": "TestMappingOwnerPage"}}
    progress = {"type": "Custom", "param": {"custom_action": "TestMappingOwnerProgress"}}
    patch = mapping["pipeline_override"]
    for name in (
        members, clear, saved, LOADING, POPUP, TEXT, COLOR, TOAST, "未选择队长",
        "活动未开启", "覆盖布局菜单", "识别历战分数", "前往作战", "打开历战映射", "打开挑战", "打开战斗",
        "意外点击_限定活动", "意外点击_环境情报", "意外点击_物品详情", "意外点击_历战分数", "确定", "返回",
        "战斗失败_历战", "继续挑战", "点击空白处关闭", "返回主菜单",
    ):
        patch.setdefault(name, {}).update(recognition=detection)
    for name in (mapping["entry"], daily_progress.ADMIT_NODE, daily, map_start, members, clear, saved, LOADING, HUD, START, CLICK,
                 HANDOFF, "作战启动_进入过渡", terminate, GRACEFUL, STOP, "返回主菜单"):
        patch.setdefault(name, {}).update(pre_delay=0, post_delay=0, post_wait_freezes=0,
                                          rate_limit=10, timeout=650)
    patch["历战映射_成员整备作战接续"] = {"pre_delay": 0, "post_delay": 0, "rate_limit": 10, "timeout": 650}
    # Isolate formation side effects, not the role workflow's real next edges.
    for step in formation_steps:
        patch.setdefault(step, {}).update(recognition=detection, action="DoNothing", pre_delay=0,
                                          post_delay=0, post_wait_freezes=0, rate_limit=10, timeout=650)
    patch[map_start]["recognition"] = {"param": {"expected": "^START$", "only_rec": True}}
    patch[saved].update(action="StopTask")
    patch[LOADING].update(action=progress)
    try:
        job = tasker.post_task(mapping["entry"], patch).wait()
        native_success = job.succeeded
        native_failure = job.failed
        names = [tasker.get_node_detail(i).name for i in job.get().node_id_list]
        if entry_route in {"foreign_formation", "unrelated_hud"}:
            probe = tasker.post_task("空任务", {"空任务": {"action": {
                "type": "Custom", "param": {"custom_action": "InspectNextTask"},
            }}}).wait()
            assert probe.succeeded
    finally:
        stop_and_wait_idle(tasker)

    admitted = [mapping["entry"], daily_progress.ADMIT_NODE]
    assert names[:2] == admitted, names
    assert (ledger.read_bytes() if ledger.exists() else None) == original_ledger
    if entry_route == "ledger_unreadable":
        assert native_failure and names == admitted and not starts and not battle_calls, names
        return
    if entry_route != "ledger_done":
        assert names[2] == daily, names
    if entry_route in {"foreign_formation", "unrelated_hud"}:
        # Native StopTask is a clean ending, but the named marker means failure
        # to the App. Merely stopping after an illegal click is not sufficient.
        assert not starts and not battle_calls, (starts, names)
        assert native_success and names[-3:] == [terminate, GRACEFUL, STOP], names
        assert {START, CLICK, HUD, "返回主菜单", "返回"}.isdisjoint(names), names
        assert any(edge["name"] == "返回" and edge["jump_back"] for edge in inspected[GRACEFUL]["next"])
        assert all(edge["name"] != "返回" for edge in inspected[START]["next"])
        assert inspected[CLICK]["max_hit"] == 60
        assert all(edge["name"] != "历战映射_异常收尾" for edge in inspected[HUD]["on_error"])
    elif entry_route == "ledger_done":
        assert native_success and names == [*admitted, "空任务"] and not starts and not battle_calls, names
    elif entry_route == "members_stalled":
        # Members still uses its existing default failure handling; it must
        # not complete D08 from the stale map's settlement button.
        assert native_success and names[-2:] == [GRACEFUL, STOP], names
        assert starts == [members_page] and not battle_calls, names
        assert {saved, CLICK, terminate}.isdisjoint(names), names
    else:
        assert native_success and names[-1] == saved and names.count(HUD) == 1, names
        assert battle_calls == [entry_route]
        expected = [next_scene[entry_route]]
        if entry_route != "map_to_loading":
            expected.append(formation)
            assert names.count(CLICK) == names.count(HANDOFF) == 1
        else:
            assert names.count(LOADING) == 1 and CLICK not in names
        if entry_route == "members":
            assert [name for name in names if name in formation_steps] == formation_steps
        assert starts == expected


@pytest.mark.parametrize("case", ["details", "team_word_only", "non_r02"])
def test_r02_character_details_hands_back_to_real_mode_owner(tmp_path, case):
    r02_owner = "战斗事件_厄愿潮声"
    recovery = "意外点击_选择人物"
    join = "厄愿潮声_角色详情_编入队伍"
    stats = "厄愿潮声_角色详情_战斗参数"
    pipe = ROOT / "resource/base/pipeline"
    owner = "肉鸽战斗_寒境曙光" if case == "non_r02" else r02_owner
    graph = jsonc.loads((pipe / ("Roguelike_4/Roguelike_4.jsonc" if case == "non_r02"
                               else "Roguelike_2/Roguelike_2.jsonc")).read_text(encoding="utf-8"))
    prep, detail, loaded = range(3)
    frames = [np.full((720, 1280, 3), step, np.uint8) for step in (prep, detail, loaded)]
    state = prep
    inputs = []

    class Screen(Controller):
        def screencap(self):
            return frames[state]

        def click(self, x, y):
            nonlocal state
            inputs.append((state, x, y))
            if state == detail:
                assert (x, y) == (78, 36)
                state = prep
            elif state == prep:
                assert inside((x, y), RIGHT)
                state = loaded if len(inputs) > 1 else detail
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            scene = int(argv.image[0, 0, 0])
            name = argv.node_name
            if scene == prep and name in (TEXT, COLOR):
                return RIGHT
            if scene == detail and (name == recovery or (name in (join, stats) and (case != "team_word_only" or name == join))):
                return (950, 470, 105, 33)
            if scene == loaded and name == LOADING:
                return (1180, 646, 30, 12)
            return None

    resource, controller, tasker = load_resource(tmp_path), Screen(), Tasker()
    assert resource.register_custom_recognition("TestR02StartOwnerPage", Page())
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    if case == "non_r02":
        patch = {}
    else:
        config = json.loads((ROOT / "tasks/厄愿潮声.json").read_text(encoding="utf-8"))
        patch = config["task"][0]["pipeline_override"]
    detection = {"type": "Custom", "param": {"custom_recognition": "TestR02StartOwnerPage"}}
    candidates = {node.removeprefix("[JumpBack]") for node in graph[owner]["next"]} - {START}
    for name in candidates | {TEXT, COLOR, TOAST, HUD, POPUP, "未选择队长", join, stats}:
        patch.setdefault(name, {}).update(recognition=detection, pre_delay=0, post_delay=0)
    for name in (owner, START, CLICK, HANDOFF, "作战启动_进入过渡", "作战启动_终止", GRACEFUL, STOP):
        patch.setdefault(name, {}).update(pre_delay=0, post_delay=0, rate_limit=20, timeout=650)
    patch[owner].update(recognition="DirectHit", action="DoNothing", on_error=[])
    patch[LOADING].update(action="StopTask")
    try:
        job = tasker.post_task(owner, patch).wait()
        native_success = job.succeeded
        names = [tasker.get_node_detail(i).name for i in job.get().node_id_list]
    finally:
        stop_and_wait_idle(tasker)

    assert native_success
    if case == "details":
        assert [tap[0] for tap in inputs] == [prep, detail, prep]
        assert [n for n in names if n in (CLICK, HANDOFF, recovery, LOADING)] == [
            CLICK, HANDOFF, recovery, CLICK, HANDOFF, LOADING,
        ]
        assert GRACEFUL not in names
    else:
        assert [tap[0] for tap in inputs] == [prep]
        assert HANDOFF not in names and recovery not in names
        assert names[-2:] == [GRACEFUL, STOP]
