"""Startup must close the matrix reward and chapter card before returning home."""
from threading import Timer

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.ClickAnchor import ClickAnchor
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


REWARD = "关闭奖励_矩阵循生"
TRANSITION = "转场_矩阵循生"
MAP_BACK = "启动_从矩阵地图返回"
RETURN = "返回主菜单"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry,initial", [("启动", 0), ("启动", 1), (RETURN, 1)])
def test_reward_chapter_map_menu_home_are_navigated_in_order(tmp_path, language, entry, initial):
    state = {"page": initial}
    clicks, wrong, expired = [], [], []
    regions = [(590, 220, 100, 40), (600, 10, 1, 1), (120, 20, 50, 30), (286, 19, 70, 31)]
    if language == "en_US":
        # The English bundle already clicks the home icon instead of its text.
        regions[3] = (196, 14, 84, 46)

    class Capture(Controller):
        def click(self, x, y):
            bx, by, width, height = regions[state["page"]]
            if state["page"] == 3:
                expected = (x, y) == (bx + width, by + height // 2)
            else:
                expected = bx <= x < bx + width and by <= y < by + height
            if not expected:
                wrong.append((state["page"], x, y))
                return False
            clicks.append(state["page"])
            state["page"] += 1
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == REWARD and page == 0:
                return regions[0]
            if name == TRANSITION and page == 1:
                return (1020, 650, 180, 45)
            if name in {"肉鸽内部", "更换人物"} and page == 2:
                return (1003, 627, 163, 55)
            if name == "返回_文字" and page == 2:
                return regions[2]
            if name == "返回主菜单_文字" and page in {0, 3}:
                return regions[3]  # Visible behind the reward too.
            if name in {"检查主界面", "检查主界面2"} and page == 4:
                return (1000, 300, 100, 100)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("StartupMatrixPages", Page())
    assert resource.register_custom_action("ClickAnchor", ClickAnchor())
    keep = {REWARD, TRANSITION, MAP_BACK, RETURN, "返回主菜单_reco", "检查主界面", "检查主界面2"}
    patch = {}
    for parent in ("启动", RETURN):
        for item in resource.get_node_data(parent)["next"]:
            name = (item if isinstance(item, str) else item["name"]).removeprefix("[JumpBack]")
            if name not in keep:
                patch[name] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=180, on_error=[])
    for name in keep | {"启动", "矩阵循生_奖励已关闭"}:
        patch.setdefault(name, {}).update(timing)
    for name in (REWARD, TRANSITION, "肉鸽内部", "更换人物", "返回_文字", "返回主菜单_文字", "检查主界面", "检查主界面2"):
        patch.setdefault(name, {}).update(recognition={"type": "Custom", "param": {"custom_recognition": "StartupMatrixPages"}})
    patch[entry].update(recognition="DirectHit", action="DoNothing")
    for name in ("检查主界面", "检查主界面2"):
        patch[name].update(action="StopTask", next=[])
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
        assert job.succeeded and not expired and not wrong, wrong
        assert clicks == list(range(initial, 4)) and state["page"] == 4
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
