"""Regression checks from the 2026-10-21 Android navigation screenshots."""

from pathlib import Path
import json
import re

import jsonc
import pytest

from action.basics.daily_progress import ADMIT_NODE, GUARD_POINTER


PIPELINE = Path(__file__).resolve().parents[2] / "resource/base/pipeline"


def test_activity_overview_return_accepts_both_layouts_with_page_guards():
    menu = jsonc.loads((PIPELINE / "Common/Battle_Menu.jsonc").read_text(encoding="utf-8"))
    node = menu["意外点击_限定活动"]
    variants = node["recognition"]["param"]["any_of"]
    assert node["recognition"]["type"] == "Or"
    assert variants[0] == "意外点击_限定活动_文字"
    assert menu[variants[0]]["recognition"] == {
        "type": "OCR",
        "param": {"roi": [0, 66, 210, 142], "expected": "限定开放"},
    }
    assert variants[1] == "意外点击_活动总览"
    overview = menu[variants[1]]["recognition"]
    assert overview["type"] == "And"
    guards = overview["param"]["all_of"]
    assert guards[:2] == ["返回", "返回主菜单_reco"]
    assert guards[2] == "活动总览_跳过动画文字"
    assert menu[guards[2]]["recognition"] == {
        "type": "OCR",
        "param": {"roi": [1040, 0, 200, 65], "expected": "跳过动画"},
    }
    assert node["action"]["param"]["target"] == [78, 36]


@pytest.mark.parametrize("language", ["zh_TW", "ja_JP", "en_US"])
def test_activity_label_localization_targets_the_ocr_node(language):
    localized = jsonc.loads(
        (PIPELINE.parents[1] / language / "pipeline/Common/Battle_Menu.jsonc").read_text(encoding="utf-8")
    )
    assert "意外点击_限定活动_文字" in localized
    assert "意外点击_限定活动" not in localized


def test_startup_returns_from_confirmed_guild_page_before_waiting_or_relaunching():
    startup = jsonc.loads((PIPELINE / "Start_up.jsonc").read_text(encoding="utf-8"))
    branches = startup["启动"]["next"]
    recovery = "[JumpBack]启动_从指挥局返回"
    assert branches.index("[JumpBack]返回主菜单") < branches.index(recovery)
    assert branches.index(recovery) < branches.index("[JumpBack]打开应用")
    assert branches.index(recovery) < branches.index("[JumpBack]计数器")
    node = startup["启动_从指挥局返回"]
    assert node["recognition"] == {
        "type": "And",
        "param": {"all_of": ["检查是否在指挥局内部", "返回_文字"], "box_index": 1},
    }
    assert node["action"]["type"] == "Click"
    assert node["post_delay"] == 1500


@pytest.mark.parametrize(
    ("matched_nodes", "expected"),
    [
        ({"意外点击_限定活动_文字"}, True),
        ({"返回_文字", "返回主菜单_文字", "活动总览_跳过动画文字"}, True),
        ({"返回主菜单_文字", "活动总览_跳过动画文字"}, False),
        ({"返回_文字", "活动总览_跳过动画文字"}, False),
        ({"返回_文字", "返回主菜单_文字"}, False),
        (set(), False),
    ],
)
def test_native_activity_guard_requires_a_real_page_match(tmp_path, matched_nodes, expected):
    import numpy as np
    from maa.controller import CustomController
    from maa.custom_recognition import CustomRecognition
    from maa.resource import Resource
    from maa.tasker import Tasker

    class MemoryController(CustomController):
        def connect(self):
            return True

        def request_uuid(self):
            return "navigation-guard-memory"

        def get_features(self):
            return 0

        def screencap(self):
            return np.zeros((720, 1280, 3), np.uint8)

    class PagePredicate(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name in matched_nodes:
                return (10, 10, 20, 20)
            return None

    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.post_bundle(PIPELINE.parent).wait().succeeded
    assert resource.register_custom_recognition("NavigationPagePredicate", PagePredicate())
    controller = MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    leaves = ["意外点击_限定活动_文字", "返回_文字", "返回主菜单_文字", "活动总览_跳过动画文字"]
    override = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "NavigationPagePredicate"}}}
        for name in leaves
    }
    override["意外点击_限定活动"] = {
        "action": "DoNothing", "next": [], "on_error": [], "timeout": 0,
        "pre_delay": 0, "post_delay": 0,
    }
    job = tasker.post_task("意外点击_限定活动", override).wait()
    assert job.succeeded is expected


