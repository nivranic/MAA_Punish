"""A mandatory story answer must interrupt the disabled skip-button loop."""

from threading import Timer
import jsonc

import cv2
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


CHOICE = "剧情选项_雾夜镇魂曲"
SKIP = "跳过剧情_雾夜镇魂曲"


def story_frame(answer=True, skip=True, text=True):
    frame = np.full((720, 1280, 3), 30, np.uint8)
    if skip:
        cv2.putText(frame, "SKIP", (1080, 53), cv2.FONT_HERSHEY_SIMPLEX,
                    .9, (130, 130, 130), 2)
    if answer:
        frame[330:390, 755:1235] = 255
        if text:
            cv2.putText(frame, "WHERE SHALL WE GO?", (775, 370), cv2.FONT_HERSHEY_SIMPLEX,
                        .75, (0, 0, 0), 2)
    return frame


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["雾夜镇魂曲", "雾夜镇魂曲内部"])
@pytest.mark.parametrize("mandatory", [True, False])
def test_story_choice_clears_before_skip_resumes(tmp_path, language, entry, mandatory):
    state = {"page": "choice" if mandatory else "story", "transition_frames": 0}
    clicks = []

    class Capture(Controller):
        def screencap(self):
            if state["page"] == "transition":
                state["transition_frames"] += 1
                if state["transition_frames"] >= 4:
                    state["page"] = "story"
            return story_frame(answer=state["page"] in {"choice", "transition"})

        def click(self, x, y):
            clicks.append((state["page"], x, y))
            if 755 <= x < 1235 and 330 <= y < 390:
                state.update(page="transition", transition_frames=0)
            elif y < 75 and state["page"] == "story":
                state["page"] = "confirm"
            elif 923 <= x < 1129 and 531 <= y < 676 and state["page"] == "confirm":
                state["page"] = "done"
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == "确认跳过" and state["page"] == "confirm":
                return (980, 610, 80, 40)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R06StoryPage", Page())
    data = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_6/Roguelike_6.json").read_text(encoding="utf-8"))
    names = {n.removeprefix("[JumpBack]") for parent in ["雾夜镇魂曲", "雾夜镇魂曲内部"] for n in data[parent]["next"]}
    names -= {SKIP, entry}
    names.add("确认跳过")
    overrides = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "R06StoryPage"}},
               "pre_delay": 0, "post_delay": 0, "timeout": 1000, "on_error": []}
        for name in names
    }
    overrides[entry] = {"recognition": "DirectHit", "pre_delay": 0, "post_delay": 0, "timeout": 1000, "on_error": []}
    overrides["跳过剧情"] = {"recognition": {"param": {"expected": "SKIP"}}}
    for name in [SKIP, CHOICE, "剧情选项消失_雾夜镇魂曲"]:
        overrides[name] = {"pre_delay": 0, "post_delay": 0, "timeout": 1000, "rate_limit": 1, "on_error": []}
    overrides["确认跳过"].update(next=["R06StoryDone"])
    overrides["R06StoryDone"] = {"recognition": "DirectHit", "action": "StopTask", "next": [], "on_error": []}
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(15, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, overrides).wait()
        assert not expired and job.succeeded and state["page"] == "done"
        answer_clicks = [c for c in clicks if 330 <= c[2] < 390]
        assert len(answer_clicks) == (1 if mandatory else 0)
        assert not any(page == "transition" for page, _, _ in clicks)
        assert clicks[-1][0] == "confirm"
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("skip,answer,text", [(False, True, True), (True, False, True), (True, True, False)])
def test_story_choice_rejects_incomplete_page_evidence(tmp_path, skip, answer, text):
    class Capture(Controller):
        def screencap(self):
            return story_frame(answer, skip, text)

    resource, controller, tasker = load_resource(tmp_path), Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task(CHOICE, {
            "跳过剧情": {"recognition": {"param": {"expected": "SKIP"}}},
            CHOICE: {"action": "DoNothing", "next": [], "on_error": [], "timeout": 100},
        }).wait()
        assert not job.succeeded
    finally:
        stop_and_wait_idle(tasker)
