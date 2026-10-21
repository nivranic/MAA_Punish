"""Only accepted portrait matches may contribute owned roles to a cache."""
from types import SimpleNamespace

from maa.define import OCRResult, TemplateMatchResult

from action.basics.RoleSelection import RoleSelection
from test_support.fakes import FakeContext


def result_with_matches(*accepted, raw=()):
    return SimpleNamespace(
        hit=bool(accepted),
        all_results=[*accepted, *raw],
        filtered_results=list(accepted),
        best_result=accepted[0] if accepted else None,
    )


def test_rejected_raw_candidates_are_not_used_as_role_matches():
    accepted = TemplateMatchResult(box=[136, 412, 92, 91], score=0.857026)
    weak = TemplateMatchResult(box=[133, 92, 82, 82], score=0.437664)

    assert RoleSelection._template_match_hits(
        result_with_matches(accepted, raw=[weak])
    ) == [accepted]


def test_multiple_accepted_portraits_are_retained():
    first = TemplateMatchResult(box=[136, 80, 92, 91], score=0.9)
    second = TemplateMatchResult(box=[136, 200, 92, 91], score=0.8)

    assert RoleSelection._template_match_hits(result_with_matches(first, second)) == [
        first, second
    ]


def test_locked_portrait_does_not_end_scan_before_an_owned_role(monkeypatch):
    locked_box = [136, 302, 92, 91]
    owned_box = [136, 412, 92, 91]
    roles = {
        "locked": {"template": ["locked.png"], "metadata": {}},
        "owned": {"template": ["owned.png"], "metadata": {"ice": 100}},
    }
    context = FakeContext()
    ownership_boxes = []

    def recognize(entry, image, pipeline_override=None):
        if entry == "识别角色":
            template = pipeline_override[entry]["recognition"]["param"]["template"]
            box = locked_box if template == ["locked.png"] else owned_box
            return result_with_matches(TemplateMatchResult(box=box, score=0.9))
        if entry == "检查人物是否拥有":
            box = (
                pipeline_override[entry]["recognition"]["param"]["roi"]
                if pipeline_override else None
            )
            ownership_boxes.append(box)
            # The old unscoped check returns the locked row and exits the page.
            return SimpleNamespace(hit=box is None or box == locked_box)
        if entry == "识别试用角色":
            return SimpleNamespace(hit=False)
        if entry == "识别战斗参数":
            return SimpleNamespace(
                hit=True,
                best_result=OCRResult(box=owned_box, text="8123", score=0.99),
            )
        raise AssertionError(entry)

    monkeypatch.setattr(context, "run_recognition", recognize)

    found = RoleSelection().recognize_role(context, roles)

    assert found == {"owned": {"ice": 100, "power": 8123}}
    assert ownership_boxes == [locked_box, owned_box]
