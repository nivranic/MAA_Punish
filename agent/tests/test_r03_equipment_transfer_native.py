"""Leave an optional set transfer through its own confirmation, without moving gear."""
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle

EVENT = "矩阵循生_套装调整"
TITLE = "矩阵循生_套装调整标题"
PROMPT = "矩阵循生_套装调整离开提示"
OVERLAY = "矩阵循生_套装调整提示标题"
BACK = "矩阵循生_退出套装调整"
CONFIRM = "矩阵循生_确认退出套装调整"
GONE = "矩阵循生_套装调整提示已关闭"
BUTTON = "矩阵循生_离开演算确认文字"
MAP = "肉鸽内部"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["矩阵循生", "不祥烙印_矩阵循生"])
@pytest.mark.parametrize("case", ["page", "popup", "no_popup", "chapter", "missing_title", "missing_prompt", "missing_button", "stays_open"])
def test_transfer_exit_preserves_existing_equipment(tmp_path, language, entry, case):
    state = {"page": 0 if case in {"page", "no_popup"} else 1, "entered": False}
    clicks, wrong, expired = [], [], []

    class Capture(Controller):
        def click(self, x, y):
            region = (130, 20, 60, 40) if state["page"] == 0 else (710, 465, 60, 35)
            if case == "chapter" and state["page"] == 2:
                region = (600, 10, 1, 1)
            if not (region[0] <= x < region[0] + region[2] and region[1] <= y < region[1] + region[3]):
                wrong.append((state["page"], x, y))
                return False
            clicks.append("chapter" if case == "chapter" and state["page"] == 2 else ("back" if state["page"] == 0 else "confirm"))
            if case != "stays_open":
                state["page"] = 2 if case == "no_popup" else state["page"] + 1
            return True

    class Page(CustomRecognition):
        def analyze(self, ctx, argv):
            name, page = argv.node_name, state["page"]
            if name == "不祥烙印_标题" and not state["entered"]:
                return (670, 110, 150, 35)
            if page < 2:
                if name == TITLE and case != "missing_title": return (65, 75, 390, 45)
                if name == "返回_文字": return (130, 20, 60, 40)
            if page == 1:
                if name == OVERLAY: return (615, 215, 60, 30)
                if name == PROMPT and case != "missing_prompt": return (420, 330, 430, 40)
                if name == BUTTON and case != "missing_button": return (710, 465, 60, 35)
            if case == "chapter" and page == 2 and name == "转场_矩阵循生": return (1020, 650, 180, 45)
            if page == (3 if case == "chapter" else 2) and name == MAP: return (1003, 627, 163, 55)
            return None

    class Begin(CustomAction):
        def run(self, ctx, argv):
            state["entered"] = True
            return True

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("TransferPage", Page())
    assert resource.register_custom_action("BeginTransfer", Begin())
    patch = {}
    for parent in (entry, EVENT):
        node = resource.get_node_data(parent)
        if node is None: continue
        for item in node["next"]:
            name = item["name"]
            if name not in {EVENT, BACK, CONFIRM, MAP, "转场_矩阵循生"}:
                patch[name] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=150, on_error=[])
    # Model entry from the story once. A permanent DirectHit would create a
    # fake loop through the transfer handler's return-to-story edge.
    patch[entry] = {**timing, "action": {"type": "Custom", "param": {"custom_action": "BeginTransfer"}}}
    for name in (EVENT, BACK, CONFIRM, GONE, "矩阵循生_套装调整点击返回", "矩阵循生_套装调整返回前检查"):
        if resource.get_node_data(name) is not None:
            patch.setdefault(name, {}).update(timing)
    for name in (TITLE, PROMPT, OVERLAY, BUTTON, MAP, "返回_文字", "不祥烙印_标题", "转场_矩阵循生"):
        patch.setdefault(name, {}).update(**timing, recognition={"type": "Custom", "param": {"custom_recognition": "TransferPage"}})
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
        assert not expired and not wrong, wrong
        if case in {"page", "popup", "no_popup", "chapter"}:
            assert job.succeeded and state["page"] == (3 if case == "chapter" else 2)
            assert clicks == {"page": ["back", "confirm"], "popup": ["confirm"], "no_popup": ["back"], "chapter": ["confirm", "chapter"]}[case]
        elif case == "stays_open":
            assert job.failed and clicks == ["confirm"] and state["page"] == 1
        else:
            assert job.failed and not clicks
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
