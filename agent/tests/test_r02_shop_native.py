"""The full shop heading must be recognized after confirming entry."""

import cv2
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
def test_shop_confirmation_reaches_exit_without_cropping_title(tmp_path, language):
    state = {"page": "confirm"}
    clicks = []

    class Capture(Controller):
        def screencap(self):
            frame = np.zeros((720, 1280, 3), np.uint8)
            if state["page"] == "shop":
                cv2.putText(frame, "SHARK SALE", (620, 111), cv2.FONT_HERSHEY_SIMPLEX,
                            .85, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(frame, "END TRADE", (1000, 668), cv2.FONT_HERSHEY_SIMPLEX,
                            .75, (255, 255, 255), 2, cv2.LINE_AA)
            return frame

        def click(self, x, y):
            # Do not raise across the native callback boundary; assert the
            # observed click sequence and the resolved boxes on the test thread.
            current = state["page"]
            clicks.append((current, x, y))
            if current == "confirm":
                state["page"] = "shop"
            elif current == "shop":
                state["page"] = "map"
            else:
                return False
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if state["page"] == "confirm" and argv.node_name == "底部确定_文字":
                return (600, 510, 80, 30)
            if state["page"] == "map" and argv.node_name in {"事件完成_厄愿潮声", "事件完成_二次确认_厄愿潮声"}:
                return (1022, 630, 225, 50)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R02ShopPage", Page())
    overrides = {name: {
        "pre_delay": 0, "post_delay": 0, "rate_limit": 1, "timeout": 1500, "on_error": [],
    } for name in ["商店确认_厄愿潮声", "商店_厄愿潮声", "结束交易_厄愿潮声",
                   "事件完成_厄愿潮声", "事件完成_二次确认_厄愿潮声"]}
    overrides["商店_厄愿潮声"]["recognition"] = {"param": {"expected": "SHARK SALE"}}
    overrides["结束交易_厄愿潮声"]["recognition"] = {"param": {"expected": "END TRADE"}}
    for name in ["底部确定_文字", "事件完成_厄愿潮声", "事件完成_二次确认_厄愿潮声",
                 "关闭事件奖励_厄愿潮声", "确定", "商店_对话2", "点击继续"]:
        overrides.setdefault(name, {})["recognition"] = {"type": "Custom", "param": {"custom_recognition": "R02ShopPage"}}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task("商店确认_厄愿潮声", overrides).wait()
        assert job.succeeded
        assert [page for page, _, _ in clicks] == ["confirm", "shop"] and state["page"] == "map"
        _, x, y = clicks[0]
        assert 600 <= x < 680 and 510 <= y < 540
        nodes = [tasker.get_node_detail(node_id) for node_id in job.get().node_id_list]
        exit_, = [node for node in nodes if node.name == "结束交易_厄愿潮声"]
        assert exit_.completed and exit_.recognition.hit
        # Native OCR pads the rendered glyphs (e.g. x=996 when drawn at
        # x=1000). A real click within that box is not a fixture failure.
        left, top, width, height = exit_.recognition.box
        _, x, y = clicks[1]
        assert left <= x < left + width and top <= y < top + height
        assert 980 <= x < 1160 and 630 <= y < 690
    finally:
        stop_and_wait_idle(tasker)