@pytest.mark.parametrize(
    ("node_name", "observed_box"),
    [
        ("返回_文字", (125, 16, 58, 55)),  # Profile screen.
        ("返回_文字", (130, 22, 48, 29)),  # Guild screen.
        ("返回主菜单_文字", (288, 23, 65, 29)),  # Profile screen.
    ],
)
def test_navigation_roi_contains_complete_observed_label(node_name, observed_box):
    shared = jsonc.loads(
        (PIPELINE / "Common/Shared_Recognition.jsonc").read_text(encoding="utf-8")
    )
    x, y, width, height = shared[node_name]["recognition"]["param"]["roi"]
    bx, by, bw, bh = observed_box
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_layout_entry_can_confirm_popup_and_resume_navigation():
    layout = jsonc.loads(
        (PIPELINE / "Common/Read_Interface/Cache_Roi_Zone.json").read_text(
            encoding="utf-8"
        )
    )
    general = jsonc.loads((PIPELINE / "General.jsonc").read_text(encoding="utf-8"))

    # A plain terminal confirmation would end layout collection at the popup.
    # Reuse the existing click action and jump back to the navigation choices.
    assert "[JumpBack]确定" in layout["打开界面设置"]["next"]
    confirmation = general["确定"]
    assert confirmation["action"]["type"] == "Click"
    assert "确定_文字" in confirmation["recognition"]["param"]["all_of"]
    assert not confirmation.get("next")


def test_join_team_roi_contains_complete_observed_label():
    roles = jsonc.loads((PIPELINE / "Role_Selection.jsonc").read_text(encoding="utf-8"))
    x, y, width, height = roles["编入队伍"]["recognition"]["param"]["roi"]
    # 15:35 roster screenshot: the old left edge at 983 cropped off 编.
    bx, by, bw, bh = (952, 472, 102, 29)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_layout_tutorial_is_handled_before_capture_and_collapse():
    layout = jsonc.loads(
        (PIPELINE / "Common/Read_Interface/Cache_Roi_Zone.json").read_text(encoding="utf-8")
    )
    for parent, later in (("打开界面设置", "收起布局菜单"), ("收起布局菜单", "识别区域")):
        branches = layout[parent]["next"]
        assert branches.index("[JumpBack]关闭布局引导") < branches.index(later)
    # A tutorial can consume the first fold click; check the expanded menu again.
    assert "收起布局菜单" in layout["收起布局菜单"]["next"]
    # Do not calibrate while the menu is still expanded behind a tutorial.
    assert "展开布局菜单" in layout["识别区域"]["recognition"]["param"]["all_of"]


@pytest.mark.parametrize(
    "observed_box",
    [(141, 81, 86, 90), (134, 186, 96, 98), (136, 302, 92, 91),
     (136, 412, 92, 91), (136, 523, 91, 91)],
)
def test_role_portrait_roi_contains_observed_complete_portraits(observed_box):
    roles = jsonc.loads((PIPELINE / "Role_Selection.jsonc").read_text(encoding="utf-8"))
    x, y, width, height = roles["识别角色"]["recognition"]["param"]["roi"]
    bx, by, bw, bh = observed_box
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_trial_role_switch_roi_contains_complete_power_label():
    siege = jsonc.loads((PIPELINE / "Simulated_Siege.jsonc").read_text(encoding="utf-8"))
    x, y, width, height = siege["选择其他人物"]["recognition"]["param"]["roi"]
    bx, by, bw, bh = (259, 131, 52, 17)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_combat_roi_contains_live_pause_button_and_original_search_area():
    combat = jsonc.loads(
        (PIPELINE / "Auto_Battle/Auto_Battle.jsonc").read_text(encoding="utf-8")
    )
    x, y, width, height = combat["战斗中"]["recognition"]["param"]["roi"]
    # Three live combat frames all matched this complete 58x49 pause template.
    bx, by, bw, bh = (1145, 26, 58, 49)
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh
    # Keep the original top-right area for layouts with a farther-right button.
    assert x <= 1165 and y == 0
    assert x + width == 1280 and height == 100


