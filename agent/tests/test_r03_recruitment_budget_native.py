"""R03 recruitment: real form/cost/selection pixels and fail-closed bookkeeping."""
import os
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from threading import Timer
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from action.combat.config import ROLE_ACTIONS
from test_support.fakes import FakeContext
from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]
ROLE = "露西亚·深红囚影"


def native_probe(tmp_path, probe, *, field=False, event_team=False, opening=None, adjusting=False):
    ocr_bundle = os.environ.get("MAA_TEST_OCR_BUNDLE")
    if not ocr_bundle and not list((ROOT / "resource/base/model").rglob("*.onnx")):
        pytest.skip("OCR models absent; set MAA_TEST_OCR_BUNDLE")
    frames = {}
    for name in ("owned", "selected", "cost1", "cost2", "team", "event_team", "event_filled",
                 "flow_owned", "flow_trial", "guili_owned"):
        frame = cv2.imread(str(EVIDENCE_DIR / f"r03_recruitment_{name}.png"))
        assert frame is not None and frame.shape == (720, 1280, 3)
        frames[name] = frame
    for name in ("formation_empty", "formation_red", "formation_two", "formation_leader", "formation_unset", "adjustment"):
        frame = cv2.imread(str(EVIDENCE_DIR / f"r03_recruitment_{name}.png"))
        assert frame is not None and frame.shape == (720, 1280, 3)
        frames[name] = frame
    # Relocated real tick pixels simulate the two clicks, not an observed device transition.
    frames["adjusted"] = frames["adjustment"].copy()
    for x in (929, 1053):
        frames["adjusted"][360:412, x:x+55] = frames["adjustment"][233:285, x:x+55]

    class Capture(CustomController):
        def __init__(self):
            super().__init__()
            self.inputs = []
            self.frame = frames[event_team or "owned"]

        def connect(self): return True
        def request_uuid(self): return "offline-r03-recruitment-budget"
        def get_features(self): return 0
        def screencap(self): return self.frame
        def click(self, x, y, contact=0, pressure=1):
            self.inputs.append((x, y))
            if field:
                self.frame = frames["team"]
            if opening:
                self.frame = frames[opening]
            if adjusting:
                if y > 580:
                    self.frame = frames["formation_leader"] if x > 850 else frames["adjustment"].copy()
                elif 360 <= y <= 412:
                    left = 929 if x < 1000 else 1053
                    self.frame[360:412, left:left+55] = frames["adjustment"][233:285, left:left+55]
            return True

    class Probe(CustomAction):
        def run(self, context, argv):
            probe(context, frames)
            return True

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu() and resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if ocr_bundle:
        assert resource.post_bundle(Path(ocr_bundle)).wait().succeeded
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    assert resource.register_custom_action("RecruitProbe", Probe())
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(25, stop)
    watchdog.start()
    try:
        job = tasker.post_task("矩阵循生_编入角色" if field else "RecruitProbe", {
            "RecruitProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "RecruitProbe"}},
                "next": [], "on_error": [],
            },
        }).wait()
        assert job.succeeded and not expired
        return controller.inputs
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


def test_native_budget_and_one_two_four_cost_icons(tmp_path):
    values = []

    def probe(context, frames):
        reader = R03Recruitment(RoleSelection())
        values.extend(reader.read_budget(context, frames[name]) for name in ("owned", "cost1", "cost2", "selected"))
        values.append(reader.read_budget(context, frames["team"], team=True))
        # The selected frame includes a real refusal toast over the slash: reject 313.
        for name, roi in (("owned", [240, 232, 155, 32]), ("cost1", [240, 186, 155, 32]), ("cost2", [240, 92, 155, 32])):
            result = context.run_recognition("矩阵循生_角色招募消耗", frames[name], {
                "矩阵循生_角色招募消耗": {"recognition": {"param": {"roi": roi}}},
            })
            values.append(len(result.filtered_results))

    assert native_probe(tmp_path, probe) == []
    assert values == [(3, 3), (1, 3), (3, 3), None, (0, 3), 4, 1, 2]


