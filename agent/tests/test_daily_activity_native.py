"""Native daily completion: real color checks, synthetic pages, no device input."""

from pathlib import Path

import json
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
CHEST_X = (621, 754, 887, 1020, 1154)
TIERS = {"0-19": (0, 19), "20-39": (20, 39), "40-59": (40, 59),
         "60-79": (60, 79), "80-99": (80, 99), "100": (100, 200)}


def native_resource(tmp_path, language="base"):
    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if language != "base":
        assert resource.post_bundle(ROOT / "resource" / language).wait().succeeded
    return resource


class MemoryController(CustomController):
    def connect(self): return True
    def request_uuid(self): return "daily-activity-regression"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), np.uint8)


def predicate(name):
    return {"recognition": {"type": "Custom", "param": {"custom_recognition": name}}}


@pytest.mark.parametrize("home,counter", [(True, True), (True, False), (False, True), (False, False)])
def test_stamina_entry_requires_home_and_same_frame_counter(tmp_path, home, counter):
    clicks = []

    class Controller(MemoryController):
        def click(self, x, y):
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == "主界面_战斗节点" and home:
                return (990, 258, 120, 100)
            if argv.node_name == "拟战_主页血清数量" and counter:
                return (920, 60, 60, 30)
            return None

    resource = native_resource(tmp_path)
    assert resource.register_custom_recognition("DailyStaminaPage", Page())
    controller = Controller()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    overrides = {name: predicate("DailyStaminaPage") for name in ("主界面_战斗节点", "拟战_主页血清数量")}
    overrides["吃体力药"] = {"next": [], "on_error": [], "pre_delay": 0, "post_delay": 0, "timeout": 100}
    try:
        job = tasker.post_task("吃体力药", overrides).wait()
        assert job.succeeded is (home and counter)
        assert len(clicks) == int(home and counter)
        for x, y in clicks:
            assert 920 <= x < 980 and 60 <= y < 90
    finally:
        stop_and_wait_idle(tasker)


def test_stamina_usage_keeps_existing_switch_and_never_enters_black_card_purchase():
    task = json.loads((ROOT / "tasks/拟战场域.json").read_text(encoding="utf-8"))
    cases = {case["name"]: case["pipeline_override"]["拟战场域"]["next"]
             for case in task["option"]["使用临期体力"]["cases"]}
    assert "吃体力药" in cases["Yes"]
    assert "吃体力药" not in cases["No"]
    import jsonc
    nodes = jsonc.loads((ROOT / "resource/base/pipeline/daily_battle/Stamina.jsonc").read_text(encoding="utf-8"))
    assert nodes["无体力药"]["next"] == ["退出血清界面"]
    assert nodes["无体力药"].get("action", {"type": "DoNothing"})["type"] == "DoNothing"


