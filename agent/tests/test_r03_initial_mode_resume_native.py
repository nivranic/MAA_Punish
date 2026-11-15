"""A selected initial team still needs its budget mode restored before start."""
from threading import Timer

import cv2
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.PPOverride import PPOverride
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("budget,three", [("3/3", True), ("1/3", True), ("0/3", True), ("4/4", False), ("1/4", False), ("5/5", False), ("0/5", False), ("4/3", None), ("6/5", None), ("1/7", None), ("3", None), ("1/7", "existing")])
def test_mode_initializes_before_start_even_when_a_member_is_already_selected(tmp_path, language, budget, three):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.putText(frame, budget, (108, 638), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2, cv2.LINE_AA)
    seen = []

    class Capture(Controller):
        def screencap(self): return frame
        def click(self, *_): raise AssertionError("No unverified game input")

    class Page(CustomRecognition):
        def analyze(self, ctx, argv):
            if argv.node_name in {"矩阵循生_初始首发标记", "编队完成_矩阵循生"}:
                return (100, 100, 20, 20)
            if argv.node_name == "开始演算" and three != "existing":
                return (100, 100, 20, 20)
            return None

    class Start(CustomAction):
        def run(self, ctx, argv):
            seen.append((ctx.get_node_data("选择模式_矩阵循生")["enabled"],
                         ctx.get_node_data("进入物归新主_矩阵循生")["enabled"]))
            return True

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_action("PPOverride", PPOverride())
    assert resource.register_custom_action("ObserveStart", Start())
    assert resource.register_custom_recognition("InitialPage", Page())
    root = resource.get_node_data("矩阵循生")
    patch = {name["name"]: {"recognition": {"type": "Custom", "param": {"custom_recognition": "InitialPage"}}}
             for name in root["next"] if name["name"] != "选择模式_矩阵循生"}
    for name in ("矩阵循生_初始首发标记", "开始演算"):
        patch[name] = {"recognition": {"type": "Custom", "param": {"custom_recognition": "InitialPage"}}}
    patch["矩阵循生"] = {"pre_delay": 0, "post_delay": 0, "timeout": 500, "on_error": []}
    for name in ("选择模式_矩阵循生", "招募能量3点", "招募能量4点", "编队完成_矩阵循生"):
        patch.setdefault(name, {}).update(pre_delay=0, post_delay=0, timeout=500, on_error=[])
    patch["编队完成_矩阵循生"].update(action={"type": "Custom", "param": {"custom_action": "ObserveStart"}}, next=["ModeProbeEnd"])
    patch["ModeProbeEnd"] = {"recognition": "DirectHit", "action": "StopTask", "next": [], "on_error": []}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    timer = Timer(15, tasker.post_stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task("矩阵循生", patch).wait()
        if three is None:
            assert job.failed and seen == []
            return
        assert job.succeeded
        if three == "existing":
            assert seen == [(True, False)]
            return
        assert seen == [(False, three)]
        detail = tasker.get_task_detail(job.job_id)
        visited = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert ("招募能量3点" if three else "招募能量4点") in visited
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