def test_native_owned_trial_and_insufficient_budget_are_separate(tmp_path, monkeypatch):
    values = []

    def probe(context, frames):
        reader = R03Recruitment(RoleSelection())
        image = frames["owned"]
        values.append(reader.candidates(context, image, ROLE, ROLE_ACTIONS[ROLE]))
        # Simulated budget 4 isolates the real avatar/form/cost recognizers.
        monkeypatch.setattr(reader, "read_budget", lambda *_args, **_kwargs: (4, 4))
        values.append(reader.candidates(context, image, ROLE, ROLE_ACTIONS[ROLE]))
        values.append(reader.candidates(context, image, ROLE + "[试用]", ROLE_ACTIONS[ROLE]))

    assert native_probe(tmp_path, probe) == []
    assert values == [[], [([142, 228, 82, 82], (4, 4), 4)], [([142, 433, 82, 82], (4, 4), 4)]]


def test_native_one_energy_support_forms_keep_trial_identity(tmp_path):
    values = []

    def probe(context, frames):
        reader = R03Recruitment(RoleSelection())
        for image, name in (("flow_owned", "丽芙·流光"), ("flow_trial", "丽芙·流光"),
                            ("flow_trial", "丽芙·流光[试用]"), ("guili_owned", "薇拉·瑰丽")):
            action = ROLE_ACTIONS[name.replace("[试用]", "")]
            values.append(reader.candidates(context, frames[image], name, action))

    assert native_probe(tmp_path, probe) == []
    assert values == [
        [([143, 324, 82, 82], (1, 3), 1)], [],
        [([143, 181, 82, 82], (1, 3), 1)],
        [([142, 330, 82, 82], (5, 5), 1)],
    ]


def test_native_formation_slots_require_title_budget_and_fixed_color_markers(tmp_path):
    values = []

    def probe(context, frames):
        reader = R03Recruitment(RoleSelection())
        for name in ("formation_empty", "formation_red", "formation_two"):
            values.append(reader.read_formation(context, frames[name]))
        image = frames["formation_red"].copy()
        image[455:495, 534:579] = 0
        values.append(reader.read_formation(context, image))
        values.append(reader.read_formation(context, frames["flow_owned"]))

    assert native_probe(tmp_path, probe) == []
    assert values == [
        {"budget": (5, 5), "empty": {"B": True, "R": True, "Y": True}, "red_first": False},
        {"budget": (1, 3), "empty": {"B": True, "R": False, "Y": True}, "red_first": True},
        {"budget": (0, 3), "empty": {"B": False, "R": False, "Y": True}, "red_first": True},
        None, None,
    ]


@pytest.mark.parametrize(("after", "expected"), [("flow_owned", True), ("guili_owned", False)])
def test_native_open_slot_confirms_list_and_unchanged_budget(tmp_path, after, expected):
    values = []

    def probe(context, frames):
        values.append(R03Recruitment(RoleSelection()).open_slot(context, "B"))

    inputs = native_probe(tmp_path, probe, event_team="formation_red", opening=after)
    assert values == [expected] and len(inputs) == 1
    assert 230 <= inputs[0][0] <= 280 and 345 <= inputs[0][1] <= 395


def test_native_open_slot_does_not_click_an_occupied_slot(tmp_path):
    values = []

    def probe(context, frames):
        values.append(R03Recruitment(RoleSelection()).open_slot(context, "B"))

    assert native_probe(tmp_path, probe, event_team="formation_two", opening="flow_owned") == []
    assert values == [False]


def test_native_adjustment_distinguishes_checked_empty_and_missing_page(tmp_path):
    values = []

    def probe(context, frames):
        for name in ("adjustment", "adjusted", "formation_unset"):
            image = frames[name]
            values.append(context.run_recognition("矩阵循生_调整页就绪", image).hit)
            for label in ("队长", "首发"):
                values.append(context.run_recognition(f"矩阵循生_设置红位{label}", image).hit)
                values.append(context.run_recognition(f"矩阵循生_红位{label}已勾选", image).hit)
            values.append(context.run_recognition("矩阵循生_保存队伍调整", image).hit)
        for name in ("formation_unset", "formation_leader"):
            values.append(R03Recruitment.red_ready(context, frames[name]))

    assert native_probe(tmp_path, probe) == []
    assert values == [True, True, False, True, False, False,
                      True, False, True, False, True, True,
                      False, False, False, False, False, False,
                      False, True]


