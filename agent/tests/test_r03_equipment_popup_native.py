"""A set-activation overlay must close before equipment routing resumes."""
import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle

DISMISS = "矩阵循生_关闭套装激活提示"
TITLE = "矩阵循生_套装激活标题"
DETAIL = "矩阵循生_套装具体效果"
EQUIPMENT = "确认装备_矩阵循生"


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", [EQUIPMENT, "矩阵循生"])
@pytest.mark.parametrize("title,detail", [(True, True), (True, False), (False, True), (False, False)])
def test_set_overlay_is_dismissed_before_underlying_equipment(
    tmp_path, language, entry, title, detail,
):
    state = {"popup": True, "closed": False}
    clicks = []

    class Capture(Controller):
        def click(self, x, y):
            if state["popup"]:
                assert (x, y) == (640, 570)
                clicks.append("dismiss")
                state["popup"] = False
            else:
                assert not state["closed"] and 1096 <= x < 1142 and 653 <= y < 680
                clicks.append("confirm")
                state["closed"] = True
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name = argv.node_name
            if name == TITLE and state["popup"] and title:
                return (520, 230, 250, 60)
            if name == DETAIL and state["popup"] and detail:
                return (710, 350, 140, 45)
            if name == "选择装备_矩阵循生" and not state["closed"]:
                return (70, 65, 170, 40)
            # The dimmed confirm label is still readable under the overlay.
            if name == "确定_文字" and not state["closed"]:
                return (1096, 653, 46, 27)
            if not state["popup"] and not state["closed"]:
                if name == "矩阵循生_装备红位编号":
                    return (1194, 295, 32, 53)
                if name in {"矩阵循生_装备红位左角", "矩阵循生_装备红位右角"}:
                    return (1080, 286, 14, 16)
                if name == "肉鸽内部":
                    return (1030, 650, 70, 30)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("SetPopupPage", Page())
    overrides = R03Recruitment.red_equipment_patch()
    for name in (TITLE, DETAIL, "选择装备_矩阵循生", "确定_文字",
                 "矩阵循生_装备红位编号", "矩阵循生_装备红位左角", "矩阵循生_装备红位右角",
                 "矩阵循生_装备余位编号", "肉鸽内部"):
        overrides[name] = {"recognition": {"type": "Custom", "param": {
            "custom_recognition": "SetPopupPage",
        }}}
    if entry == "矩阵循生":
        nodes = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_3/Roguelike_3.jsonc").read_text("utf-8"))
        for value in nodes[entry]["next"]:
            name = value.removeprefix("[JumpBack]")
            if name not in {DISMISS, EQUIPMENT, "肉鸽内部"}:
                overrides.setdefault(name, {})["enabled"] = False
        overrides[entry] = {"action": {"type": "DoNothing"}}
        overrides["肉鸽内部"].update(action={"type": "StopTask"}, next=[])
    for name in {entry, EQUIPMENT, DISMISS, "矩阵循生_红位装备确认"}:
        overrides.setdefault(name, {}).update(
            timeout=150, rate_limit=1, pre_delay=0, post_delay=0,
            post_wait_freezes=0, on_error=[],
        )
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task(entry, overrides).wait()
        expected = title and detail
        assert job.succeeded is expected
        # Closing this post-equip notice returns to the caller for a fresh page
        # dispatch; it must not immediately confirm the dim underlying button.
        assert clicks == (["dismiss"] if expected else [])
        assert state["popup"] is (not expected)
    finally:
        stop_and_wait_idle(tasker)
