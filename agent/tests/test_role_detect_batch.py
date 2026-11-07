"""Same-frame role scans keep ordered matching and the native Or contract."""

from copy import deepcopy
import json
from threading import Timer
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker

from action.combat.core import role_detect
from test_support.fakes import FakeContext, make_hit, make_miss
from test_support.native_lifecycle import stop_and_wait_idle


CATALOG = {
    "generic-key": {"name": "通用", "cls_name": "GeneralFight", "attack_template": "b.png"},
    "first-key": {"name": "首位", "cls_name": "First", "attack_template": ["a.png", "c.png"]},
    "second-key": {"cls_name": "Second", "attack_template": "b.png"},
    "empty-key": {"cls_name": "Empty", "attack_template": []},
}
NODE = {
    "recognition": {"type": "TemplateMatch", "param": {
        "template": ["original.png"], "threshold": [0.99],
        "roi": [12, 23, 140, 75], "roi_offset": [3, 4, -5, -6],
        "green_mask": True, "method": 3, "order_by": "Score", "index": 0,
    }},
    "inverse": False,
}


class RoleContext(FakeContext):
    """Match template groups, with the observed native first-hit Or envelope."""

    def __init__(self, hits=()):
        super().__init__(node_data={"检查角色": deepcopy(NODE)})
        self.hits = set(hits)
        self.get_calls = []
        self.after_recognition = None

    def get_node_data(self, name):
        self.get_calls.append(name)
        return super().get_node_data(name)

    def run_recognition(self, entry, image=None, pipeline_override=None):
        self.check_role_calls.append({
            "entry": entry, "image": image, "pipeline_override": pipeline_override,
        })
        recognition = pipeline_override[entry]["recognition"]
        if recognition.get("type") == "Or":
            rows = []
            for child in recognition["param"]["any_of"]:
                assert set(child) == {"recognition"}, "Bare inline recognition becomes DirectHit"
                assert child["recognition"]["type"] == "TemplateMatch"
                hit = bool(self.hits.intersection(child["recognition"]["param"]["template"]))
                rows.append({"algorithm": "TemplateMatch", "box": [1, 2, 3, 4] if hit else None})
                if hit:
                    break
            result = SimpleNamespace(hit=hit, algorithm="Or", raw_detail=rows)
        else:
            hit = bool(self.hits.intersection(recognition["param"]["template"]))
            result = make_hit() if hit else make_miss()
        if self.after_recognition is not None:
            self.after_recognition()
        return result


@pytest.mark.parametrize("hits,kwargs,expected,attempts", [
    (["a.png", "b.png"], {}, ("首位", "First"), 1),
    (["c.png"], {}, ("首位", "First"), 1),
    (["b.png"], {}, ("second-key", "Second"), 2),
    ([], {}, ("未知", "GeneralFight"), 3),
    (["a.png", "b.png"], {"prefer_cls": ["GeneralFight"]}, ("通用", "GeneralFight"), 1),
    (["a.png", "b.png"], {"skip_cls": ["First", "Second"]}, ("通用", "GeneralFight"), 1),
    (["a.png", "b.png"], {"prefer_cls": ["Second", "Second", "First"], "skip_cls": ["Second"]}, ("首位", "First"), 1),
])
def test_same_frame_catalog_preserves_first_role_groups_and_order(monkeypatch, hits, kwargs, expected, attempts):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    context, image = RoleContext(hits), object()
    assert role_detect.detect_current_role(context, image, **kwargs) == expected
    assert len(context.check_role_calls) == 1
    call = context.check_role_calls[0]
    assert call["image"] is image
    recognition = call["pipeline_override"]["检查角色"]["recognition"]
    assert recognition["type"] == "Or"
    children = recognition["param"]["any_of"]
    entries = role_detect.ordered_role_entries(**kwargs)
    assert len(children) == len(entries)
    assert len(children) >= attempts
    assert [child["recognition"]["param"]["template"] for child in children] == [
        role_detect._normalize_attack_templates(info["attack_template"]) for _, info in entries
    ]
    assert context.get_calls == ["检查角色"]
    assert context.actions == context.pipeline_overrides == []


