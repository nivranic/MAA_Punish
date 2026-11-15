"""Cage stamina comes from the current list, including its blue segmented bar."""

from pathlib import Path

import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from action.basics.RoleSelection import RoleSelection
from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("width", "height", "count"), [
    (0, 5, 0), (81, 5, 1), (163, 5, 2), (244, 5, 3),
    (64, 5, 0), (244, 12, 0), (20, 5, 0),
])
def test_cage_blue_bar_counts_with_real_native_recognition(tmp_path, width, height, count):
    image = np.full((720, 1280, 3), 120, np.uint8)
    image[174:179, 144:388] = (77, 73, 70)
    image[174:174 + height, 144:144 + width] = (249, 174, 52)
    values = []

    class FrameController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-cage-stamina-bar"
        def get_features(self): return 0
        def screencap(self): return image

    class Probe(CustomAction):
        def run(self, context, argv):
            values.append(RoleSelection()._read_cage_count(context, image, (145, 80, 92, 91)))
            return True

    Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), FrameController(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    assert resource.register_custom_action("CageStaminaProbe", Probe())
    try:
        assert tasker.post_task("CageStaminaProbe", {"CageStaminaProbe": {
            "action": {"type": "Custom", "param": {"custom_action": "CageStaminaProbe"}},
            "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0,
        }}).wait().succeeded
        assert values == [count]
    finally:
        stop_and_wait_idle(tasker)
