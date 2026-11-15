"""The support power reader uses digits beside the matched portrait, in all bundles."""
import cv2
import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.tasker import Tasker

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("text,expected,y", [("5400", 5400, 395), ("600", 600, 240), ("", None, 395)])
def test_power_is_read_relative_to_the_candidate_row(tmp_path, language, text, expected, y):
    frame = np.full((720, 1280, 3), 220, dtype=np.uint8)
    cv2.putText(frame, text, (309, y + 78), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (60, 60, 60), 1, cv2.LINE_AA)
    # A different row's larger value must never be used for this candidate.
    cv2.putText(frame, "9800", (309, 600), cv2.FONT_HERSHEY_SIMPLEX,
                .65, (60, 60, 60), 1, cv2.LINE_AA)
    observed = []

    class Capture(Controller):
        def screencap(self): return frame
        def click(self, *_): raise AssertionError("Power reading sends no input")

    class Probe(CustomAction):
        def run(self, ctx, argv):
            observed.append(R03Recruitment.read_power(ctx, frame, (142, y, 82, 82)))
            return True

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_action("ReadPower", Probe())
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    try:
        job = tasker.post_task("ReadPower", {"ReadPower": {
            "recognition": "DirectHit", "action": {"type": "Custom", "param": {"custom_action": "ReadPower"}},
            "pre_delay": 0, "post_delay": 0, "next": [],
        }}).wait()
        assert job.succeeded and observed == [expected]
    finally:
        stop_and_wait_idle(tasker)
