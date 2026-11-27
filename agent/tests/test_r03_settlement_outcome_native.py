"""Native routing regressions for R03 success, collapse and unbound statistics."""

import json
from threading import Timer

import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.PPOverride import PPOverride
from action.basics.r03_unconfirmed_settlement import R03UnconfirmedSettlement
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle

ROOT_ENTRY = "矩阵循生"
SUCCESS_TITLE = "矩阵循生_模拟演算完成"
SUCCESS_STOP = "矩阵循生_完成结算停止"
UNBOUND = "矩阵循生_未知来源统计"
COLLAPSE = "链路崩溃"
START = "启程_矩阵循生"
SEED = "R03SettlementAlreadyPolling"
ROWS = {
    "矩阵循生_完成提示标题": ({"success_splash"}, (520, 372, 240, 42)),
    "矩阵循生_完成藏品标题": ({"success_collection"}, (535, 75, 215, 44)),
    "链路崩溃_文字": ({"failure_splash", "failure_collection"}, (545, 370, 190, 42)),
    "点击继续": ({"failure_splash", "success_splash"}, (595, 670, 90, 24)),
    "矩阵循生_完成结算下一页": (
        {"success_collection", "failure_collection", "members"}, (995, 631, 95, 30)
    ),
    "矩阵循生_完成统计标题": ({"stats", "stats_loading"}, (102, 95, 125, 38)),
    "矩阵循生_完成统计按钮": ({"stats"}, (1005, 631, 58, 30)),
    START: ({"hub"}, (1085, 635, 112, 38)),
    # `下一页` also matches `完成`. Keep this competing fallback real.
    "下一页": ({"stats"}, (1005, 631, 58, 30)),
}
ZONES = {
    "failure_splash": (595, 670, 90, 24), "success_splash": (595, 670, 90, 24),
    "failure_collection": (995, 631, 95, 30), "success_collection": (995, 631, 95, 30),
    "members": (995, 631, 95, 30), "stats": (1005, 631, 58, 30),
    "hub": (1085, 635, 112, 38),
}


