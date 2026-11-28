"""A run that ran out of 返场 ends on its settlement page; the task leaves it and starts the next run itself.

The pages are stand-ins; the nodes and their lists are the production ones. MaaFramework keeps one current list and
a stack of [JumpBack] owners: a chain that reaches a node without next pops that stack, and an empty stack ends the
task. So the hand-over to the main list has to be explicit (next: ["宣叙妄响"]), which is what the second control shows.
"""

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


DEFEAT = "失败_宣叙妄响"
EXIT = "退出_肉鸽"
LOBBY = "宣叙妄响主界面_reco"
MAIN = "宣叙妄响"
CURTAIN = "揭开帷幕_宣叙妄响"
CURTAIN_RECO = "揭开帷幕_宣叙妄响_reco"
IN_RUN = "宣叙妄响内部"
ENTRY, PROBE = "R01EndingEntry", "R01EndingNewRunProbe"
# Boxes measured on frames recorded on the device on 2026-11-28 (run c-01).
DEFEAT_LABEL = (276, 196, 256, 67)    # defeat page: 「作战失败」
EXIT_BUTTON = (1037, 638, 170, 52)    # settlement page, bottom right: its single button 「退出」
EXIT_LABEL = (1050, 648, 49, 28)      # ... and the word itself, which is all 退出_文字 sees
CURTAIN_LABEL = (913, 562, 250, 147)  # landing page without a run: 「揭开帷幕」


def nodes():
    path = ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc"
    return jsonc.loads(path.read_text(encoding="utf-8"))


def plain_names(items):
    return [item.removeprefix("[JumpBack]") for item in items]


def encloses(roi, box):
    x, y, w, h = roi
    bx, by, bw, bh = box
    return x <= bx and y <= by and bx + bw <= x + w and by + bh <= y + h


