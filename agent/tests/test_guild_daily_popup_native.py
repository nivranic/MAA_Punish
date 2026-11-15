"""Daily login may appear only after returning from the guild at day rollover."""

from pathlib import Path

import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


RESOURCE = Path(__file__).resolve().parents[2] / "resource"


@pytest.mark.parametrize("language", ["base", "zh_TW", "ja_JP", "en_US"])
@pytest.mark.parametrize("popup", [True, False])
def test_guild_return_closes_delayed_login_before_confirming_home(tmp_path, language, popup):
    phase = ["popup" if popup else "home"]
    clicks = []

    class MemoryController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "guild-daily-popup"
        def get_features(self): return 0
        def screencap(self): return np.zeros((720, 1280, 3), np.uint8)

        def click(self, x, y):
            clicks.append((x, y))
            assert phase[0] == "popup"
            assert (x, y) == (640, 10)
            phase[0] = "home"
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            wanted = {"关闭公告": "popup", "检查主界面2": "home"}
            return (10, 10, 20, 20) if phase[0] == wanted.get(argv.node_name) else None

    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.set_cpu()
    assert resource.post_bundle(RESOURCE / "base").wait().succeeded
    if language != "base":
        assert resource.post_bundle(RESOURCE / language).wait().succeeded
    assert resource.register_custom_recognition("GuildDailyPopupPage", Page())
    controller = MemoryController()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    override = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "GuildDailyPopupPage"}}}
        for name in ("关闭公告", "关闭拟真围剿通知", "月卡续期", "检查主界面2",
                     "关闭奖励_指挥局", "返回", "返回_图标")
    }
    override["检查指定按钮"] = {
        "recognition": "DirectHit", "action": "DoNothing", "pre_delay": 0, "post_delay": 0,
        "post_wait_freezes": {"time": 0}, "timeout": 100, "rate_limit": 0, "on_error": [],
    }
    override["关闭点击空白弹窗"] = {"pre_delay": 0, "post_delay": 0}
    override["检查主界面2"].update({"next": [], "pre_delay": 0, "post_delay": 0})
    try:
        job = tasker.post_task("检查指定按钮", override).wait()
        assert job.succeeded
        assert phase[0] == "home"
        assert clicks == ([(640, 10)] if popup else [])
        names = [tasker.get_node_detail(node_id).name for node_id in job.get().node_id_list]
        assert names[-1] == "检查主界面2"
        assert ("关闭点击空白弹窗" in names) is popup
    finally:
        stop_and_wait_idle(tasker)
