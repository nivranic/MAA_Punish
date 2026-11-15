"""A recognized matrix leave dialog takes priority over its background back button."""
from threading import Timer

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle

EXIT = "矩阵循生_确认离开演算"
TITLE = "矩阵循生_离开演算标题"
BUTTON = "矩阵循生_离开演算确认文字"
GONE = "矩阵循生_离开演算弹窗已关闭"
EQUIPMENT_RETURN = "启动_从矩阵装备返回"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["启动", "任务失败优雅退出"])
@pytest.mark.parametrize("case", ["valid", "title_missing", "button_missing", "stays_open", "equipment"])
def test_leave_confirmation_is_scoped_and_waits_for_the_overlay(tmp_path, language, entry, case):
    state = {"closed": False, "settling": 0, "opened": case != "equipment"}
    clicks = []

    class Capture(Controller):
        def screencap(self):
            if state["settling"] and case != "stays_open":
                state["settling"] -= 1
                state["closed"] = state["settling"] == 0
            return super().screencap()

        def click(self, x, y):
            clicks.append((x, y))
            if not state["opened"]:
                assert 120 <= x < 170 and 20 <= y < 50
                state["opened"] = True
                return True
            assert not state["closed"] and not state["settling"]
            assert 710 <= x < 770 and 465 <= y < 500, "Clicked the background back button"
            state["settling"] = 3
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name = argv.node_name
            if name in {"选择装备_矩阵循生", "矩阵循生_指定穿戴位标题"} and not state["opened"]:
                return (70, 70, 190, 40)
            if name == "返回_文字" and not state["opened"]:
                return (120, 20, 50, 30)
            if name == TITLE and state["opened"] and not state["closed"] and case != "title_missing":
                return (540, 320, 200, 40)
            if name == BUTTON and state["opened"] and not state["closed"] and case != "button_missing":
                return (710, 465, 60, 35)
            if name == "返回" and not state["closed"] and case in {"valid", "stays_open", "equipment"}:
                return (120, 20, 50, 30)
            if name == "检查主界面" and state["closed"]:
                return (1080, 270, 100, 100)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("MatrixLeavePage", Page())
    source = "Start_up.jsonc" if entry == "启动" else "General.jsonc"
    nodes = jsonc.loads((ROOT / "resource/base/pipeline" / source).read_text("utf-8"))
    overrides = {
        name.removeprefix("[JumpBack]"): {"enabled": False}
        for name in nodes[entry]["next"]
        if name.removeprefix("[JumpBack]") not in {EXIT, EQUIPMENT_RETURN, "返回", "停止任务", "检查主界面"}
    }
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    for name in (entry, EXIT, GONE, EQUIPMENT_RETURN, "返回", "停止任务", "检查主界面"):
        overrides[name] = dict(timing)
    for name in (TITLE, BUTTON, "返回", "检查主界面", "选择装备_矩阵循生", "矩阵循生_指定穿戴位标题", "返回_文字"):
        overrides.setdefault(name, {}).update(recognition={"type": "Custom", "param": {
            "custom_recognition": "MatrixLeavePage",
        }})
    overrides["返回"].update(action={"type": "Click"}, next=[])
    overrides["检查主界面"].update(action={"type": "StopTask"}, next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []
    def stop():
        expired.append(True)
        tasker.post_stop()
    timer = Timer(10, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, overrides).wait()
        assert not expired
        if case in {"valid", "equipment"}:
            assert job.succeeded and state["closed"] and len(clicks) == (2 if case == "equipment" else 1)
        elif case == "stays_open":
            assert job.failed and not state["closed"] and len(clicks) == 1
        else:
            assert clicks == [] and not state["closed"]
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
