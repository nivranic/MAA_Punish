"""R03 must switch away from a dedicated attacker before accepting a generic support."""
from pathlib import Path
from test_support.device_evidence import EVIDENCE_DIR, require_device_evidence
from types import SimpleNamespace

import cv2
import pytest
from maa.custom_action import CustomAction

from action.combat.core import role_detect, switch
from test_r03_recruitment_native import native_hub
from test_support.fakes import FakeContext, make_hit


@pytest.mark.parametrize("strict", [True, False])
def test_generic_switch_checks_the_target_policy(monkeypatch, strict):
    context = FakeContext(recognition_map={"切换蓝色QTE": make_hit((100, 200, 40, 40))})
    monkeypatch.setattr(role_detect, "is_cls_on_field", lambda *_: False)
    answers = iter([("终焉", "Oblivion"), ("瑰丽", "GeneralFight")])
    monkeypatch.setattr(role_detect, "detect_current_role", lambda *_: next(answers))
    combat = SimpleNamespace(roguelike3_identity_required=strict)
    monkeypatch.setattr(switch, "capture_frame", lambda *_: object())
    assert switch.attempt_switch_to_color(context, "B", "GeneralFight", combat=combat)
    assert context.tasker.controller.clicks == ([(120, 220)] if strict else [])


@pytest.mark.parametrize("display,cls_name", [("终焉", "Oblivion"), ("未知", "GeneralFight")])
def test_r03_generic_target_never_arrives_on_wrong_or_unknown_identity(monkeypatch, display, cls_name):
    context = FakeContext(recognition_map={"切换蓝色QTE": make_hit()})
    monkeypatch.setattr(role_detect, "is_cls_on_field", lambda *_: False)
    monkeypatch.setattr(role_detect, "detect_current_role", lambda *_: (display, cls_name))
    monkeypatch.setattr(switch, "capture_frame", lambda *_: object())
    clock = iter(range(10))
    monkeypatch.setattr(switch.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(switch.time, "sleep", lambda _: None)
    combat = SimpleNamespace(roguelike3_identity_required=True)
    assert not switch.attempt_switch_to_color(context, "B", "GeneralFight", combat=combat,
                                             verify_timeout=2)


def test_real_r03_huds_require_a_click_before_generic_support_arrives(tmp_path, monkeypatch):
    fixture_dir = EVIDENCE_DIR
    images = [cv2.imread(str(fixture_dir / f"r03_multi_{name}_hud.png"))
              for name in ("oblivion", "flow")]
    assert all(image is not None for image in images)
    with native_hub(tmp_path, visible=False) as (resource, tasker, frame, inputs, expired):
        observations, errors = [], []

        class Probe(CustomAction):
            def run(self, context, argv):
                try:
                    combat = SimpleNamespace(roguelike3_identity_required=True, frame=None)
                    captures = iter(images)

                    def capture(_context, _combat):
                        frame[:] = next(captures)
                        _combat.frame = frame
                        return frame

                    monkeypatch.setattr(switch, "capture_frame", capture)
                    observations.append(switch.attempt_switch_to_color(
                        context, "B", "GeneralFight", combat=combat,
                    ))
                    return True
                except Exception as exc:
                    errors.append(exc)
                    return False

        assert resource.register_custom_action("R03SwitchArrivalProbe", Probe())
        job = tasker.post_task("R03SwitchArrivalProbe", {
            "R03SwitchArrivalProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "Custom", "param": {"custom_action": "R03SwitchArrivalProbe"}},
                "next": [], "on_error": [],
            },
            "检查角色": {"recognition": {"param": {"roi": [1018, 436, 138, 142]}}},
        }).wait()
        assert not errors, repr(errors)
        assert job.succeeded and not expired and observations == [True]
        assert len(inputs) == 1
