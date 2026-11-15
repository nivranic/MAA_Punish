"""Startup leaves a confirmed fog event without selecting or ending it."""

from threading import Timer

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("event,back", [(True, True), (True, False),
                                       (False, True), (False, False)])
def test_startup_leaves_only_confirmed_fog_event(tmp_path, language, event, back):
    state = {"page": "event"}
    clicks = []

    class Capture(Controller):
        def click(self, x, y):
            state["page"] = "menu" if state["page"] == "event" else "home"
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            matches = {
                "雾夜镇魂曲内部条件1": (state["page"] == "event" and event, (280, 80, 70, 60)),
                "返回_文字": (state["page"] == "event" and back, (130, 20, 60, 40)),
                "返回主菜单_文字": (state["page"] == "menu", (285, 10, 75, 55)),
                "检查主界面2": (state["page"] == "home", (50, 50, 50, 50)),
            }
            hit, box = matches.get(argv.node_name, (False, None))
            return box if hit else None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R06StartupPage", Page())
    startup = jsonc.loads((ROOT / "resource/base/pipeline/Start_up.jsonc").read_text(encoding="utf-8"))
    preserved = {"返回主菜单", "启动_从雾夜事件返回"}
    names = {name.removeprefix("[JumpBack]") for name in startup["启动"]["next"]} - preserved
    names.update(["雾夜镇魂曲内部条件1", "返回_文字", "返回主菜单_文字", "检查主界面2"])
    overrides = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "R06StartupPage"}},
               "pre_delay": 0, "post_delay": 0, "timeout": 100, "rate_limit": 1, "on_error": []}
        for name in names
    }
    for name in preserved | {"启动"}:
        overrides[name] = {"pre_delay": 0, "post_delay": 0, "timeout": 100, "rate_limit": 1, "on_error": []}
    overrides["检查主界面2"].update(action={"type": "StopTask"}, next=[])
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
        job = tasker.post_task("启动", overrides).wait()
        assert not expired
        assert job.succeeded is (event and back)
        assert len(clicks) == (2 if event and back else 0)
        assert state["page"] == ("home" if event and back else "event")
        if clicks:
            x, y = clicks[0]
            assert 130 <= x < 190 and 20 <= y < 60
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