def test_effective_parameters_are_fresh_and_local(monkeypatch):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    context = RoleContext()
    original = deepcopy(context.node_data)
    role_detect.detect_current_role(context, object())
    assert context.node_data == original
    for child in context.check_role_calls[-1]["pipeline_override"]["检查角色"]["recognition"]["param"]["any_of"]:
        parameters = child["recognition"]["param"]
        assert parameters == {
            **original["检查角色"]["recognition"]["param"],
            "template": parameters["template"], "threshold": [0.8] * len(parameters["template"]),
        }
        assert parameters is not context.node_data["检查角色"]["recognition"]["param"]
    context.node_data["检查角色"]["recognition"]["param"]["roi"] = [30, 40, 50, 60]
    role_detect.detect_current_role(context, object())
    children = context.check_role_calls[-1]["pipeline_override"]["检查角色"]["recognition"]["param"]["any_of"]
    assert all(child["recognition"]["param"]["roi"] == [30, 40, 50, 60] for child in children)


def test_callback_keeps_serial_ticks_before_attempts_and_stops_at_first_hit(monkeypatch):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    context, observations = RoleContext(["b.png"]), []

    def tick():
        observations.append(len(context.check_role_calls))

    assert role_detect.detect_current_role(context, b"same-frame", on_tick=tick) == ("second-key", "Second")
    assert observations == [0, 1]
    assert context.get_calls == []
    assert [call["image"] for call in context.check_role_calls] == [b"same-frame", b"same-frame"]
    assert all("type" not in call["pipeline_override"]["检查角色"]["recognition"] for call in context.check_role_calls)


def test_empty_catalog_does_not_request_node_or_recognition(monkeypatch):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", {})
    context = RoleContext()
    assert role_detect.detect_current_role(context, b"image") == ("未知", "GeneralFight")
    assert context.get_calls == context.check_role_calls == []


@pytest.mark.parametrize("when", ["before", "after"])
def test_stop_does_not_publish_a_detected_identity(monkeypatch, when):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    context = RoleContext(["a.png"])
    if when == "before":
        context.tasker.stopping = True
    else:
        context.after_recognition = lambda: setattr(context.tasker, "stopping", True)
    assert role_detect.detect_current_role(context, b"image") == ("未知", "GeneralFight")
    if when == "before":
        assert context.get_calls == context.check_role_calls == []


@pytest.mark.parametrize("result", [
    None,
    SimpleNamespace(hit=True, algorithm="DirectHit", raw_detail=[]),
    SimpleNamespace(hit=False, algorithm="Or", raw_detail=[]),
    SimpleNamespace(hit=False, algorithm="Or", raw_detail=[{"algorithm": "TemplateMatch", "box": None}]),
    SimpleNamespace(hit=True, algorithm="Or", raw_detail=[{"algorithm": "DirectHit", "box": [1, 2, 3, 4]}]),
    SimpleNamespace(hit=True, algorithm="Or", raw_detail=[{"algorithm": "TemplateMatch"}]),
    SimpleNamespace(hit=True, algorithm="Or", raw_detail=[{"algorithm": "TemplateMatch", "box": None}]),
    SimpleNamespace(hit=True, algorithm="Or", raw_detail=[{"algorithm": "TemplateMatch", "box": [1, 2, 3, 4]}] * 2),
    SimpleNamespace(hit=True, algorithm="Or", raw_detail=[{"algorithm": "TemplateMatch", "box": []}]),
    SimpleNamespace(algorithm="Or"),
])
def test_malformed_or_result_is_explicitly_rejected(monkeypatch, result):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    context = RoleContext()
    monkeypatch.setattr(context, "run_recognition", lambda **_: result)
    with pytest.raises(RuntimeError, match="Or"):
        role_detect.detect_current_role(context, b"image")


@pytest.mark.parametrize("node", [None, {}, {"recognition": {"type": "DirectHit", "param": {}}},
    {**NODE, "inverse": True}, {"recognition": {"type": "TemplateMatch", "param": None}},
])
def test_unvalidated_effective_node_is_explicitly_rejected(monkeypatch, node):
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    context = RoleContext()
    context.node_data["检查角色"] = deepcopy(node)
    with pytest.raises(RuntimeError, match="检查角色"):
        role_detect.detect_current_role(context, b"image")
    assert context.check_role_calls == []