@pytest.mark.parametrize(
    ("text", "locked"),
    [("碎片", True), ("10/50", True), ("/50", True),
     ("3/3", False), ("80", False), ("8159", False)],
)
def test_fragment_counter_fallback_does_not_match_owned_role_metadata(text, locked):
    roles = jsonc.loads((PIPELINE / "Role_Selection.jsonc").read_text(encoding="utf-8"))
    expected = roles["检查人物是否拥有"]["recognition"]["param"]["expected"]
    patterns = [expected] if isinstance(expected, str) else expected
    assert any(re.search(pattern, text) for pattern in patterns) is locked


@pytest.mark.parametrize(
    ("filename", "node_name", "observed_box"),
    [
        ("Phantom_Pain_Cage.jsonc", "检查A区", (573, 425, 85, 28)),
        ("Phantom_Pain_Cage.jsonc", "挑战次数不足", (709, 108, 41, 35)),
        ("War_Zone.jsonc", "纷争战区内部", (503, 121, 53, 30)),
        ("War_Zone.jsonc", "首通已完成", (1143, 511, 53, 35)),
        ("War_Zone.jsonc", "战斗准备", (992, 656, 97, 30)),
        ("War_Zone.jsonc", "进入战区选人界面", (1141, 20, 59, 56)),
    ],
)
def test_daily_mode_roi_contains_observed_complete_label(filename, node_name, observed_box):
    pipeline = jsonc.loads((PIPELINE / filename).read_text(encoding="utf-8"))
    x, y, width, height = pipeline[node_name]["recognition"]["param"]["roi"]
    bx, by, bw, bh = observed_box
    assert x <= bx and y <= by
    assert x + width >= bx + bw and y + height >= by + bh


def test_cage_marker_keeps_pixel_threshold_and_stays_in_first_card():
    cage = jsonc.loads((PIPELINE / "Phantom_Pain_Cage.jsonc").read_text(encoding="utf-8"))
    param = cage["检查A区"]["recognition"]["param"]
    # Preserve the upstream false-positive guard; the old ROI clipped the marker.
    assert param["count"] == 1000
    x, y, width, height = param["roi"]
    assert x <= 462 and y <= 384
    assert x + width >= 605 and y + height >= 679
    assert x + width < 725  # Do not borrow the next card's blue pixels.


def test_war_zone_header_does_not_merge_two_rows_into_one_ocr_box():
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    _, y, _, height = war["纷争战区内部"]["recognition"]["param"]["roi"]
    # Original PNG: the lower 排行 row starts at y=153. Both rows together
    # are detected as a square and misread, even after widening the old ROI.
    assert y + height <= 153


def test_mapping_settlement_retry_uses_button_result_and_is_bounded():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    retry = mapping["结算_点击失效"]
    assert retry["recognition"]["type"] == "And"
    assert retry["recognition"]["param"]["all_of"] == ["结算"]
    assert retry["recognition"]["param"].get("box_index", 0) == 0
    assert retry["action"]["type"] == "Click"
    assert retry["action"].get("param", {}).get("target", True) is True
    assert 0 < retry["max_hit"] <= 3
    assert "[JumpBack]结算_点击失效" in mapping["结算"]["next"]


def _entry_next(war, parent):
    """入口工作链：断点续接接线后入口 next 首位是 守卫节点，原链在其 next 下。"""
    node = war[parent]
    guard = (node.get("attach") or {}).get(GUARD_POINTER)
    if guard:
        return war[guard]["next"]
    branches = node["next"]
    if branches and str(branches[0]).startswith("日常断点_"):
        return war[str(branches[0])]["next"]
    return branches


@pytest.mark.parametrize(
    ("parent", "later"),
    [("纷争战区", "纷争战区内部"),
     ("纷争战区内部", "[JumpBack]首通未完成"),
     ("首通未完成", "战斗准备")],
)
def test_war_zone_closes_profile_modal_before_map_actions(parent, later):
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    branches = _entry_next(war, parent)
    assert branches.index("[JumpBack]关闭战区玩家详情") < branches.index(later)
    close = war["关闭战区玩家详情"]
    assert close["recognition"]["param"]["all_of"] == [
        "意外点击_玩家详情", "战区玩家详情关闭按钮"
    ]
    assert close["recognition"]["param"]["box_index"] == 1
    assert close["action"].get("param", {}).get("target", True) is True
    x, y, width, height = war["战区玩家详情关闭按钮"]["recognition"]["param"]["roi"]
    assert x <= 1180 and y <= 51
    assert x + width >= 1225 and y + height >= 98


