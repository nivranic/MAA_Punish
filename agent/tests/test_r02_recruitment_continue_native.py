"""Zero remaining recruitment is a step boundary, not the end of R02."""

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["厄愿潮声", "处理招募页_厄愿潮声"])
def test_zero_recruitment_advances_once_and_resumes_adventure(tmp_path, language, entry):
    zero = "招募次数0_厄愿潮声"
    title = "厄愿潮声_招募页标题"
    next_button = "下一步_厄愿潮声"
    complete = "肉鸽内部_厄愿潮声"
    state = {"page": "recruit", "linger": 0}
    clicks = []

    class Capture(Controller):
        def screencap(self):
            if state["page"] == "fading":
                state["linger"] -= 1
                if state["linger"] == 0:
                    state["page"] = "adventure"
            return super().screencap()

        def click(self, x, y):
            assert state["page"] == "recruit", "A fading next button must not be clicked twice"
            assert 1040 <= x < 1200 and 630 <= y < 690
            clicks.append((x, y))
            state.update(page="fading", linger=4)
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            page, name = state["page"], argv.node_name
            if page in {"recruit", "fading"}:
                if name in {zero, title}:
                    return (450, 48, 380, 47)
                if name == next_button:
                    return (1040, 630, 160, 60)
            if page == "adventure" and name == complete:
                return (50, 50, 100, 50)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R02RecruitPage", Page())
    nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_2/Roguelike_2.jsonc").read_text(encoding="utf-8"))
    names = {zero, title, next_button, complete}
    for name in ["厄愿潮声", "处理招募页_厄愿潮声"]:
        names.update(item.removeprefix("[JumpBack]") for item in nodes[name]["next"])
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R02RecruitPage"}},
        "pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
        "timeout": 500, "rate_limit": 1, "on_error": [],
    } for name in names}
    for name in ["厄愿潮声", "处理招募页_厄愿潮声"]:
        overrides[name] = {"recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"},
                           "pre_delay": 0, "post_delay": 0, "timeout": 500, "rate_limit": 1, "on_error": []}
    for name in ["招募完成下一步_厄愿潮声", "离开招募页_厄愿潮声"]:
        overrides[name] = {"pre_delay": 0, "post_delay": 0, "timeout": 500, "rate_limit": 1, "on_error": []}
    overrides[complete].update(action={"type": "StopTask"}, next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(entry, overrides).wait()
        assert job.succeeded, (clicks, state)
        assert state["page"] == "adventure" and len(clicks) == 1
        detail = tasker.get_task_detail(job.job_id)
        visited = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert visited[-1] == complete
        assert "离开招募页_厄愿潮声" in visited
        assert "厄愿潮声_日耗尽停止" not in visited
    finally:
        stop_and_wait_idle(tasker)
