"""An exited buff page must return to the map instead of timing out."""

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("case", ["upgrade-and-replace", "replace-only", "already-map"])
def test_buff_uses_upgrade_before_skip_and_unwinds_after_return(tmp_path, language, case):
    root = "宣叙妄响内部"
    upgrade = "选择增益_宣叙妄响"
    replace = "选择增益替换_宣叙妄响"
    confirm = "选择增益处理_宣叙妄响"
    skip = "跳过选择_宣叙妄响"
    map_node = "宣叙妄响内部_reco"
    entry = root if case == "upgrade-and-replace" else replace
    state = {"page": "map" if case == "already-map" else "buff"}
    clicks = []

    class Capture(Controller):
        def click(self, x, y):
            if y < 100:
                clicks.append("skip")
                state["page"] = "map"
            elif state["page"] == "buff":
                clicks.append("upgrade")
                state["page"] = "confirm"
            elif state["page"] == "confirm":
                clicks.append("confirm")
                state["page"] = "map"
            else:
                raise AssertionError((x, y, state))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if page == "buff":
                if name == upgrade and case == "upgrade-and-replace":
                    return (490, 270, 40, 20)
                if name == replace:
                    return (490, 104, 40, 20)
                if name == skip:
                    return (1028, 36, 118, 25)
            if page == "confirm" and name == confirm:
                return (880, 620, 180, 50)
            if page == "map" and name in {map_node, "BuffProbeDone"}:
                return (950, 640, 250, 50)
            return None

    nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc").read_text(encoding="utf-8"))
    candidates = {item.removeprefix("[JumpBack]") for item in nodes[root]["next"]}
    candidates.update([upgrade, replace, confirm, skip, map_node, "BuffProbeDone"])
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01BuffPage"}},
        "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
        "timeout": 150, "rate_limit": 1, "on_error": [],
    } for name in candidates}
    overrides.setdefault(entry, {}).update(recognition={"type": "DirectHit"},
                                           timeout=150, on_error=[])
    if entry == root:
        overrides[root]["next"] = [*nodes[root]["next"], "BuffProbeDone"]
    overrides["BuffProbeDone"]["action"] = {"type": "StopTask"}
    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R01BuffPage", Page())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(entry, overrides).wait()
        assert job.succeeded, (case, clicks, state)
        assert state["page"] == "map"
        assert clicks == ({"upgrade-and-replace": ["upgrade", "confirm"],
                           "replace-only": ["skip"], "already-map": []}[case])
    finally:
        stop_and_wait_idle(tasker)