def test_real_native_or_all_miss_and_ordered_template_groups(tmp_path, monkeypatch):
    """Synthetic pixels only; reject accidental DirectHit and flatten-by-score."""
    monkeypatch.setattr(role_detect, "ROLE_ACTIONS", CATALOG)
    bundle = tmp_path / "bundle"
    (bundle / "pipeline").mkdir(parents=True)
    (bundle / "image").mkdir()
    rng = np.random.default_rng(2371)
    templates = {name: rng.integers(0, 210, (16, 16, 3), dtype=np.uint8) for name in "abc"}
    for name, template in templates.items():
        ok, encoded = cv2.imencode(".png", template)
        assert ok
        (bundle / "image" / f"{name}.png").write_bytes(encoded.tobytes())
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[20:36, 70:86], frame[20:36, 20:36] = templates["a"], templates["b"]
    empty = np.zeros_like(frame)
    nodes = {
        "检查角色": {"recognition": {"type": "TemplateMatch", "param": {
            "template": ["a.png"], "threshold": [0.8], "roi": [0, 0, 120, 64],
            "green_mask": False,
        }}},
        "BatchTest": {"recognition": {"type": "DirectHit"},
            "action": {"type": "Custom", "param": {"custom_action": "BatchTest"}},
            "next": [], "on_error": [], "pre_delay": 0, "post_delay": 0},
    }
    (bundle / "pipeline" / "test.json").write_text(json.dumps(nodes) + "\n", encoding="utf-8")
    inputs, errors, expired, observed = [], [], [], []

    class Controller(CustomController):
        def connect(self): return True
        def request_uuid(self): return "synthetic-role-batch"
        def get_features(self): return 0
        def screencap(self): return frame
        def deny(self, *args):
            inputs.append(args)
            return False
        click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = deny

    class Probe(CustomAction):
        def run(self, context, argv):
            try:
                before = context.get_node_data("检查角色")

                class Recorder:
                    tasker = context.tasker
                    def get_node_data(self, name): return context.get_node_data(name)
                    def run_recognition(self, *args, **kwargs):
                        result = context.run_recognition(*args, **kwargs)
                        observed.append({"algorithm": result.algorithm, "hit": result.hit, "rows": result.raw_detail})
                        return result

                proxy = Recorder()
                # First role is to the right of the higher-priority-by-position match.
                assert role_detect.detect_current_role(proxy, frame) == ("首位", "First")
                assert role_detect.detect_current_role(proxy, frame, prefer_cls=["Second"]) == ("second-key", "Second")
                assert role_detect.detect_current_role(proxy, empty) == ("未知", "GeneralFight")
                # The first role's second template must remain grouped with that role.
                only_c = empty.copy()
                only_c[20:36, 70:86] = templates["c"]
                assert role_detect.detect_current_role(proxy, only_c) == ("首位", "First")
                assert context.get_node_data("检查角色") == before
                assert len(observed) == 4
                assert [item["algorithm"] for item in observed] == ["Or"] * 4
                assert [item["hit"] for item in observed] == [True, True, False, True]
                assert len(observed[2]["rows"]) == 3
                assert all(row["algorithm"] == "TemplateMatch" and row["box"] is None for row in observed[2]["rows"])
                return True
            except Exception as exc:
                errors.append(repr(exc))
                return False

    assert Tasker.set_log_dir(tmp_path / "native-log")
    Tasker.set_debug_mode(False)
    Tasker.set_save_draw(False)
    Tasker.set_save_on_error(False)
    resource, controller, tasker = Resource(), Controller(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(bundle).wait().succeeded
    assert resource.register_custom_action("BatchTest", Probe())
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)

    def timeout():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(20, timeout)
    watchdog.start()
    try:
        job = tasker.post_task("BatchTest").wait()
        assert job.succeeded, repr(errors)
        assert not errors and not expired and not inputs
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
        (tmp_path / "receipt.json").write_text(json.dumps({
            "errors": errors, "expired": expired, "input_attempts": inputs,
            "observed": observed, "native_idle": not tasker.running and not tasker.stopping,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