@pytest.mark.parametrize("home,arrow", [(True, True), (True, False), (False, True), (False, False)])
def test_compact_menu_expansion_requires_same_frame_home_and_arrow(tmp_path, home, arrow):
    clicks = []

    class Controller(MemoryController):
        def click(self, x, y):
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == "主界面_战斗节点" and home:
                return (1000, 330, 130, 80)
            if argv.node_name == "切换主界面状态_按钮" and arrow:
                return (1140, 490, 35, 30)
            return None

    resource = native_resource(tmp_path)
    assert resource.register_custom_recognition("CompactHome", Page())
    controller = Controller()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task("切换主界面状态", {
            "主界面_战斗节点": predicate("CompactHome"),
            "切换主界面状态_按钮": predicate("CompactHome"),
            "切换主界面状态": {"next": [], "on_error": [], "pre_delay": 0, "post_delay": 0},
        }).wait()
        assert job.succeeded is (home and arrow)
        assert len(clicks) == int(home and arrow)
        assert all(1140 <= x < 1175 and 490 <= y < 520 for x, y in clicks)
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("value,claimed,collects,completes,claim_works", [
    (0, (), 0, False, True),
    (50, (), 1, False, True),
    (50, (0, 1), 0, False, True),
    (50, (0, 1, 2, 3, 4), 0, False, True),
    (99, (), 1, False, True),
    (100, (), 1, True, True),
    (130, (0, 1), 1, True, True),
    (200, (0, 1, 2, 3, 4), 0, True, True),
    (100, (0, 1, 2, 3), 1, False, False),
])
def test_reward_completion_requires_score_and_all_five_claimed(
        tmp_path, value, claimed, collects, completes, claim_works):
    state = {"page": "daily", "claimed": set(claimed), "collections": 0, "home": 0}

    class Controller(MemoryController):
        def screencap(self):
            frame = super().screencap()
            if state["page"] == "daily":
                for index in state["claimed"]:
                    x = CHEST_X[index]
                    frame[621:651, x-15:x+15] = (0, 255, 0)
            return frame

        def click(self, x, y):
            if y == 638 and x in CHEST_X:
                assert state["page"] == "daily"
                state["collections"] += 1
                if claim_works:
                    state["claimed"].update(i for i in range(5) if value >= (i+1)*20)
                    state["page"] = "reward"
            elif (x, y) == (446, 30):
                assert state["page"] == "reward"
                state["page"] = "daily"
            else:
                assert 245 <= x < 315 and 20 <= y < 55
                state["home"] += 1
                state["page"] = "home"
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name = argv.node_name
            if name == "关闭奖励":
                return (400, 20, 30, 30) if state["page"] == "reward" else None
            if state["page"] != "daily":
                return None
            if name in {"每日任务", "返回主菜单_文字"}:
                return (245, 20, 70, 35)
            if name.startswith("领取活跃"):
                minimum, maximum = TIERS[name.removeprefix("领取活跃")]
                return (328, 655, 100, 49) if minimum <= value <= maximum else None
            return None

    resource = native_resource(tmp_path)
    assert resource.register_custom_recognition("DailyRewardPage", Page())
    controller = Controller()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    overrides = {name: predicate("DailyRewardPage") for name in (
        "每日任务", "返回主菜单_文字", "关闭奖励", "一键领取_任务", *("领取活跃"+tier for tier in TIERS))}
    overrides["每日任务"].update(action="DoNothing", pre_delay=0, post_delay=0)
    overrides["返回主菜单"] = {"next": [], "post_delay": 0}
    overrides["任务失败优雅退出"] = {"next": [], "on_error": []}
    for name in [*("领取活跃"+tier for tier in TIERS), "每日活跃不足返回", "关闭奖励"]:
        overrides.setdefault(name, {}).update(pre_delay=0, post_delay=0, timeout=250, rate_limit=0,
                                             post_wait_freezes={"time": 0})
    try:
        job = tasker.post_task("每日任务", overrides).wait()
        assert job.succeeded
        names = [tasker.get_node_detail(i).name for i in job.get().node_id_list]
        assert ("任务失败优雅退出" not in names) is completes
        assert ("每日活跃宝箱全部已领" in names) is completes
        assert state["collections"] == collects
        if completes:
            assert state["home"] == 1
    finally:
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("language", ["base", "zh_TW", "ja_JP", "en_US"])
@pytest.mark.parametrize("menu_appears,profile_opens", [(True, True), (False, False), (True, False)])
def test_task_entry_requires_assistant_menu_and_profile_before_claiming(
        tmp_path, language, menu_appears, profile_opens):
    clicks = []
    state = {"page": "home"}

    class Controller(MemoryController):
        def click(self, x, y):
            clicks.append((x, y))
            if (x, y) == (620, 150):
                assert state["page"] == "home"
                if menu_appears:
                    state["page"] = "menu"
            elif 460 <= x < 520 and 270 <= y < 305:
                assert state["page"] == "menu"
                state["page"] = "profile" if profile_opens else "home"
            elif 75 <= x < 105 and 20 <= y < 50:
                assert state["page"] == "profile"
                state["page"] = "home"
            else:
                raise AssertionError((x, y, state))
            return True

    class Home(CustomRecognition):
        def analyze(self, context, argv):
            name = argv.node_name
            if name == "主界面_战斗节点" and state["page"] in {"home", "menu"}:
                return (990, 258, 120, 100)
            if name == "每日助理交流_入口文字" and state["page"] == "menu":
                return (460, 270, 60, 35)
            if name == "每日助理交流_档案文字" and state["page"] == "profile":
                return (810, 90, 100, 30)
            if name == "返回" and state["page"] == "profile":
                return (75, 20, 30, 30)
            return None

    resource = native_resource(tmp_path, language)
    assert resource.register_custom_recognition("DailyAssistantHome", Home())
    controller = Controller()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        overrides = {name: predicate("DailyAssistantHome") for name in (
            "主界面_战斗节点", "每日助理交流_入口文字", "每日助理交流_档案文字", "返回")}
        for name in ("每日助理交流", "每日助理交流_进入", "每日助理交流_完成返回"):
            overrides[name] = {"pre_delay": 0, "post_delay": 0, "timeout": 150, "rate_limit": 0}
        overrides["任务_领取"] = {"next": [], "pre_delay": 0, "post_delay": 0}
        overrides["任务失败优雅退出"] = {"next": [], "on_error": []}
        job = tasker.post_task("任务", overrides).wait()
        assert job.succeeded
        names = [tasker.get_node_detail(i).name for i in job.get().node_id_list]
        completed = menu_appears and profile_opens
        assert ("任务_领取" in names) is completed
        assert ("每日助理交流_完成返回" in names) is completed
        assert ("任务失败优雅退出" in names) is not completed
        assert clicks[0] == (620, 150)
        assert len(clicks) == 1 + int(menu_appears) + int(completed)
        if completed:
            assert names == ["任务", "每日助理交流", "每日助理交流_进入", "每日助理交流_完成返回", "任务_领取"]
    finally:
        stop_and_wait_idle(tasker)
