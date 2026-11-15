"""The post-boss brand page is part of the adventure, not a combat screen."""
from threading import Timer
import re

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


ADVANCE = "不祥烙印_前进"
TITLE = "不祥烙印_标题"
BUTTON = "不祥烙印_前进文字"
COMPLETE = "矩阵循生_模拟演算完成"
SELECT = "不祥烙印_选择剧情"
OPTION = "不祥烙印_剧情选项"
CONFIRM = "不祥烙印_确认选项"
DECIDE = "决定_矩阵循生"
EVENT = "不祥烙印_矩阵循生"
TAKE = "不祥烙印_取走"
TAKE_TEXT = "不祥烙印_取走文字"
REWARD = "关闭奖励_矩阵循生"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("text,allowed", [
    ("前进", True), ("前進", True), ("环顾四周", True), ("環顧四周", True),
    ("休整", True), ("决定", False), ("支付50", False), ("继续", False), ("终止", False),
])
def test_brand_progression_accepts_only_observed_story_controls(tmp_path, language, text, allowed):
    resource = load_resource(tmp_path, language)
    patterns = resource.get_node_data(BUTTON)["recognition"]["param"]["expected"]
    if isinstance(patterns, str):
        patterns = [patterns]
    assert any(re.search(pattern, text) for pattern in patterns) is allowed


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["矩阵循生", "BOSS战_矩阵循生"])
@pytest.mark.parametrize("flow,missing", [
    ("advance", None), ("advance", "title"), ("advance", "button"),
    ("choice", None), ("choice", "title"), ("choice", "button"), ("choice", "option"),
    ("take", None), ("take", "title"), ("take", "button"),
])
def test_brand_advances_only_its_button_then_hands_back_to_completion(tmp_path, language, entry, missing, flow):
    state = {"page": 0}
    clicks, expired = [], []

    class Capture(Controller):
        def click(self, x, y):
            if flow == "take" and state["page"] == 1:
                assert 590 <= x < 670 and 220 <= y < 260
                state["page"] = 2
            elif flow == "choice" and state["page"] == 0:
                assert 730 <= x < 900 and 360 <= y < 395
                state["page"] = .5
            else:
                assert 990 <= x < 1050 and 620 <= y < 660
                state["page"] = int(state["page"]) + 1
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if flow == "take" and state["page"] == 1:
                if argv.node_name == REWARD:
                    return (590, 220, 80, 40)
                if argv.node_name == MAP:
                    return (1003, 627, 163, 55)  # Visible behind the reward overlay.
                return None
            if state["page"] < 2:
                if argv.node_name == TITLE and missing != "title":
                    return (670, 110, 150, 35)
                if argv.node_name == OPTION and flow == "choice" and state["page"] < 1 and missing != "option":
                    return (730, 360, 170, 35)
                current_button = TAKE_TEXT if flow == "take" else (DECIDE if flow == "choice" and state["page"] < 1 else BUTTON)
                if argv.node_name == current_button and missing != "button":
                    return (990, 620, 60, 40)
            if argv.node_name == (MAP if flow == "take" else COMPLETE) and state["page"] == 2:
                return (500, 380, 220, 40)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("BrandStoryPage", Page())
    parent = resource.get_node_data(entry)
    allowed = {EVENT, ADVANCE, SELECT, COMPLETE, MAP}
    patch = {
        (node if isinstance(node, str) else node["name"]).removeprefix("[JumpBack]"): {"enabled": False}
        for node in parent["next"]
        if (node if isinstance(node, str) else node["name"]).removeprefix("[JumpBack]") not in allowed
    }
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=100, on_error=[])
    patch[entry] = {**timing, "recognition": "DirectHit", "action": "DoNothing"}
    for name in (EVENT, ADVANCE, SELECT, CONFIRM, TAKE, "矩阵循生_奖励已关闭"):
        if resource.get_node_data(name) is not None:
            patch[name] = dict(timing)
    for name in (TITLE, BUTTON, OPTION, DECIDE, COMPLETE, TAKE_TEXT, REWARD, MAP):
        patch[name] = {**timing, "recognition": {"type": "Custom", "param": {
            "custom_recognition": "BrandStoryPage",
        }}}
    patch[COMPLETE].update(action="StopTask", next=[])
    patch[MAP].update(action="StopTask", next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    def stop():
        expired.append(True)
        tasker.post_stop()
    timer = Timer(10, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, patch).wait()
        assert not expired
        names = [tasker.get_node_detail(n).name for n in tasker.get_task_detail(job.job_id).node_id_list]
        if missing is None:
            assert job.succeeded and names[-1] == (MAP if flow == "take" else COMPLETE), names
            assert len(clicks) == (3 if flow == "choice" else 2)
            assert names.count(ADVANCE) == {"advance": 2, "choice": 1, "take": 0}[flow]
            assert names.count(SELECT) == names.count(CONFIRM) == (flow == "choice")
            assert names.count(TAKE) == (flow == "take")
        else:
            assert job.failed and clicks == [] and state["page"] == 0
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("case", ["both", "first_text_missing", "arrows_missing", "paragraph_only"])
def test_real_option_ocr_uses_only_the_first_arrow_region(tmp_path, case):
    import cv2
    import numpy as np
    from maa.custom_action import CustomAction
    from test_r03_recruitment_native import native_hub

    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        frame[:] = 240
        for y, text in [(371, "FIRST"), (461, "SECOND")]:
            if case != "arrows_missing":
                cv2.fillPoly(frame, [np.array([[702, y], [702, y + 12], [713, y + 6]])], (225, 160, 100))
            if case != "paragraph_only" and not (case == "first_text_missing" and y == 371):
                cv2.putText(frame, text, (735, y + 11), cv2.FONT_HERSHEY_SIMPLEX, .7, (20, 20, 20), 2)
        cv2.putText(frame, "STORY PARAGRAPH", (700, 215), cv2.FONT_HERSHEY_SIMPLEX, .7, (20, 20, 20), 2)
        # Thin blue borders are not option arrows.
        for y in (344, 408, 434, 498):
            cv2.line(frame, (698, y), (719, y), (225, 160, 100), 1)
        results, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    result = context.run_recognition(OPTION, frame)
                    results.append((bool(result and result.hit), result.box if result and result.hit else None))
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        assert resource.register_custom_action("BrandOptionProbe", Probe())
        job = tasker.post_task("BrandOptionProbe", {"BrandOptionProbe": {
            "recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "BrandOptionProbe"}},
            "next": [], "on_error": [],
        }}).wait()
        assert job.succeeded and not expired and not errors and not inputs, errors
        assert len(results) == 1 and results[0][0] is (case == "both")
        if case == "both":
            assert 350 <= results[0][1][1] < 410
