"""Wait for a sale popup instead of repeatedly toggling the same inventory item."""

from threading import Timer
import re

import cv2
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("popup", ["delayed", "already_open", "missing"])
@pytest.mark.parametrize("item_kind", ["rune", "skill"])
def test_inventory_item_is_opened_once_then_sold_when_visible(tmp_path, language, popup, item_kind):
    state = {"page": "open" if popup == "already_open" else "inventory", "frames": 0}
    clicks = []
    button_text = "SELL" if item_kind == "rune" else "DROP"

    class Capture(Controller):
        def screencap(self):
            frame = np.zeros((720, 1280, 3), np.uint8)
            if state["page"] == "opening":
                state["frames"] += 1
                if popup != "missing" and state["frames"] >= 4:
                    state["page"] = "open"
            if state["page"] == "open":
                cv2.putText(frame, button_text, (455, 552), cv2.FONT_HERSHEY_SIMPLEX,
                            .8, (255, 255, 255), 2)
            return frame

        def click(self, x, y):
            clicks.append((state["page"], x, y))
            if 590 <= x <= 630 and 620 <= y <= 675:
                # Another icon tap restarts the animation before its text appears.
                state.update(page="opening", frames=0)
            elif state["page"] == "open" and 439 <= x < 601 and 505 <= y < 574:
                state["page"] = "sold"
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == "背包已满_神寂启示录":
                return (510, 335, 280, 55)
            return None

    resource = load_resource(tmp_path, language)
    patterns = resource.get_node_data("出售_神寂启示录")["recognition"]["param"]["expected"]
    if language == "en_US":
        observed_text = "Sell" if item_kind == "rune" else "Discard"
    else:
        observed_text = "出售" if item_kind == "rune" else "廢棄" if language == "zh_TW" else "废弃"
    assert any(re.search(pattern, observed_text) for pattern in patterns)
    assert not any(re.search(pattern, "Equip 装备") for pattern in patterns)
    assert resource.register_custom_recognition("R05InventoryPage", Page())
    overrides = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "R05InventoryPage"}}}
        for name in ["背包已满_神寂启示录", "选择界面未关闭"]
    }
    for name in ["背包已满_神寂启示录", "点击多余技能_神寂启示录", "出售_神寂启示录"]:
        overrides.setdefault(name, {}).update(pre_delay=0, post_delay=0, rate_limit=1,
                                              timeout=1000, on_error=[])
    overrides["出售_神寂启示录"]["recognition"] = {"param": {"expected": button_text}}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(15, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task("背包已满_神寂启示录", overrides).wait()
        assert not expired
        assert job.succeeded is (popup != "missing")
        assert len(clicks) == (2 if popup == "delayed" else 1)
        assert state["page"] == ("opening" if popup == "missing" else "sold")
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
