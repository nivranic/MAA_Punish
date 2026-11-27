"""Use the actual OCR model for selected D09 Chinese role and locked-stage labels."""

import json

import pytest
from maa.tasker import Tasker

from test_audit_purchase_native import MemoryController
from test_daily_activity_native import ROOT, native_resource
from test_support.chinese_frame import chinese_frame
from test_support.native_lifecycle import stop_and_wait_idle


ROLE_LABELS = (
    ("破晓", "破曉"), ("徊闪", "徊閃"), ("觅语", "覓語"), ("擎驱", "擎驅"),
    ("明觉", "明覺"), ("游麟", "遊麟"), ("瑰丽", "瑰麗"), ("银牙", "銀牙"),
    ("异火", "異火"), ("风暴", "風暴"), ("蚀暗", "蝕暗"), ("红莲", "紅蓮"),
)
PROBE = "D09_中文只读探针"
ROLE = "A级角色"
LOCKED = "未解锁自动作战"
MANUAL = "未解锁自动作战_战斗准备"


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("case_id,traditional", ROLE_LABELS, ids=[role[0] for role in ROLE_LABELS])
def test_selected_role_matches_its_cn_label_and_rejects_other_roles(tmp_path, language, case_id, traditional):
    pi = json.loads((ROOT / "tasks/刷A级碎片.json").read_text(encoding="utf-8"))
    selection = next(case for case in pi["option"]["选择A级角色"]["cases"] if case["name"] == case_id)
    translations = json.loads((ROOT / "i18n/zh_tw.json").read_text(encoding="utf-8"))
    assert translations[selection["label"][1:]] == traditional
    literal_option = selection["pipeline_override"]
    resource = native_resource(tmp_path, language)
    wanted = traditional if language == "zh_TW" else case_id
    wrong = "耀炎"
    outcomes = inspect_labels(resource, language, ROLE, (280, 477), [wanted, wrong], literal_option)
    assert outcomes == [True, False], (language, wanted, wrong, outcomes)


@pytest.mark.parametrize("language,expected,negative", [
    ("base", "战斗", "自动作战"),
    ("zh_TW", "戰鬥", "自動作戰"),
])
def test_locked_stage_requires_battle_not_auto_battle(tmp_path, language, expected, negative):
    resource = native_resource(tmp_path, language)
    outcomes = inspect_labels(resource, language, LOCKED, (951, 625), [expected, negative], {})
    assert outcomes == [True, False], (language, expected, negative, outcomes)


@pytest.mark.parametrize("language,expected,negative", [
    ("base", "战斗准备", "自动作战"),
    ("zh_TW", "戰鬥準備", "自動作戰"),
])
def test_battle_ready_label_is_recognised_inside_the_widened_roi(tmp_path, language, expected, negative):
    resource = native_resource(tmp_path, language)
    outcomes = inspect_labels(resource, language, MANUAL, (958, 633), [expected, negative], {})
    assert outcomes == [True, False], (language, expected, negative, outcomes)


def inspect_labels(resource, language, node, position, labels, option):
    controller, tasker = MemoryController(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    probe = {
        PROBE: {"next": [node], "on_error": [], "post_delay": 0, "rate_limit": 10, "timeout": 350},
        node: {"action": "DoNothing", "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0},
    }
    try:
        outcomes = []
        for label in labels:
            image = chinese_frame(language, [(position, label)], font_size=24 if node == LOCKED else 31)
            controller.screencap = lambda: image.copy()
            outcomes.append(tasker.post_task(PROBE, [option, probe]).wait().succeeded)
        assert controller.clicks == []
        return outcomes
    finally:
        stop_and_wait_idle(tasker)
