"""Only an unambiguous budget OCR result may authorize recruitment."""
from types import SimpleNamespace

import pytest
from maa.define import OCRResult, OrRecognitionResult

from action.basics.roguelike3_recruitment import R03Recruitment


PARENT = "矩阵循生_招募能量_列表"
WHOLE = f"{PARENT}_整区"
LOCATED = f"{PARENT}_定位"


def result(name, best, *, hit=True):
    return SimpleNamespace(name=name, best_result=best, hit=hit)


def ocr(text):
    return OCRResult(box=[410, 611, 63, 49], score=0.99, text=text)


@pytest.mark.parametrize("name,parts", [
    (PARENT, [result(WHOLE, ocr("1/5"))]),
    (PARENT, [result(WHOLE, ocr("15"), hit=False), result(LOCATED, ocr("1/5"))]),
])
def test_one_known_ocr_path_supplies_the_budget(name, parts):
    assert R03Recruitment._budget_value(result(name, OrRecognitionResult(parts))) == (1, 5)


@pytest.mark.parametrize("name,parts", [
    ("another_parent", [result(LOCATED, ocr("1/5"))]),
    (PARENT, [result("another_child", ocr("1/5"))]),
    (PARENT, []),
    (PARENT, [result(WHOLE, ocr("1/5"), hit=False)]),
    (PARENT, [result(WHOLE, ocr("1/5")), result(LOCATED, ocr("1/5"))]),
    (PARENT, [result(WHOLE, ocr("1/5")), result(LOCATED, ocr("0/5"))]),
    (PARENT, [result(LOCATED, None)]),
    (PARENT, [result(LOCATED, SimpleNamespace(text="1/5"))]),
    (PARENT, [result(LOCATED, OrRecognitionResult([result(WHOLE, ocr("1/5"))]))]),
])
def test_unknown_ambiguous_or_non_ocr_results_are_rejected(name, parts):
    assert R03Recruitment._budget_value(result(name, OrRecognitionResult(parts))) is None


@pytest.mark.parametrize("text", ["15", "515", "6/5", "0/0", "-1/5", "1/5extra"])
def test_localized_ocr_cannot_bypass_the_fraction_guard(text):
    value = result(PARENT, OrRecognitionResult([result(LOCATED, ocr(text))]))
    assert R03Recruitment._budget_value(value) is None


@pytest.mark.parametrize("text,expected", [("0/5", (0, 5)), ("1/5", (1, 5)), ("5/5", (5, 5))])
def test_direct_formation_ocr_remains_supported(text, expected):
    assert R03Recruitment._budget_value(result("矩阵循生_招募能量_编队", ocr(text))) == expected


def test_non_hit_parent_does_not_supply_a_budget():
    value = result(PARENT, OrRecognitionResult([result(LOCATED, ocr("1/5"))]), hit=False)
    assert R03Recruitment._budget_value(value) is None
