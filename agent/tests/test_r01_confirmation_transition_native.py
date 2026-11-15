"""R01 must not click a fading confirmation into the formation's character."""

import jsonc
from pathlib import Path

import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
CONFIRM = "战斗冲突处理_宣叙妄响"
FORMATION = "确认进入战斗冲突"
DETAIL = "意外点击_选择人物"
START = "作战开始"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("open_detail", [False, True])
def test_confirmation_waits_for_formation_and_recovers_character_detail(
    tmp_path, language, open_detail
):
    state = {"page": "confirm", "remaining": 0, "detail_pending": False}
    clicks = []
    lingering_observations = []

    class Controller(CustomController):
        def connect(self): return True
        def request_uuid(self): return "r01-confirmation-transition"
        def get_features(self): return 0

        def screencap(self):
            if state["detail_pending"]:
                state.update(page="detail", detail_pending=False)
            elif state["page"] == "fading":
                state["remaining"] -= 1
                if state["remaining"] <= 0:
                    state["page"] = "formation"
                else:
                    lingering_observations.append(True)
            return np.zeros((720, 1280, 3), np.uint8)

        def click(self, x, y):
            page = state["page"]
            clicks.append(page)
            if page == "confirm":
                state.update(page="fading", remaining=4)
            elif page == "fading":
                # The old self-loop clicks a still recognizable button again.
                state["page"] = "detail"
            elif page == "detail":
                assert (x, y) == (78, 36)
                state["page"] = "formation"
            elif page == "formation":
                state["page"] = "launched"
            else:
                raise AssertionError(page)
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == "底部确定_文字" and page in {"confirm", "fading"}:
                return (603, 517, 82, 28)
            if name == FORMATION and page == "formation":
                if open_detail and "detail" not in clicks:
                    state["detail_pending"] = True
                return (1126, 522, 80, 28)
            if name == DETAIL and page == "detail":
                return (935, 450, 209, 80)
            if name == START and page == "formation":
                return (980, 640, 220, 60)
            return None

    Tasker.set_log_dir(tmp_path / "native")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource = Resource()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    if language != "base":
        assert resource.post_bundle(ROOT / "resource" / language).wait().succeeded
    assert resource.register_custom_recognition("R01TransitionPage", Page())
    nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc").read_text(encoding="utf-8"))
    candidates = {"底部确定_文字", FORMATION, DETAIL, START}
    for name in (CONFIRM, FORMATION):
        candidates.update(item.removeprefix("[JumpBack]") for item in nodes[name]["next"])
    overrides = {
        name: {
            "recognition": {"type": "Custom", "param": {"custom_recognition": "R01TransitionPage"}},
            "pre_delay": 0, "post_delay": 0, "rate_limit": 1,
            "post_wait_freezes": 0, "timeout": 1000, "on_error": [],
        }
        for name in candidates if name != CONFIRM
    }
    overrides[CONFIRM] = {
        "pre_delay": 0, "post_delay": 0, "rate_limit": 1,
        "post_wait_freezes": 0, "timeout": 1000, "on_error": [],
    }
    # End the test at the real start click; no combat implementation is replaced.
    overrides[START].update(action={"type": "Click"}, next=["R01ProbeComplete"])
    overrides["R01ProbeComplete"] = {
        "recognition": {"type": "DirectHit"},
        "action": {"type": "StopTask"}, "next": [], "on_error": [],
    }
    controller = Controller()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(CONFIRM, overrides).wait()
        assert job.succeeded, (state, clicks, lingering_observations)
        assert lingering_observations
        assert clicks == (["confirm", "detail", "formation"] if open_detail else ["confirm", "formation"])
        assert state["page"] == "launched"
    finally:
        stop_and_wait_idle(tasker)
