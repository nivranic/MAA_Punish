"""Do not use the generic back button while the roguelike lobby loads."""

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("ready_node", ["揭开帷幕_宣叙妄响_reco", "未完待续_宣叙妄响_文字", None])
def test_enter_waits_for_lobby_even_while_back_is_visible(tmp_path, ready_node):
    entry, lobby = "宣叙妄响", "打开宣叙妄响"
    state = {"page": "menu", "frames": 0, "observed": False}
    clicks = []

    class Capture(Controller):
        def screencap(self):
            if state["page"] == "loading":
                state["frames"] += 1
                if ready_node and state["frames"] >= 5:
                    state["page"] = "ready"
            return super().screencap()

        def click(self, x, y):
            clicks.append(state["page"])
            assert state["page"] == "menu", "Loading must not click the back button"
            state["page"] = "loading"
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == lobby and page == "menu":
                return (420, 470, 140, 45)
            if name == "返回" and page == "loading":
                return (78, 22, 100, 40)
            if name == ready_node and page == "ready":
                state["observed"] = True
                return (920, 610, 240, 90)
            if name == "R01EntryProbeDone" and state["observed"]:
                return (1, 1, 1, 1)
            return None

    nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc").read_text(encoding="utf-8"))
    names = {item.removeprefix("[JumpBack]") for item in nodes[entry]["next"]}
    names.update(nodes[lobby]["next"])
    names.add("R01EntryProbeDone")
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01EntryPage"}},
        "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
        "rate_limit": 1, "timeout": 200, "on_error": [],
    } for name in names}
    overrides[entry] = {
        "recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"},
        "next": [*nodes[entry]["next"], "R01EntryProbeDone"],
        "pre_delay": 0, "post_delay": 0, "timeout": 200, "on_error": [],
    }
    overrides["R01EntryProbeDone"]["action"] = {"type": "StopTask"}
    resource = load_resource(tmp_path)
    assert resource.register_custom_recognition("R01EntryPage", Page())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(entry, overrides).wait()
        assert job.succeeded is bool(ready_node)
        assert clicks == ["menu"]
        assert state["observed"] is bool(ready_node)
    finally:
        stop_and_wait_idle(tasker)
