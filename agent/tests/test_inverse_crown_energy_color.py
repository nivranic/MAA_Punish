"""Native color predicates on synthetic gauges; not physical readiness proof."""

import json
from pathlib import Path
from threading import Timer

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
RESOURCE_PATH = ROOT / "resource/base/pipeline/Auto_Battle/Check_Characters_Skill/InverseCrown.jsonc"
FULL_NODE = "检查逆冕能量即将满"


class ImageOnlyController(CustomController):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def connect(self): return True
    def request_uuid(self): return "synthetic-inverse-energy-color"
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)

    def reject(self, *args):
        self.inputs.append(args)
        return False

    click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = reject


def synthetic_gauge(node, bright_pixels, bright_hsv, tail_hsv):
    """Generate color blocks, without using a screenshot or reimplementing matching."""
    x, y, width, height = node["recognition"]["param"]["roi"]
    bright = cv2.cvtColor(np.uint8([[bright_hsv]]), cv2.COLOR_HSV2BGR)[0, 0]
    tail = cv2.cvtColor(np.uint8([[tail_hsv]]), cv2.COLOR_HSV2BGR)[0, 0]
    pixels = np.broadcast_to(tail, (height, width, 3)).copy()
    columns, remainder = divmod(bright_pixels, height)
    pixels[:, :columns] = bright
    if remainder:
        pixels[:remainder, columns] = bright
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[y:y + height, x:x + width] = pixels
    return frame


@pytest.mark.parametrize("bright_pixels,bright_hsv,tail_hsv,expected", [
    pytest.param(156, (8, 143, 229), (6, 132, 90), True, id="full-bright-gauge"),
    pytest.param(0, (8, 143, 229), (6, 132, 90), False, id="dark-tail-is-not-full"),
    pytest.param(135, (8, 143, 229), (6, 132, 90), False, id="45-bright-columns-plus-dark-tail"),
    pytest.param(149, (8, 143, 229), (0, 0, 0), False, id="149-bright-pixels-are-insufficient"),
    pytest.param(150, (8, 143, 229), (0, 0, 0), True, id="150-bright-pixels-meet-count"),
    pytest.param(156, (90, 143, 229), (0, 0, 0), False, id="other-hue-is-not-full"),
    pytest.param(156, (8, 240, 229), (0, 0, 0), False, id="other-saturation-is-not-full"),
])
def test_native_full_requires_bright_matching_pixels(tmp_path, bright_pixels, bright_hsv, tail_hsv, expected):
    node = json.loads(RESOURCE_PATH.read_text(encoding="utf-8"))[FULL_NODE]
    frame = synthetic_gauge(node, bright_pixels, bright_hsv, tail_hsv)
    pipeline_dir = tmp_path / "bundle" / "pipeline"
    pipeline_dir.mkdir(parents=True)
    (pipeline_dir / "energy.json").write_text(
        json.dumps({FULL_NODE: node}, ensure_ascii=False), encoding="utf-8",
    )
    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), ImageOnlyController(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(pipeline_dir.parent).wait().succeeded
    results = []

    class Inspect(CustomAction):
        def run(self, context, argv):
            results.append(context.run_recognition(FULL_NODE, frame))
            return True

    assert resource.register_custom_action("EnergyColorInspect", Inspect())
    assert resource.override_pipeline({"EnergyColorInspectEntry": {
        "recognition": {"type": "DirectHit"},
        "action": {"type": "Custom", "param": {"custom_action": "EnergyColorInspect"}},
        "pre_delay": 0, "post_delay": 0, "next": [],
    }})
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    watchdog_fired = []

    def stop():
        watchdog_fired.append(True)
        tasker.post_stop()

    watchdog = Timer(10, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        assert tasker.post_task("EnergyColorInspectEntry").wait().succeeded
        assert not watchdog_fired, "Native color fixture exceeded its 10-second watchdog"
        assert len(results) == 1 and results[0] is not None
        assert results[0].hit is expected
        assert controller.inputs == []
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
