"""Startup can leave the confirmed harbor map without terminating its run."""

from threading import Timer

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.ClickAnchor import ClickAnchor
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("harbor,leave", [(True, True), (True, False),
                                         (False, True), (False, False)])
def test_startup_leaves_only_confirmed_harbor_map(tmp_path, language, harbor, leave):
    state = {"page": "map"}
    clicks = []
    home_region = (196, 14, 84, 46) if language == "en_US" else (285, 10, 75, 55)

    class Capture(Controller):
        def click(self, x, y):
            if state["page"] == "map":
                state["page"] = "menu"
            else:
                state["page"] = "home"
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            matches = {
                "港口异常点_寒境曙光": (state["page"] == "map" and harbor, (80, 30, 220, 75)),
                "退出_寒境曙光": (state["page"] == "map" and leave, (1120, 10, 110, 55)),
                "返回主菜单_文字": (state["page"] == "menu", home_region),
                "检查主界面2": (state["page"] == "home", (50, 50, 50, 50)),
            }
            hit, box = matches.get(argv.node_name, (False, None))
            return box if hit else None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R04StartupPage", Page())
    assert resource.register_custom_action("ClickAnchor", ClickAnchor())
    startup = jsonc.loads((ROOT / "resource/base/pipeline/Start_up.jsonc").read_text(encoding="utf-8"))
    preserved = {"返回主菜单", "启动_从寒境港口返回"}
    names = {name.removeprefix("[JumpBack]") for name in startup["启动"]["next"]} - preserved
    names.update(["港口异常点_寒境曙光", "退出_寒境曙光", "返回主菜单_文字", "检查主界面2"])
    overrides = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "R04StartupPage"}},
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
        assert job.succeeded is (harbor and leave)
        assert len(clicks) == (2 if harbor and leave else 0)
        assert state["page"] == ("home" if harbor and leave else "map")
        if clicks:
            x, y = clicks[0]
            assert 1120 <= x < 1230 and 10 <= y < 65
            x, y, w, h = home_region
            assert clicks[-1] == (x + w, y + h // 2)
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
