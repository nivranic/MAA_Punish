"""Strict fraction OCR under the two observed animated weapon backgrounds."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence

import cv2
import pytest

from action.basics.roguelike3_recruitment import R03Recruitment
from test_r03_recruitment_budget_native import native_probe


def test_deepred_one_budget_is_detected_without_rewriting_the_occluded_fraction(tmp_path):
    values = []

    def probe(context, frames):
        image = cv2.imread(str(EVIDENCE_DIR / "r03_budget_deepred_one_overlap.png"))
        assert image is not None
        values.append(R03Recruitment.read_budget(context, image))
        whole_roi = context.run_recognition("矩阵循生_招募能量_列表_整区", image)
        values.append([item["text"] for item in whole_roi.raw_detail["all"]])
        values.append(whole_roi.hit)
        values.append(context.run_recognition("矩阵循生_招募列表就绪", image).hit)

    assert native_probe(tmp_path, probe) == []
    assert values == [(1, 5), ["15"], False, True]


@pytest.mark.parametrize("sample", ["recent", "earlier"])
def test_actual_background_error_reads_fraction_without_replacing_515(tmp_path, sample):
    values = []

    def probe(context, frames):
        image = cv2.imread(str(EVIDENCE_DIR / f"r03_budget_background_{sample}.png"))
        assert image is not None
        values.append(R03Recruitment.read_budget(context, image))
        original = context.run_recognition("矩阵循生_招募能量_列表_整区", image, {
            "矩阵循生_招募能量_列表_整区": {"recognition": {"param": {"color_filter": ""}}},
        })
        values.append([item["text"] for item in original.raw_detail["all"]])
        values.append(original.hit)

    assert native_probe(tmp_path, probe) == []
    assert values == [(5, 5), ["515"], False]


@pytest.mark.parametrize("missing", ["numerator", "slash", "denominator", "all"])
def test_incomplete_fraction_stays_unreadable_without_input(tmp_path, missing):
    values = []

    def probe(context, frames):
        image = cv2.imread(str(EVIDENCE_DIR / "r03_budget_background_recent.png"))
        assert image is not None
        if missing == "numerator":
            image[611:660, 410:442] = 0
        elif missing == "slash":
            image[630:660, 440:454] = 0
        elif missing == "denominator":
            image[611:660, 453:473] = 0
        else:
            image[:] = 0
        values.append(R03Recruitment.read_budget(context, image))

    assert native_probe(tmp_path, probe) == []
    assert values == [None]


def test_refusal_toast_still_rejects_budget_and_other_budget_values_survive(tmp_path):
    values = []

    def probe(context, frames):
        for sample in ("selected", "owned", "cost1", "cost2", "flow_owned", "flow_trial", "guili_owned"):
            values.append(R03Recruitment.read_budget(context, frames[sample]))

    assert native_probe(tmp_path, probe) == []
    assert values == [None, (3, 3), (1, 3), (3, 3), (1, 3), (1, 3), (5, 5)]
