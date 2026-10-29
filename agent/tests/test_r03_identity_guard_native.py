"""Real native task failure routing; memory captures, all inputs denied.

The battle HUD is DirectHit and character identity is a scripted recognition
endpoint. Production CombatRunner/provider/session and canonical task policy
run unchanged. This is control-flow evidence, not game or device acceptance.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
from threading import Timer
from types import SimpleNamespace

import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.library import Library
from maa.resource import Resource
from maa.tasker import Tasker

from action.combat.core import provider
from action.combat.core import session
from action.combat.core.session import CombatTask
from action.combat.entry.runner import CombatRunner
from test_support.native_lifecycle import stop_and_wait_idle

ROOT = Path(__file__).resolve().parents[2]
TASK = json.loads((ROOT / "tasks/矩阵循生.json").read_bytes())
SINK = "矩阵循生_身份拒绝停止"
DANGER = "R03IdentityDangerInput"


class DenyInputController(CustomController):
    def __init__(self):
        super().__init__()
        self.inputs, self.captures = [], 0

    def connect(self): return True
    def request_uuid(self): return "offline-r03-identity-control-flow"
    def get_features(self): return 0
    def screencap(self):
        self.captures += 1
        return np.zeros((720, 1280, 3), np.uint8)

    def deny(self, *args):
        self.inputs.append(args)
        return False

    click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = deny


class ObserveRunner(CustomAction):
    def __init__(self):
        super().__init__()
        self.actual = CombatRunner()
        self.callback_success = None
        self.danger_match = None

    def run(self, context, argv):
        if context.get_node_data(DANGER) is not None:
            self.danger_match = context.run_recognition(
                DANGER, np.zeros((720, 1280, 3), np.uint8),
            ).hit
        result = self.actual.run(context, argv)
        self.callback_success = result.success
        return result


def bounded_job(tasker, entry, patch):
    watchdog_fired = []

    def stop():
        watchdog_fired.append(True)
        tasker.post_stop()

    watchdog = Timer(15, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task(entry, patch)
        job.wait()
        detail = tasker.get_task_detail(job.job_id)
        assert detail is not None and detail.status.done
        assert not watchdog_fired, "Native fixture exceeded 15-second watchdog"
        return job, detail
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("failure", ["unknown", "recognition_error", "cleanup_error", "emission_error"])
def test_real_runner_false_stops_jumpback_parent_and_next_task_has_no_policy(tmp_path, monkeypatch, failure):
    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), DenyInputController(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    observed = ObserveRunner()
    assert resource.register_custom_action("CombatRunner", observed)
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    detections = []

    def unknown(_context, _image, **kwargs):
        detections.append(kwargs)
        assert kwargs["on_tick"] is None
        if failure == "recognition_error":
            raise OSError("PRIVATE_NATIVE_RECOGNITION_ERROR")
        return "未知", "GeneralFight"

    monkeypatch.setattr(provider, "detect_current_role", unknown)
    original_emit = session.emit_combat_outcome
    original_wait = CombatTask._wait_in_combat

    def fail(*_args):
        raise OSError("PRIVATE_NATIVE_LIFECYCLE_ERROR")

    if failure in ("cleanup_error", "emission_error"):
        def end_after_capture(self):
            original_wait(self)
            self._in_combat = False
            self.out_of_combat_reason = "enter_timeout"
            if failure == "cleanup_error":
                self.input_scope = SimpleNamespace(close=fail)
        monkeypatch.setattr(CombatTask, "_wait_in_combat", end_after_capture)
    if failure == "emission_error":
        monkeypatch.setattr(session, "emit_combat_outcome", fail)
    patch = deepcopy(TASK["task"][0]["pipeline_override"])
    patch.update({
        "R03IdentityParent": {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "DoNothing"},
            "next": ["[JumpBack]战斗中", DANGER],
        },
        DANGER: {
            "recognition": {"type": "DirectHit"},
            "action": {"type": "Click", "param": {"target": [10, 10, 1, 1]}},
            "next": [],
        },
    })
    patch["战斗中"].update(recognition={"type": "DirectHit"})
    for name in ("R03IdentityParent", "战斗中", SINK, DANGER):
        patch[name].update(pre_delay=0, post_delay=0, rate_limit=0,
                           pre_wait_freezes={"time": 0}, post_wait_freezes={"time": 0})
    try:
        job, detail = bounded_job(tasker, "R03IdentityParent", patch)
        names = [tasker.get_node_detail(node_id).name for node_id in detail.node_id_list]
        assert names == ["R03IdentityParent", "战斗中", SINK]
        assert observed.callback_success is False and observed.danger_match is True
        outcome = observed.actual.last_outcome
        assert outcome.callback_status == ("REJECTED" if failure == "unknown" else "ERROR")
        assert outcome.game_outcome == "UNKNOWN"
        assert outcome.exception_type == ("Roguelike3IdentityError" if failure == "unknown" else "OSError")
        if failure == "unknown":
            assert outcome.exit_reason == "r3_field_identity_unknown"
        elif failure == "cleanup_error":
            assert outcome.cleanup_exception_type == "OSError"
        elif failure == "emission_error":
            assert outcome.emission_exception_type == "OSError"
        assert outcome.field_identity == "unknown"
        assert len(detections) == (1 if failure in ("unknown", "recognition_error") else 0)
        assert "PRIVATE" not in json.dumps(asdict(outcome))
        assert controller.inputs == [] and controller.captures >= 1
        assert not tasker.running and not tasker.stopping
        assert not provider.roguelike3_identity_required(resource.get_node_data("战斗中"))

        # The next task uses the same Tasker/Resource and actual Runner, with
        # only its no-combat endpoint scripted to finish without ordinary input.
        flags, next_nodes = [], []

        def no_combat(self):
            flags.append(self.roguelike3_identity_required)
            next_nodes.append(self.context.get_node_data("战斗中"))
            self.out_of_combat_reason = "enter_timeout"

        monkeypatch.setattr(CombatTask, "_wait_in_combat", no_combat)
        monkeypatch.setattr(session, "emit_combat_outcome", original_emit)
        plain, plain_detail = bounded_job(tasker, "战斗中", {"战斗中": {
            "recognition": {"type": "DirectHit"}, "next": [], "pre_delay": 0, "post_delay": 0,
        }})
        assert plain.succeeded and flags == [False]
        assert not provider.roguelike3_identity_required(next_nodes[0])
        assert observed.actual.last_outcome.callback_status == "COMPLETED"
        assert controller.inputs == [] and not tasker.running and not tasker.stopping
        report = {
            "framework": Library.version(), "failure_injection": failure,
            "first_task_status": "SUCCEEDED" if detail.status.succeeded else "FAILED",
            "first_task_succeeded": job.succeeded, "callback_success": False,
            "danger_sibling_matchable": True, "node_history": names,
            "rejected_outcome": asdict(outcome), "controller_input_attempts": controller.inputs,
            "captures": controller.captures, "same_tasker_next_task_status": "SUCCEEDED" if plain_detail.status.succeeded else "FAILED",
            "same_tasker_next_policy_flags": flags,
            "same_tasker_next_outcome": asdict(observed.actual.last_outcome),
            "native_idle": not tasker.running and not tasker.stopping,
            "scope": "Synthetic HUD/unknown identity; real native error routing, no device/game acceptance",
        }
        output = Path(os.environ.get("MAA_R03_IDENTITY_EVIDENCE", tmp_path))
        output.mkdir(parents=True, exist_ok=True)
        with (output / f"native-routing-{failure}.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    finally:
        stop_and_wait_idle(tasker)