def test_war_profile_close_symbol_bypasses_batch_text_detection():
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    param = war["战区玩家详情关闭按钮"]["recognition"]["param"]
    # On the same phone image, individual OCR finds X but the real next-list
    # batch detector misses it. Recognize only the cropped symbol instead.
    assert param["only_rec"] is True
    x, y, width, height = param["roi"]
    assert x >= 1170 and x + width <= 1230
    assert y >= 47 and y + height <= 100  # Exclude the + above and score below.


def test_mapping_gates_only_map_settlement_and_drops_the_currency_retry():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    name = "历战映射_地图本轮结算准入"
    start = mapping["开始挑战"]
    gate = mapping[name]
    assert start["next"] == [
        "[JumpBack]战斗中",
        "[JumpBack]确定",
        name,
        "[JumpBack]战斗失败_历战",
        "[JumpBack]继续挑战",
        "[JumpBack]载入中",
        "[JumpBack]通用处理-未下载弹窗-角色皮肤没下载-检测层",
        "[JumpBack]点击空白处关闭",
        "[JumpBack]作战开始",
        "[JumpBack]意外点击_物品详情",
    ]
    assert "开始挑战_点击丢失" not in mapping
    assert start["on_error"] == gate["on_error"] == ["作战启动_终止"]
    # The pre-battle map and the old score screen both show 结算, so the plain OCR
    # alone is not enough: only a real HUD handover after this start may continue.
    assert gate["recognition"] == {
        "type": "And",
        "param": {
            "all_of": [
                "结算",
                {
                    "recognition": {
                        "type": "Custom",
                        "param": {"custom_recognition": "D08MapSettlement"},
                    }
                },
            ],
            "box_index": 0,
        },
    }
    assert gate["action"] == {"type": "DoNothing"} and gate["next"] == ["结算"]
    assert gate["pre_delay"] == gate["post_delay"] == gate["rate_limit"] == 0
    assert {key for key, node in mapping.items() if name in node.get("next", [])} == {"开始挑战"}
    assert mapping["历战映射_成员整备作战接续"]["next"] == [
        "[JumpBack]战斗中",
        "[JumpBack]确定",
        "结算",
        "[JumpBack]战斗失败_历战",
        "[JumpBack]继续挑战",
        "[JumpBack]载入中",
        "[JumpBack]通用处理-未下载弹窗-角色皮肤没下载-检测层",
        "[JumpBack]点击空白处关闭",
        "[JumpBack]清除队伍",
    ]
    assert mapping["结算"]["action"]["type"] == "Click"
    tasks = PIPELINE.parents[2] / "tasks/历战映射.json"
    overrides = jsonc.loads(tasks.read_text(encoding="utf-8"))["task"][0]["pipeline_override"]
    assert overrides["战斗中"]["on_error"] == ["历战映射_异常收尾"]
    assert overrides["点击作战开始"]["max_hit"] == 20


def test_mapping_cold_entry_requires_its_own_path_before_shared_start():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    entry = mapping["历战映射"]
    assert entry["next"] == [ADMIT_NODE] and entry["on_error"] == []
    guard = entry["attach"][GUARD_POINTER]
    assert guard == "日常断点_历战映射"
    cold = mapping[guard]
    assert cold["on_error"] == ["作战启动_终止"]
    # No page-level guessing from the shared start button: the cold entry has to
    # walk its own path (开始挑战 → map gate → 结算) instead of tapping 作战开始.
    assert "[JumpBack]作战开始" not in cold["next"]
    assert {"[JumpBack]开始挑战", "[JumpBack]成员整备"} <= set(cold["next"])
    assert "[JumpBack]作战开始" in mapping["开始挑战"]["next"]
    assert mapping["成员整备"]["next"] == ["历战映射_成员整备作战接续"]
    assert mapping["历战映射_成员整备作战接续"]["recognition"]["param"]["all_of"] == ["清除队伍"]


def test_mapping_item_modal_close_is_inside_live_x_and_bounded():
    mapping = jsonc.loads((PIPELINE / "HighRankMapping.jsonc").read_text(encoding="utf-8"))
    close = mapping["意外点击_物品详情"]
    x, y = close["action"]["param"]["target"]
    assert 1180 <= x < 1225 and 51 <= y < 96
    assert 0 < close["max_hit"] <= 3
    assert "[JumpBack]意外点击_物品详情" in mapping["结算"]["next"]