def native_replay(tmp_path, language, pages, owner=ROOT_ENTRY, *, start_leads_to_next_game=False):
    resource = load_resource(tmp_path, language)
    assert resource.register_custom_action("PPOverride", PPOverride())
    assert resource.register_custom_action("R03UnconfirmedSettlement", R03UnconfirmedSettlement())

    page_index = 0
    clicks, unexpected, expired = [], [], []

    class Capture(Controller):
        def click(self, x, y):
            nonlocal page_index
            page = pages[page_index]
            clicks.append((page, x, y))
            if page not in ZONES or page_index == len(pages) - 1:
                unexpected.append(clicks[-1])
                return False
            bx, by, w, h = ZONES[page]
            if not (bx <= x < bx + w and by <= y < by + h):
                unexpected.append(clicks[-1])
                return False
            page_index += 1
            return True

    class Pages(CustomRecognition):
        def analyze(self, context, argv):
            expected, box = ROWS[argv.node_name]
            if pages[page_index] in expected:
                return box
            return None

    assert resource.register_custom_recognition("R03SettlementReplayPages", Pages())
    if owner != ROOT_ENTRY:
        candidates = resource.get_node_data(owner)["next"]
        assert candidates
    task = json.loads((ROOT / "tasks/矩阵循生.json").read_bytes())["task"][0]
    patch = task["pipeline_override"]
    entry = task["entry"]
    if owner != ROOT_ENTRY:
        entry = SEED
        patch[entry] = {"next": candidates, "on_error": []}
    timings = {"rate_limit": 1, "pre_delay": 0, "post_delay": 0, "timeout": 300}
    for node in (*ROWS, entry, ROOT_ENTRY, COLLAPSE, SUCCESS_TITLE,
                 "矩阵循生_完成结算分派", "矩阵循生_完成提示继续", "矩阵循生_完成结算统计",
                 "矩阵循生_败局提示继续", "矩阵循生_败局结算统计", "矩阵循生_败局回枢纽"):
        patch.setdefault(node, {}).update(timings)
    for node in ROWS:
        patch[node]["recognition"] = {
            "type": "Custom", "param": {"custom_recognition": "R03SettlementReplayPages"}
        }
    patch["覆盖布局菜单"] = {"enabled": False}
    # Do not simulate an entire new adventure. Only the test with two runs scripts the
    # next pages after the production Start action; other cases must never click Start.
    if start_leads_to_next_game:
        patch[START]["next"] = [SUCCESS_TITLE]
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(8, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task(entry, patch).wait()
        details = [tasker.get_node_detail(i) for i in job.get().node_id_list]
        return job.succeeded, [n.name for n in details], [n.completed for n in details], clicks, unexpected, expired
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("language", ["base", "zh_TW"])
@pytest.mark.parametrize("owner", [ROOT_ENTRY, "肉鸽内部", "编队完成_矩阵循生", "矩阵循生_区域选择"])
@pytest.mark.parametrize("statistics", ["stats", "stats_loading"])
def test_statistics_without_this_runs_title_cannot_click_or_stop_as_success(tmp_path, language, owner, statistics):
    success, names, completed, clicks, unexpected, expired = native_replay(
        tmp_path, language, [statistics, "hub"], owner
    )
    assert not expired and not unexpected
    assert not success and names[-1] == UNBOUND and completed[-1] is False, (success, names, clicks)
    assert all(names)  # Named FAILED history must not create an anonymous App queue-abort marker.
    assert not clicks and not set(names) & {SUCCESS_TITLE, SUCCESS_STOP, START, "返回主菜单_err"}


@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_real_completion_title_allows_statistics_and_stops_at_hub(tmp_path, language):
    pages = ["success_splash", "success_collection", "members", "stats", "hub"]
    success, names, _, clicks, unexpected, expired = native_replay(tmp_path, language, pages)
    assert success and not unexpected and not expired
    assert names[-1] == SUCCESS_STOP and names.count(SUCCESS_TITLE) == 1
    assert START not in names and UNBOUND not in names
    assert [page for page, _, _ in clicks] == pages[:-1]


@pytest.mark.parametrize("first", ["failure_splash", "failure_collection"])
@pytest.mark.parametrize("owner", [ROOT_ENTRY, "肉鸽内部"])
@pytest.mark.parametrize("language", ["base", "zh_TW"])
def test_collapse_uses_its_own_tail_then_preserves_normal_restart_policy(tmp_path, language, owner, first):
    failure = ["failure_splash", "failure_collection", "members", "stats", "hub"]
    if first == "failure_collection":
        failure = failure[1:]
    pages = failure + ["success_splash", "success_collection", "members", "stats", "hub"]
    success, names, _, clicks, unexpected, expired = native_replay(
        tmp_path, language, pages, owner, start_leads_to_next_game=True
    )
    assert not expired and not unexpected and success, (names, clicks, unexpected)
    assert [page for page, _, _ in clicks] == pages[:-1]
    assert names.count(COLLAPSE) == names.count(START) == 1
    assert names.index(COLLAPSE) < names.index(START) < names.index(SUCCESS_TITLE)
    assert names.count(SUCCESS_STOP) == 1 and names[-1] == SUCCESS_STOP
    assert UNBOUND not in names and "返回主菜单_err" not in names


@pytest.mark.parametrize(("pages", "attempts"), [
    (["failure_splash"], ["failure_splash"]),  # controller rejects a guarded click
    (["failure_splash", "stats_loading"], ["failure_splash"]),  # no submit button
    (["failure_collection", "members"], ["failure_collection", "members"]),  # next page rejected
])
def test_collapse_never_resumes_or_cleans_up_when_tail_is_unconfirmed(tmp_path, pages, attempts):
    success, names, _, clicks, _, expired = native_replay(tmp_path, "base", pages)
    assert not success and not expired, (names, clicks)
    assert COLLAPSE in names and not set(names) & {SUCCESS_TITLE, SUCCESS_STOP, START, "返回主菜单_err"}
    assert [page for page, _, _ in clicks] == attempts
