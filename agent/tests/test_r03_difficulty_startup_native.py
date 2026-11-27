"""A stopped matrix difficulty page can return home without choosing a difficulty."""
from threading import Timer

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.ClickAnchor import ClickAnchor
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


BACK = "启动_从矩阵难度返回"
TITLE = "矩阵循生_难度选择标题"
CARD = "矩阵循生_堆栈潜伏名称"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["启动", "返回主菜单"])
@pytest.mark.parametrize("missing", [None, TITLE, CARD, "返回_文字"])
def test_difficulty_return_needs_title_specific_card_and_back(tmp_path, language, entry, missing):
    state, clicks, expired = {"page": 0}, [], []
    home_region = (196, 14, 84, 46) if language == "en_US" else (285, 10, 75, 55)

    class Capture(Controller):
        def click(self, x, y):
            clicks.append((state["page"], x, y))
            state["page"] += 1
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if page == 0 and name in {TITLE, CARD, "返回_文字"} and name != missing:
                return (130, 20, 60, 40) if name == "返回_文字" else (75, 300, 180, 40)
            if page == 1 and name == "返回主菜单_文字":
                return home_region
            if page == 2 and name in {"检查主界面", "检查主界面2"}:
                return (1000, 300, 100, 100)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("MatrixDifficultyStartup", Page())
    assert resource.register_custom_action("ClickAnchor", ClickAnchor())
    keep = {BACK, "返回主菜单", "返回主菜单_reco", "检查主界面", "检查主界面2"}
    patch = {}
    for parent in ("启动", "返回主菜单"):
        for child in resource.get_node_data(parent)["next"]:
            if child["name"] not in keep:
                patch[child["name"]] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=120, on_error=[])
    for name in keep | {"启动"}:
        if resource.get_node_data(name) is not None:
            patch.setdefault(name, {}).update(timing)
    for name in (TITLE, CARD, "返回_文字", "返回主菜单_文字", "检查主界面", "检查主界面2"):
        patch.setdefault(name, {}).update(recognition={"type": "Custom", "param": {
            "custom_recognition": "MatrixDifficultyStartup",
        }})
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
        assert not expired
        assert job.succeeded is (missing is None)
        assert [page for page, _, _ in clicks] == ([0, 1] if missing is None else [])
        if missing is None:
            _, back_x, back_y = clicks[0]
            assert 130 <= back_x < 190 and 20 <= back_y < 60
            home_x, home_y, width, height = home_region
            assert clicks[1] == (1, home_x + width, home_y + height // 2)
        assert state["page"] == (2 if missing is None else 0)
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