def test_shared_battle_start_click_is_bounded():
    # FG-COMMON-START: an unresponsive 作战开始 used to loop through this click
    # without any budget (260+ taps in the D08 first run). The shared budget
    # must stay above every normal per-task click count (R04 is the widest at
    # roughly one tap per battle) while bounding a stuck run to minutes.
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    click = war["点击作战开始"]
    assert click["max_hit"] == 60
    # Physical target freshness, the per-attempt timeout and re-entry are
    # exercised against the loaded graph in test_shared_combat_start_native.


def test_mapping_battle_start_override_keeps_proven_budget():
    tasks = json.loads(
        (Path(__file__).resolve().parents[2] / "tasks/历战映射.json").read_text(
            encoding="utf-8"
        )
    )
    override = tasks["task"][0]["pipeline_override"]
    # The rerun with max_hit=20 plus a [JumpBack]返回 fallback passed on the
    # real device (eight battles, one start each, zero idle taps); keep it.
    assert override["点击作战开始"]["max_hit"] == 20
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    assert override["作战开始"]["next"] == [*war["作战开始"]["next"], "[JumpBack]返回"]


@pytest.mark.parametrize("page", ["settlement", "battle", "unknown"])
def test_native_mapping_combat_failure_recovers_only_known_pages(tmp_path, page):
    import numpy as np
    from maa.controller import CustomController
    from maa.custom_action import CustomAction
    from maa.custom_recognition import CustomRecognition
    from maa.resource import Resource
    from maa.tasker import Tasker
    from test_support.native_lifecycle import stop_and_wait_idle

    task = json.loads((PIPELINE.parents[2] / "tasks/历战映射.json").read_text(encoding="utf-8"))
    override = task["task"][0]["pipeline_override"]
    assert override["战斗中"]["on_error"] == ["历战映射_异常收尾"]
    phase = [0 if page != "unknown" else -1]
    actions = []
    steps = (
        ["战斗中", "历战映射_撤退文字", "确定", "结算", "返回主菜单"]
        if page == "battle" else ["战斗失败_历战", "结算", "返回主菜单"]
    )

    class MemoryController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "mapping-failure-recovery"
        def get_features(self): return 0
        def screencap(self): return np.zeros((720, 1280, 3), np.uint8)

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            expected = {name: index for index, name in enumerate(steps)}
            if page == "battle":
                expected["历战映射_重新开始文字"] = 1
            return (10, 10, 20, 20) if phase[0] == expected.get(argv.node_name, -2) else None

    class Act(CustomAction):
        def run(self, context, argv):
            if argv.node_name == "MappingFailureProbe":
                return CustomAction.RunResult(success=False)
            actions.append(argv.node_name)
            phase[0] += 1
            return CustomAction.RunResult(success=True)

    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), MemoryController(), Tasker()
    assert resource.post_bundle(PIPELINE.parent).wait().succeeded
    assert resource.register_custom_recognition("MappingRecoveryPage", Page())
    assert resource.register_custom_action("MappingRecoveryAction", Act())
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    for name in ("战斗中", "战斗失败_历战", "结算", "返回主菜单", "确定",
                 "历战映射_撤退文字", "历战映射_重新开始文字"):
        override.setdefault(name, {})["recognition"] = {
            "type": "Custom", "param": {"custom_recognition": "MappingRecoveryPage"},
        }
    for name in ("MappingFailureProbe", "战斗失败_历战", "历战映射_异常结算", "返回主菜单",
                 "历战映射_异常暂停", "历战映射_异常撤退", "历战映射_异常确定"):
        override.setdefault(name, {}).update({
            "action": {"type": "Custom", "param": {"custom_action": "MappingRecoveryAction"}},
            "next": [], "pre_delay": 0, "post_delay": 0,
            "post_wait_freezes": {"time": 0},
        })
    override["MappingFailureProbe"].update({
        "recognition": "DirectHit", "on_error": override["战斗中"]["on_error"],
    })
    override["历战映射_异常收尾"] = {"timeout": 300, "rate_limit": 0}
    override["任务失败优雅退出"] = {"timeout": 300, "rate_limit": 0}
    try:
        job = tasker.post_task("MappingFailureProbe", override).wait()
        assert job.succeeded
        expected_actions = {
            "settlement": ["战斗失败_历战", "历战映射_异常结算", "返回主菜单"],
            "battle": ["历战映射_异常暂停", "历战映射_异常撤退", "历战映射_异常确定",
                       "历战映射_异常结算", "返回主菜单"],
            "unknown": [],
        }
        assert actions == expected_actions[page]
        names = [tasker.get_node_detail(node_id).name for node_id in job.get().node_id_list]
        assert "历战映射_异常收尾" in names
        assert ("任务失败优雅退出" in names) is (page == "unknown")
    finally:
        stop_and_wait_idle(tasker)


