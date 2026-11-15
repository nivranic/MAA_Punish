"""R03 reward transitions must not click through into map item details."""

from threading import Timer

import jsonc
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


DETAIL = "意外点击藏品_矩阵循生"
ITEM_TITLE = "矩阵循生_物品详情标题"
COLLECTION_TITLE = "矩阵循生_藏品详情标题"
ITEM_CLOSE = "矩阵循生_关闭物品详情"
ITEM_CLOSED = "矩阵循生_物品详情已关闭"
REWARD = "关闭奖励_矩阵循生"
REWARD_CLOSED = "矩阵循生_奖励已关闭"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry,page", [
    ("矩阵循生", "item"),
    ("肉鸽内部", "item"),
    ("贴心搭档_矩阵循生", "item"),
    ("贴心搭档_矩阵循生", "reward"),
    ("贴心搭档_矩阵循生", "collection"),
])
def test_popup_closes_once_before_parent_resumes(tmp_path, language, entry, page):
    state = {"page": page, "remaining": 0}
    clicks, lingering = [], []
    end = "对话事件_矩阵循生" if entry == "肉鸽内部" else "肉鸽内部"

    class Capture(Controller):
        def screencap(self):
            if state["page"].startswith("fading_"):
                state["remaining"] -= 1
                if state["remaining"] == 0:
                    state["page"] = "map"
                else:
                    lingering.append(state["page"])
            return np.zeros((720, 1280, 3), np.uint8)

        def click(self, x, y):
            current = state["page"]
            clicks.append((current, x, y))
            if current == "item":
                assert (x, y) == (1200, 72)
                state.update(page="fading_item", remaining=4)
            elif current == "reward":
                state.update(page="fading_reward", remaining=4)
            elif current == "collection":
                assert (x, y) == (78, 36)
                state["page"] = "map"
            else:
                raise AssertionError(f"Click through a pending popup transition: {current}")
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            current, name = state["page"], argv.node_name
            if name == ITEM_TITLE and current in {"item", "fading_item"}:
                return (195, 80, 170, 55)
            if name == REWARD and current in {"reward", "fading_reward"}:
                return (588, 218, 101, 28)
            if name == COLLECTION_TITLE and current == "collection":
                return (700, 70, 170, 50)
            if name == "返回" and current == "collection":
                return (78, 36, 1, 1)
            if name == end and current == "map":
                return (1003, 627, 163, 55)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R03PopupPage", Page())
    nodes = {}
    for file in (ROOT / "resource/base/pipeline/Roguelike_3").glob("*.jsonc"):
        nodes.update(jsonc.loads(file.read_text(encoding="utf-8")))
    protected = {entry, DETAIL, ITEM_CLOSE, ITEM_CLOSED, REWARD_CLOSED}
    candidates = {ITEM_TITLE, COLLECTION_TITLE, REWARD, "返回", end}
    candidates.update(n.removeprefix("[JumpBack]") for n in nodes[entry]["next"])
    overrides = {
        name: {
            "recognition": {"type": "Custom", "param": {"custom_recognition": "R03PopupPage"}},
            "pre_delay": 0, "post_delay": 0, "rate_limit": 1,
            "timeout": 1000, "on_error": [],
        }
        for name in candidates - protected
    }
    for name in protected | {REWARD}:
        overrides.setdefault(name, {}).update(
            pre_delay=0, post_delay=0, rate_limit=1, timeout=1000, on_error=[]
        )
    overrides[entry].update(recognition="DirectHit", action="DoNothing")
    overrides[end].update(action="StopTask", next=[])
    overrides["返回"].update(action={"type": "Click", "param": {"target": [78, 36]}}, next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(15, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, overrides).wait()
        assert job.succeeded and not expired and state["page"] == "map"
        assert [item[0] for item in clicks] == [page]
        assert bool(lingering) is (page != "collection")
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
