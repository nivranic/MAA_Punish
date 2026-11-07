"""Standalone Pipeline action order with a scripted warning; no device input."""
from pathlib import Path

from maa.controller import CustomController

from action.combat.reactive_dodge import DodgeAfterWarning
from test_r03_recruitment_native import native_hub


def test_pipeline_warning_executes_dodge_before_attack(tmp_path):
    with native_hub(tmp_path, visible=False, accept_inputs=True) as (resource, tasker, frame, inputs, expired):
        # This controller only records virtual inputs. No ADB is involved.
        assert resource.register_custom_action("DodgeAfterWarning", DodgeAfterWarning())
        job = tasker.post_task("WarningRouteProbe", {
            "自动闪避": {"recognition": {"type": "DirectHit"}},
            "WarningRouteProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "DoNothing"},
                "next": ["自动闪避后攻击"], "on_error": [],
                "timeout": 1000,
            },
        }).wait()
        assert job.succeeded and not expired
        assert len(inputs) == 2
        assert inputs[0] == (1048, 620)
        assert inputs[1] != inputs[0]


def test_pipeline_warning_uses_win32_keyboard_overrides(tmp_path, monkeypatch):
    keys = []

    def click_key(self, keycode):
        keys.append(keycode)
        return True

    monkeypatch.setattr(CustomController, "click_key", click_key)
    with native_hub(tmp_path, visible=False, accept_inputs=True) as (resource, tasker, frame, inputs, expired):
        root = Path(__file__).resolve().parents[2]
        assert resource.post_bundle(root / "resource/pgr_win32").wait().succeeded
        assert resource.register_custom_action("DodgeAfterWarning", DodgeAfterWarning())
        job = tasker.post_task("KeyboardWarningProbe", {
            "自动闪避": {"recognition": {"type": "DirectHit"}},
            "KeyboardWarningProbe": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "DoNothing"},
                "next": ["自动闪避后攻击"], "on_error": [],
                "timeout": 1000,
            },
        }).wait()
        assert job.succeeded and not expired
        assert keys == [75, 74]  # Production Win32 K dodge, then J attack.
        assert not inputs