@pytest.mark.parametrize("already_ready", [False, True])
def test_native_red_leader_checks_both_boxes_and_saved_formation(tmp_path, already_ready):
    values = []

    def probe(context, _frames):
        values.append(R03Recruitment(RoleSelection()).ensure_red_leader(context))

    inputs = native_probe(tmp_path, probe,
                          event_team="formation_leader" if already_ready else "formation_unset",
                          adjusting=True)
    assert values == [True]
    if already_ready:
        assert inputs == []
    else:
        assert len(inputs) == 4
        assert 700 <= inputs[0][0] <= 910 and inputs[0][1] > 600
        assert 930 <= inputs[1][0] <= 980 and 360 <= inputs[1][1] <= 410
        assert 1053 <= inputs[2][0] <= 1108 and 360 <= inputs[2][1] <= 410
        assert 847 <= inputs[3][0] <= 1077 and inputs[3][1] > 594


@pytest.mark.parametrize(("before", "expected"), [("flow_owned", True), ("owned", False)])
def test_native_return_from_list_requires_same_budget(tmp_path, before, expected):
    values = []

    def probe(context, _frames):
        values.append(R03Recruitment(RoleSelection()).return_to_formation(context) is not None)

    inputs = native_probe(tmp_path, probe, event_team=before, opening="formation_unset")
    assert values == [expected] and inputs == [(98, 36)]


def test_native_selected_corners_do_not_borrow_previous_row(tmp_path):
    values = []

    def probe(context, frames):
        patch = R03Recruitment.selection_patch([142, 228, 82, 82])
        for name in ("owned", "selected"):
            result = context.run_recognition("矩阵循生_选中角色", frames[name], patch)
            values.append(result.hit)

    assert native_probe(tmp_path, probe) == []
    assert values == [False, True]


def test_native_post_event_recruitment_entry_sees_the_entire_join_label(tmp_path):
    values = []

    def probe(context, frames):
        for image in (frames["owned"], np.zeros_like(frames["owned"])):
            values.append(context.run_recognition("点击精通等级低于12的人物", image).hit)

    assert native_probe(tmp_path, probe) == []
    assert values == [True, False]


def test_native_fielding_waits_for_team_page_without_recursive_role_selection(tmp_path):
    inputs = native_probe(tmp_path, lambda *_args: None, field=True)
    assert len(inputs) == 1
    x, y = inputs[0]
    assert 990 <= x <= 1220 and 615 <= y <= 700


@pytest.mark.parametrize("frame_name", ["event_team", "event_filled"])
def test_native_event_team_returns_and_confirms_only_with_page_identity(tmp_path, frame_name):
    values = []

    def probe(context, frames):
        for image in (frames[frame_name], frames["owned"], np.zeros_like(frames["owned"])):
            values.append(context.run_recognition("矩阵循生_物归编队页", image).hit)
            values.append(context.run_recognition("矩阵循生_编入后确认", image).hit)
        detail = context.run_task("选择角色程序_矩阵循生", {
            "选择角色程序_矩阵循生": {"recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"}},
            "确定编队_矩阵循生": {"action": {"type": "StopTask"}, "post_delay": 0},
        })
        values.append([context.tasker.get_node_detail(n).name for n in detail.node_id_list][-2:])

    # The controller must also supply the event team after the entry action.
    assert native_probe(tmp_path, probe, event_team=frame_name) == []
    assert values == [True, True, False, False, False, False,
                      ["矩阵循生_物归编队确认", "确定编队_矩阵循生"]]


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("fielded", [False, True])
def test_r03_consumes_formal_cache_or_explicit_trial_and_publishes_only_confirmed(
    tmp_path, monkeypatch, cached, fielded,
):
    selector = RoleSelection()
    monkeypatch.setattr(selector, "_cache_path", lambda: tmp_path / "absent.json")
    selector._load_cache = Mock(return_value={ROLE: {"power": 7586, "lightning": 100}} if cached else None)
    selector.send_msg = Mock()
    selector._publish_combat_team_roster = Mock()
    selector.find_role = Mock(side_effect=AssertionError("R03 must use recruitment guards"))
    recruit = Mock(return_value=fielded)
    monkeypatch.setattr(R03Recruitment, "recruit", recruit)
    context = FakeContext(node_data={"pick": {"attach": {"selection_mode": "roguelike_3", "max_try": 3}}})
    result = selector.run(context, SimpleNamespace(node_name="pick", custom_action_param="{}"))
    assert result.success is fielded
    name = recruit.call_args.args[2]
    assert (name == ROLE) if cached else name.endswith("[试用]")
    assert selector._load_cache.call_count == 1
    assert selector._publish_combat_team_roster.call_count == int(fielded)
    assert not context.tasker.controller.clicks


