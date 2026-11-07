"""Two real 5/5 budget crops rejected as 515 by the old, looser OCR crop."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from types import SimpleNamespace

import cv2
import pytest
from maa.controller import Controller

from action.basics.RoleSelection import RoleSelection
from action.basics.roguelike3_recruitment import R03Recruitment
from test_r03_recruitment_budget_native import native_probe


@pytest.mark.parametrize("name", ["overlap", "initial"])
def test_actual_five_point_budget_keeps_fraction_under_character_animation(tmp_path, name):
    observations = []

    def probe(context, frames):
        image = cv2.imread(str(EVIDENCE_DIR / f"r03_budget_five_{name}.png"))
        observations.append(R03Recruitment.read_budget(context, image))
        old = context.run_recognition("矩阵循生_招募能量_列表_整区", image, {
            "矩阵循生_招募能量_列表_整区": {"recognition": {"param": {
                "roi": [410, 609, 70, 55], "color_filter": "",
            }}},
        })
        observations.append(old.hit if old else None)

    assert native_probe(tmp_path, probe) == []
    assert observations == [(5, 5), False]


def test_empty_slot_list_may_show_replace_without_authorizing_compile(tmp_path):
    observations = []

    def probe(context, frames):
        image = cv2.imread(str(EVIDENCE_DIR / "r03_recruitment_replace.png"))
        page = context.run_recognition("矩阵循生_招募列表就绪", image)
        compile_role = context.run_recognition("选择角色程序_矩阵循生", image)
        observations.append((page.hit if page else None, compile_role.hit if compile_role else None,
                             R03Recruitment.read_budget(context, image)))

    assert native_probe(tmp_path, probe) == []
    assert observations == [(True, False, (1, 5))]


def test_open_slot_uses_its_completed_list_frame_not_a_later_animation_frame(tmp_path, monkeypatch):
    observations, captures = [], []
    original = Controller.post_screencap

    def probe(context, frames):
        invalid = cv2.imread(str(EVIDENCE_DIR / "r03_budget_five_overlap.png"))

        def capture(controller):
            captures.append(True)
            if len(captures) == 1:
                return original(controller)
            result = SimpleNamespace(get=lambda: invalid)
            result.wait = lambda: result
            return result

        monkeypatch.setattr(Controller, "post_screencap", capture)
        observations.append(R03Recruitment(RoleSelection()).open_slot(context, "B"))

    inputs = native_probe(tmp_path, probe, event_team="formation_red", opening="flow_owned")
    assert observations == [True] and len(inputs) == 1
    assert len(captures) == 1