def run_ending(tmp_path, candidates, lobby_sees_landing):
    """Defeat page -> settlement page -> landing page without a run -> the next run, from the in-run list."""
    state = {"page": "defeat", "observed": False, "stray": False}
    clicks = []

    class Pages(Controller):
        def click(self, x, y):
            page = state["page"]
            clicks.append((page, x, y))
            if page == "defeat":
                state["page"] = "settled"   # the countdown is up and no 返场 is left: the run is over
            elif page == "settled" and encloses(EXIT_BUTTON, (x, y, 1, 1)):
                state["page"] = "landing"
            elif page == "landing" and encloses(CURTAIN_LABEL, (x, y, 1, 1)):
                state["page"] = "run"
            else:
                state["stray"] = True      # nothing else may be clicked on any of these pages
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            # The in-run list is the one polling when the battle ends; it stands in for it here.
            if name == IN_RUN and page in ("defeat", "run"):
                return (945, 586, 335, 134)
            if page == "defeat" and name == DEFEAT:
                return DEFEAT_LABEL
            if page == "settled" and name == EXIT:
                return EXIT_LABEL
            if page == "landing":
                if name == LOBBY and lobby_sees_landing:
                    return CURTAIN_LABEL
                if name in (CURTAIN_RECO, CURTAIN):
                    return CURTAIN_LABEL
            if page == "run" and name == PROBE:
                state["observed"] = True
                return (1, 1, 1, 1)
            return None

    speed = {"pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
             "rate_limit": 1, "timeout": 200, "on_error": []}
    graph = nodes()
    names = {DEFEAT, IN_RUN, LOBBY, EXIT, CURTAIN, CURTAIN_RECO,
             *plain_names(candidates), *plain_names(graph[IN_RUN]["next"]), *plain_names(graph[MAIN]["next"])}
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01EndingPage"}},
        **speed,
    } for name in names}
    overrides[DEFEAT]["next"] = candidates
    overrides[IN_RUN]["next"] = [PROBE, *graph[IN_RUN]["next"]]
    # The main node keeps its DirectHit; its own action is the agent's Custom PPOverride, which the
    # test resource does not register.
    overrides[MAIN] = {"action": {"type": "DoNothing"}, **speed}
    # The entry stands in for whatever reached the in-run list before the battle; nothing is stacked above it.
    overrides[ENTRY] = {
        "recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"}, "next": [IN_RUN],
        **speed,
    }
    overrides[PROBE] = {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01EndingPage"}},
        "action": {"type": "StopTask"}, "next": [],
        **speed,
    }
    resource = load_resource(tmp_path)
    assert resource.register_custom_recognition("R01EndingPage", Page())
    controller, tasker = Pages(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(ENTRY, overrides).wait()
        return job, clicks, state
    finally:
        stop_and_wait_idle(tasker)


def test_settlement_page_is_left_and_the_next_run_starts_in_the_same_task(tmp_path):
    job, clicks, state = run_ending(tmp_path, nodes()[DEFEAT]["next"], lobby_sees_landing=True)
    assert job.succeeded and state["observed"] and not state["stray"]
    assert [page for page, _, _ in clicks] == ["defeat", "settled", "landing"]
    assert encloses(EXIT_BUTTON, (clicks[1][1], clicks[1][2], 1, 1)), "the settlement page's 「退出」"
    assert encloses(CURTAIN_LABEL, (clicks[2][1], clicks[2][2], 1, 1)), "the landing page's 「揭开帷幕」"
    assert state["page"] == "run"


def test_without_the_exit_the_task_stopped_on_the_settlement_page(tmp_path):
    old = [item for item in nodes()[DEFEAT]["next"] if item.removeprefix("[JumpBack]") != EXIT]
    job, clicks, state = run_ending(tmp_path, old, lobby_sees_landing=True)
    assert not job.succeeded and not state["observed"]
    assert [page for page, _, _ in clicks] == ["defeat"] and state["page"] == "settled"


def test_a_bare_landing_reco_ends_the_task_when_nothing_is_stacked(tmp_path):
    production = nodes()[DEFEAT]["next"]
    bare = [production[0], CURTAIN_RECO, *production[1:]]
    job, clicks, state = run_ending(tmp_path, bare, lobby_sees_landing=False)
    assert job.succeeded and not state["observed"], "the task ended on the landing page, run not started"
    assert [page for page, _, _ in clicks] == ["defeat", "settled"] and state["page"] == "landing"


def test_defeat_list_exits_the_settlement_page_through_the_landing_hand_over():
    graph = nodes()
    candidates = graph[DEFEAT]["next"]
    assert candidates[0] == LOBBY and f"[JumpBack]{LOBBY}" not in candidates
    assert f"[JumpBack]{EXIT}" in candidates
    # A run still in progress goes back to its in-run page first; 「退出」 comes behind that.
    assert candidates.index("[JumpBack]宣叙妄响内部_reco2") < candidates.index(f"[JumpBack]{EXIT}")
    assert CURTAIN_RECO not in plain_names(candidates)
    lobby = graph[LOBBY]
    assert lobby["next"] == [MAIN]
    assert sorted(lobby["recognition"]["param"]["any_of"]) == ["揭开帷幕_宣叙妄响_文字", "未完待续_宣叙妄响_文字"]
    assert f"[JumpBack]{CURTAIN}" in graph[MAIN]["next"]


@pytest.mark.parametrize("language", ["zh_TW", "ja_JP"])
def test_both_landing_labels_are_localized(language):
    path = ROOT / "resource" / language / "pipeline/Roguelike_1/Roguelike_1.jsonc"
    overlay = jsonc.loads(path.read_text(encoding="utf-8"))
    for label in ("未完待续_宣叙妄响_文字", "揭开帷幕_宣叙妄响_文字"):
        # zh_TW overlays the OCR param itself, ja_JP the whole recognition.
        expected = overlay[label].get("recognition", {}).get("param", overlay[label])["expected"]
        assert expected