def test_stopped_r03_recruitment_does_not_open_filter_or_click():
    context = FakeContext()
    context.tasker.stopping = True
    reader = R03Recruitment(RoleSelection())
    assert not reader.recruit(context, ROLE_ACTIONS, ROLE, 3)
    assert reader.return_to_formation(context) is None
    assert not reader.ensure_red_leader(context)
    assert not context.run_task_calls and not context.actions and not context.tasker.controller.clicks


def test_missing_capture_does_not_trigger_implicit_recognition_or_list_swipe():
    context = FakeContext()
    context.run_recognition = Mock(side_effect=AssertionError("No recognition without a supplied frame"))
    reader = R03Recruitment(RoleSelection())
    reader.selector._get_role_element_task = Mock(return_value=None)
    assert reader.read_budget(context, None) is None
    assert reader.read_formation(context, None) is None
    assert reader.candidates(context, None, ROLE, ROLE_ACTIONS[ROLE]) == []
    context.tasker.controller.post_screencap = Mock(return_value=SimpleNamespace(
        wait=lambda: SimpleNamespace(get=lambda: None),
    ))
    assert not reader.recruit(context, ROLE_ACTIONS, ROLE, 3)
    assert not reader.open_slot(context, "B")
    assert not context.run_task_calls and not context.actions and not context.tasker.controller.clicks


@pytest.mark.parametrize("include_supported", [False, True])
def test_r03_cache_keeps_existing_matrix_roster_before_ranking(monkeypatch, include_supported):
    selector = RoleSelection()
    cached = {"比安卡·晖暮": {"power": 7803, "dark": 100}}
    if include_supported:
        cached[ROLE] = {"power": 7586, "lightning": 100}
    monkeypatch.setattr(selector, "_load_cache", Mock(return_value=cached))
    monkeypatch.setattr(selector, "send_msg", Mock())
    monkeypatch.setattr(selector, "_publish_combat_team_roster", Mock())
    recruit = Mock(return_value=True)
    monkeypatch.setattr(R03Recruitment, "recruit", recruit)
    context = FakeContext(node_data={"pick": {"attach": {"selection_mode": "roguelike_3"}}})
    assert selector.run(context, SimpleNamespace(node_name="pick", custom_action_param="{}")).success
    name = recruit.call_args.args[2]
    assert name == ROLE if include_supported else name.endswith("[试用]")
    assert name != "比安卡·晖暮"
    assert cached["比安卡·晖暮"]["power"] == 7803


@pytest.mark.parametrize(("selected", "same_row", "returned", "after", "expected"), [
    (False, True, True, (0, 4), False),
    (True, False, True, (0, 4), False),
    (True, True, False, (0, 4), False),
    (True, True, True, None, False),
    (True, True, True, (4, 4), False),
    (True, True, True, (0, 4), True),
])
def test_recruitment_requires_selection_same_row_return_and_exact_deduction(
    monkeypatch, selected, same_row, returned, after, expected,
):
    selector = RoleSelection()
    selector._get_role_element_task = Mock(return_value=None)
    reader = R03Recruitment(selector)
    context = FakeContext()
    context.run_task = Mock(return_value=object())
    context.run_recognition = Mock(return_value=SimpleNamespace(hit=True))
    row = ([142, 228, 82, 82], (4, 4), 4)
    monkeypatch.setattr(reader, "candidates", Mock(side_effect=[[row], [row] if same_row else []]))
    monkeypatch.setattr(reader, "completed", Mock(side_effect=[selected, returned]))
    monkeypatch.setattr(reader, "read_budget", Mock(return_value=after))
    assert reader.recruit(context, ROLE_ACTIONS, ROLE, 3) is expected
    entries = [call.args[0] for call in context.run_task.call_args_list]
    assert entries == (["矩阵循生_点选角色", "矩阵循生_编入角色"] if selected and same_row else ["矩阵循生_点选角色"])
    assert not context.actions
