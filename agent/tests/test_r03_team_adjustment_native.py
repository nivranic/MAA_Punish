"""Native checkbox clicks and confirmation on synthetic R03 adjustment pages.

The production unchecked template, And selection, Click, and selected-color
confirmation run unchanged. Only the page OCR is represented by a generated
anchor, and timeouts/delays are bounded for this offline controller. No device
screenshots or game input are used.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from threading import Timer

import cv2
import jsonc
import numpy as np
import pytest
from maa.controller import CustomController
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = jsonc.loads((ROOT / "resource/base/pipeline/Roguelike_3/Roguelike_3.jsonc").read_text(encoding="utf-8"))
PAGE = "矩阵循生_调整页就绪"
BOXES = {"队长": (932, 363, 46, 46), "首发": (1056, 363, 46, 46)}
NAMES = [PAGE, *[
    f"矩阵循生_{part}红位{column}{suffix}"
    for column in BOXES
    for part, suffix in (("设置", ""), ("", "未勾选"), ("", "勾选"), ("", "已勾选"))
]]


@contextmanager
def native_adjustment(tmp_path, column, *, page_visible=True, selected=False,
                      checkbox_visible=True, pipeline=None):
    source = deepcopy(PIPELINE if pipeline is None else pipeline)
    nodes = {name: source[name] for name in NAMES}
    nodes[PAGE]["recognition"] = {
        "type": "TemplateMatch", "param": {
            "roi": [180, 60, 80, 70], "template": "page-anchor.png", "threshold": 0.99,
        },
    }
    # Keep production recognition/action/next/on_error. These only bound waits.
    for node in nodes.values():
        node.update(pre_delay=0, post_delay=0, rate_limit=0, timeout=100,
                    pre_wait_freezes=0, post_wait_freezes=0)
    bundle = tmp_path / "bundle"
    (bundle / "pipeline").mkdir(parents=True)
    (bundle / "image/矩阵循生").mkdir(parents=True)
    template_bytes = (ROOT / "resource/base/image/矩阵循生/调整未勾选.png").read_bytes()
    template = cv2.imdecode(np.frombuffer(template_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert template is not None and template.shape == (46, 46, 3)
    (bundle / "image/矩阵循生/调整未勾选.png").write_bytes(template_bytes)
    anchor = np.random.default_rng(387).integers(0, 256, (28, 28, 3), dtype=np.uint8)
    assert cv2.imwrite(str(bundle / "image/page-anchor.png"), anchor)
    (bundle / "pipeline/checkbox.json").write_text(
        json.dumps(nodes, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    initial = np.full((720, 1280, 3), 240, np.uint8)
    if page_visible:
        initial[80:108, 200:228] = anchor
    x, y, width, height = BOXES[column]
    if checkbox_visible:
        initial[y:y + height, x:x + width] = template
    tick = cv2.cvtColor(np.array([[[110, 220, 220]]], np.uint8), cv2.COLOR_HSV2BGR)[0, 0]

    class Capture(CustomController):
        def __init__(self):
            super().__init__()
            self.inputs, self.captures = [], 0
            self.reset()

        def reset(self):
            self.frame = initial.copy()
            if selected:
                self.mark_selected()

        def mark_selected(self):
            self.frame[y + 15:y + 30, x + 15:x + 30] = tick

        def connect(self): return True
        def request_uuid(self): return "offline-r03-team-adjustment"
        def get_features(self): return 0
        def screencap(self):
            self.captures += 1
            return self.frame

        def click(self, px, py, contact=0, pressure=1):
            # A successful input at the template border does not toggle the UI.
            accepted = x + 6 <= px < x + width - 6 and y + 6 <= py < y + height - 6
            self.inputs.append({"point": [px, py], "selected": accepted})
            if accepted:
                self.mark_selected()
            return True

    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu() and resource.post_bundle(bundle).wait().succeeded
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(15, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        yield tasker, controller, expired
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


def run_checkbox(tasker, column):
    entry = f"矩阵循生_设置红位{column}"
    job = tasker.post_task("R03CheckboxStart", {"R03CheckboxStart": {
        "recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"},
        "pre_delay": 0, "post_delay": 0, "rate_limit": 0, "timeout": 100,
        "next": [entry], "on_error": [],
    }}).wait()
    detail = tasker.get_task_detail(job.job_id)
    names = [tasker.get_node_detail(node_id).name for node_id in detail.node_id_list]
    return job, detail, names


def save_receipt(tmp_path, controller, expired, results):
    (tmp_path / "receipt.json").write_text(json.dumps({
        "native": Library.version(), "inputs": controller.inputs,
        "captures": controller.captures, "expired": expired, "results": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("column", BOXES)
def test_native_checkbox_clicks_stay_inside_and_confirm_the_selected_color(tmp_path, column):
    with native_adjustment(tmp_path, column) as (tasker, controller, expired):
        results = []
        for _ in range(16):
            controller.reset()
            job, detail, names = run_checkbox(tasker, column)
            results.append({"succeeded": job.succeeded, "names": names})
            assert job.succeeded and detail.status.succeeded and not expired
            assert names == ["R03CheckboxStart", f"矩阵循生_设置红位{column}",
                             f"矩阵循生_红位{column}已勾选"]
        save_receipt(tmp_path, controller, expired, results)
        assert len(controller.inputs) == 16
        assert all(item["selected"] for item in controller.inputs)


@pytest.mark.parametrize("column", BOXES)
@pytest.mark.parametrize("case", ["wrong_page", "already_selected", "missing_checkbox"])
def test_native_checkbox_guards_reject_without_input(tmp_path, column, case):
    with native_adjustment(tmp_path, column,
                           page_visible=case != "wrong_page",
                           selected=case == "already_selected",
                           checkbox_visible=case != "missing_checkbox") as (tasker, controller, expired):
        job, _, names = run_checkbox(tasker, column)
        save_receipt(tmp_path, controller, expired, [{"succeeded": job.succeeded, "names": names}])
        assert job.failed and not expired
        # Native appends an unnamed failure detail when no child recognizes.
        assert [name for name in names if name] == ["R03CheckboxStart"]
        assert not controller.inputs


@pytest.mark.parametrize("column", BOXES)
def test_native_successful_border_input_does_not_bypass_color_confirmation(tmp_path, column):
    # Replay the observed edge mechanism deterministically; do not depend on a
    # particular random sample from the native rectangle click distribution.
    pipeline = deepcopy(PIPELINE)
    x, y, _, _ = BOXES[column]
    pipeline[f"矩阵循生_设置红位{column}"]["action"]["param"] = {
        "target": [x + 18, y], "target_offset": [0, 0, 0, 0],
    }
    with native_adjustment(tmp_path, column, pipeline=pipeline) as (tasker, controller, expired):
        job, detail, names = run_checkbox(tasker, column)
        action_node = next(node for node in (
            tasker.get_node_detail(node_id) for node_id in detail.node_id_list
        ) if node.name == f"矩阵循生_设置红位{column}")
        action = action_node.action
        save_receipt(tmp_path, controller, expired, [{"succeeded": job.succeeded,
                                                   "action_succeeded": action.success, "names": names}])
        assert action.success is True and job.failed and not expired
        assert [name for name in names if name] == ["R03CheckboxStart", f"矩阵循生_设置红位{column}"]
        assert controller.inputs == [{"point": [x + 18, y], "selected": False}]