def test_shared_battle_start_consumers_are_frozen():
    # Any new next-list consumer of the shared 作战开始 node must be checked
    # against the shared click budget before shipping. Extend the frozen set
    # deliberately, not silently.
    expected = {
        ("选择角色程序", "Role_Selection.jsonc"),
        ("使用预设", "Role_Selection.jsonc"),
        ("写入预设队伍色位", "Role_Selection.jsonc"),
        ("开始挑战", "HighRankMapping.jsonc"),
        ("作战界面", "Simulated_Siege.jsonc"),
        # R01 的固定坐标捷径不算 next-list 消费者；冲突确认另有共享启动路径。
        ("确认进入战斗冲突", "Roguelike_1/Roguelike_1.jsonc"),
        ("战斗事件_厄愿潮声", "Roguelike_2/Roguelike_2.jsonc"),
        ("肉鸽战斗_寒境曙光", "Roguelike_4/Roguelike_4.jsonc"),
    }
    found = set()
    for path in list(PIPELINE.rglob("*.jsonc")) + list(PIPELINE.rglob("*.json")):
        data = jsonc.loads(path.read_text(encoding="utf-8"))
        rel = path.relative_to(PIPELINE).as_posix()
        for name, body in data.items():
            for item in body.get("next") or []:
                if str(item).replace("[JumpBack]", "") == "作战开始":
                    found.add((name, rel))
    assert found == expected


def test_cage_reuses_shared_start_recognition_without_click():
    # The cage pipeline reuses the shared 作战开始 recognition as a plain And
    # condition; changing the shared ROI/threshold silently affects these.
    cage = jsonc.loads((PIPELINE / "Phantom_Pain_Cage.jsonc").read_text(encoding="utf-8"))
    for node in ("没打过", "意外处理_进入作战界面"):
        assert cage[node]["recognition"]["param"]["all_of"] == ["作战开始"]
        assert "点击作战开始" not in (cage[node].get("next") or [])


def test_cage_challenge_tab_keeps_the_full_shared_footer_label():
    cage = jsonc.loads((PIPELINE / "Phantom_Pain_Cage.jsonc").read_text(encoding="utf-8"))
    war = jsonc.loads((PIPELINE / "War_Zone.jsonc").read_text(encoding="utf-8"))
    roi = cage["打开挑战_幻痛囚笼"]["recognition"]["param"]["roi"]
    assert roi == war["打开挑战_纷争战区"]["recognition"]["param"]["roi"]
    x, y, width, height = roi
    # The old x=1130 crop retained only 战 on the actual battle panel footer.
    assert x <= 1100 and x + width >= 1157
    assert y <= 669 and y + height >= 694


def test_battlefield_battle_button_taps_a_fixed_center_point():
    # 11-27 device A/B on the real 主界面: the 战斗 box (990,258,134,96) only opens
    # the 作战面板 from its centre; the old Click target_offset [60,0,-60,0] kept the
    # full 96 px height and randomised y, so ~30% of draws hit the 活动总览 hot band at
    # the box top (y=259/287) and 打开资源 then never matched. A fixed centre tap
    # removes the random component entirely.
    nav = jsonc.loads(
        (PIPELINE / "daily_battle/Navigation.jsonc").read_text(encoding="utf-8")
    )
    node = nav["打开战斗_拟战场域"]
    action = node["action"]
    assert action["type"] == "Custom"
    assert action["param"]["custom_action"] == "ClickAnchor"
    assert action["param"]["custom_action_param"] == {"position": "center"}
    assert "target_offset" not in action["param"]
    assert node["next"] == ["打开资源"]


