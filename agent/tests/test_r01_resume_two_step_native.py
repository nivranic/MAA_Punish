"""Resuming a run in progress takes two clicks, and the run goes back to the main loop."""

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


RESUME = "未完待续_宣叙妄响"
SECOND = "续玩_宣叙妄响"
SETTLE = "就此收场"
HANDOVER = "宣叙妄响内部_reco"
ENTRY, PROBE = "R01ResumeEntry", "R01ResumeInRunProbe"
# Boxes measured on frames recorded on the device on 2026-11-28.
LOBBY_LABEL = (927, 630, 120, 36)   # landing page: the single 「未完待续」 (button x 930-1045)
PANEL_RESUME = (1012, 630, 96, 32)  # run info panel, bottom right: 「未完待续」 (button x 983-1150)
PANEL_SETTLE = (854, 631, 84, 35)   # run info panel, left of it: 「至此收场」 (button x 848-965)
RUN_FRAMES = 80                     # the map stays on screen this long; a list that never lets go ends here


def nodes():
    path = ROOT / "resource/base/pipeline/Roguelike_1/Roguelike_1.jsonc"
    return jsonc.loads(path.read_text(encoding="utf-8"))


def encloses(roi, box):
    x, y, w, h = roi
    bx, by, bw, bh = box
    return x <= bx and y <= by and bx + bw <= x + w and by + bh <= y + h


def run_resume(tmp_path, candidates):
    """Landing page -> run info panel -> map, with the production resume list or a variant of it."""
    state = {"page": "lobby", "frames": 0, "handover_hits": 0, "observed": False, "stray": False}
    clicks = []

    class Pages(Controller):
        def screencap(self):
            if state["page"] == "run":
                state["frames"] += 1
                if state["frames"] >= RUN_FRAMES:
                    state["page"] = "map_gone"
            return super().screencap()

        def click(self, x, y):
            clicks.append((state["page"], x))
            if state["page"] == "lobby":
                state["page"] = "panel"    # the landing page's button opens the run's info panel
            elif state["page"] == "panel":
                state["page"] = "run" if x >= 983 else "settled"
            else:
                state["stray"] = True      # nothing may click while the run is on screen
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            name, page = argv.node_name, state["page"]
            if name == RESUME and page == "lobby":
                return LOBBY_LABEL
            if page == "panel":
                if name == SECOND:
                    return PANEL_RESUME
                if name == SETTLE:
                    return PANEL_SETTLE
            if page == "run":
                if name == HANDOVER:
                    state["handover_hits"] += 1
                    return (960, 600, 300, 100)
                if name == PROBE:
                    state["observed"] = True
                    return (1, 1, 1, 1)
            return None

    speed = {"pre_delay": 0, "post_delay": 0, "post_wait_freezes": 0,
             "rate_limit": 1, "timeout": 200, "on_error": []}
    overrides = {name: {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01ResumePage"}},
        **speed,
    } for name in {RESUME, *(item.removeprefix("[JumpBack]")
                              for item in nodes()[RESUME]["next"])}}
    overrides[RESUME]["next"] = candidates
    # The entry stands in for the main list, which reaches the resume node as a [JumpBack] candidate.
    overrides[ENTRY] = {
        "recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"},
        "next": [f"[JumpBack]{RESUME}", PROBE],
        **speed,
    }
    overrides[PROBE] = {
        "recognition": {"type": "Custom", "param": {"custom_recognition": "R01ResumePage"}},
        "action": {"type": "StopTask"}, "next": [],
        **speed,
    }
    resource = load_resource(tmp_path)
    assert resource.register_custom_recognition("R01ResumePage", Page())
    controller, tasker = Pages(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    try:
        job = tasker.post_task(ENTRY, overrides).wait()
        return job, clicks, state
    finally:
        stop_and_wait_idle(tasker)


def test_resume_clicks_the_panel_button_and_hands_the_run_to_the_main_loop(tmp_path):
    job, clicks, state = run_resume(tmp_path, nodes()[RESUME]["next"])
    assert job.succeeded
    assert [page for page, _ in clicks] == ["lobby", "panel"]
    lobby_x, panel_x = clicks[0][1], clicks[1][1]
    assert LOBBY_LABEL[0] <= lobby_x < LOBBY_LABEL[0] + LOBBY_LABEL[2]
    assert panel_x >= 983, "the second click must be the right-hand 「未完待续」, not 「至此收场」"
    assert state["page"] == "run" and not state["stray"]
    assert state["observed"], "the resume list must end so the main loop can play the run"
    assert state["handover_hits"] == 1


def test_without_the_second_step_the_list_settled_the_run(tmp_path):
    old = [item for item in nodes()[RESUME]["next"]
           if item.removeprefix("[JumpBack]") != SECOND]
    job, clicks, state = run_resume(tmp_path, old)
    assert not job.succeeded and not state["observed"]
    assert [page for page, _ in clicks] == ["lobby", "panel"]
    assert clicks[1][1] < 983 and state["page"] == "settled"


def test_in_run_hand_over_cannot_carry_jump_back(tmp_path):
    old = [f"[JumpBack]{item}" if item == HANDOVER else item for item in nodes()[RESUME]["next"]]
    job, clicks, state = run_resume(tmp_path, old)
    assert not job.succeeded and not state["observed"]
    assert [page for page, _ in clicks] == ["lobby", "panel"] and not state["stray"]
    assert state["handover_hits"] >= 3, "the list kept re-matching the map instead of ending"


def test_resume_list_keeps_the_second_step_ahead_of_settle_and_the_hand_over_plain():
    graph = nodes()
    candidates = graph[RESUME]["next"]
    assert candidates.index(f"[JumpBack]{SECOND}") < candidates.index(f"[JumpBack]{SETTLE}")
    # [JumpBack] restarts this list; a run on screen would never be handed to the main loop.
    assert HANDOVER in candidates and f"[JumpBack]{HANDOVER}" not in candidates
    roi = graph[f"{SECOND}_文字"]["recognition"]["param"]["roi"]
    assert encloses(roi, PANEL_RESUME) and not encloses(roi, LOBBY_LABEL)


@pytest.mark.parametrize("language", ["zh_TW", "ja_JP"])
def test_second_step_label_is_localized_like_the_first(language):
    path = ROOT / "resource" / language / "pipeline/Roguelike_1/Roguelike_1.jsonc"
    overlay = jsonc.loads(path.read_text(encoding="utf-8"))
    assert overlay[f"{SECOND}_文字"] == overlay[f"{RESUME}_文字"]