def test_battlefield_auto_and_multi_challenge_branches_stay_wired():
    # 11-27 code read: 拟战 has two entries after 确认进入拟战场域. The auto path is
    # device-verified (01:26 run); the 多重挑战 path is reached through a node named
    # 开始手动战斗 whose recognition text is actually 多重挑战, so a rename or a
    # recognition edit would silently disable the multi-challenge popup handling.
    battle = jsonc.loads((PIPELINE / "daily_battle/Auto_Battle.jsonc").read_text(encoding="utf-8"))
    manual = jsonc.loads((PIPELINE / "daily_battle/Manual_Battle.jsonc").read_text(encoding="utf-8"))
    nav = jsonc.loads((PIPELINE / "daily_battle/Navigation.jsonc").read_text(encoding="utf-8"))

    assert nav["确认进入拟战场域"]["next"] == ["开始自动战斗", "开始手动战斗"]
    assert battle["开始自动战斗"]["recognition"]["param"]["expected"] == "最大"
    assert battle["点击自动作战"]["next"] == [
        "[JumpBack]等级提升",
        "检查结果_体力不足",
        "检查结果_战斗完成",
    ]

    entry = manual["开始手动战斗"]
    assert entry["recognition"]["type"] == "OCR"
    assert entry["recognition"]["param"]["expected"] == "多重挑战"
    assert entry["next"] == ["[JumpBack]覆盖布局菜单", "作战开始_拟战场域", "手动战斗_体力不足"]
    assert manual["手动战斗_作战失败"]["next"] == ["返回主菜单"]
    assert manual["手动战斗_战斗成功"]["next"] == ["返回主菜单"]
    # 手动战斗_体力不足 is the only node that recognises the real 体力不足 dialog.
    assert manual["手动战斗_体力不足"]["recognition"]["param"]["expected"] == "是否使用"


def test_battlefield_insufficient_serum_gate_reuses_the_auto_battle_button():
    # The auto path has no 体力不足 dialog to read: 检查结果_体力不足 matches the very
    # same 自动作战 button the click just failed to dismiss. Both nodes must move
    # together, and the shared recognition is intentionally the button (not a dialog).
    battle = jsonc.loads((PIPELINE / "daily_battle/Auto_Battle.jsonc").read_text(encoding="utf-8"))
    click = battle["点击自动作战"]["recognition"]
    gate = battle["检查结果_体力不足"]["recognition"]
    assert gate == click
    assert gate["type"] == "OCR" and gate["param"]["expected"] == "自动作战"
    assert battle["检查结果_体力不足"]["next"] == ["返回主菜单"]
    # 检查结果_战斗完成 clicks the settlement 确定 and falls back to 返回主菜单.
    done = battle["检查结果_战斗完成"]
    assert done["recognition"]["param"]["all_of"] == ["确定_文字"]
    assert done["next"][0] == "返回主菜单"


def test_stamina_topup_guard_requires_a_serum_reading_below_one_battle():
    # 2026-11-27 05:09 device run: 拟战 开血清页时血清 39/240（够一场 30），却照样
    # 用掉了当天刚免费领的 60 毫升补给包（39 + 60 - 30 + 2 = 71）。补血清本身要能跑，
    # 但只应在「一场都打不起」时触发，所以 吃体力药 多一个纯识别的血清门槛。
    stamina = jsonc.loads((PIPELINE / "daily_battle/Stamina.jsonc").read_text(encoding="utf-8"))
    topup = stamina["吃体力药"]["recognition"]
    assert topup["type"] == "And"
    assert topup["param"]["all_of"] == ["主界面_战斗节点", "拟战_主页血清数量", "拟战_需补血清"]
    # box_index must stay on the serum readout: it is the click target of the top-up.
    assert topup["param"]["box_index"] == 1

    guard = stamina["拟战_需补血清"]["recognition"]
    readout = stamina["拟战_主页血清数量"]["recognition"]
    assert guard["type"] == "OCR"
    assert guard["param"]["roi"] == readout["param"]["roi"]

    below = re.compile(guard["param"]["expected"])
    above = re.compile(readout["param"]["expected"])
    for value in ("0", "1", "9", "10", "29"):
        text = f"{value}/240"
        assert below.fullmatch(text), text
        assert above.fullmatch(text), text
    for value in ("30", "31", "39", "71", "239", "240"):
        text = f"{value}/240"
        assert not below.fullmatch(text), text
        assert above.fullmatch(text), text
    # 一场 拟战 的血清成本是 30，所以门槛必须正好落在 30 上，而不是 29 或 31。
    assert below.fullmatch("30/240") is None
