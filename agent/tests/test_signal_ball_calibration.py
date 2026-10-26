"""Signal-ball calibration contracts; native parameter parsing, simulated matches/input."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from maa.define import TemplateMatchResult
from maa.resource import Resource
import pytest

from action.basics.InterfaceZone import LoadRoiZone as load_module
from action.basics.InterfaceZone import roi_zone_controller as policy
from action.basics.InterfaceZone.roi_zone_classify import (
    POINT_ZONE_MARGIN,
    build_node_zone_map,
    load_combat_pipeline_nodes,
)
from action.combat.actions.CombatActions import CombatActions
from action.combat.actions.signal_ball_layout import (
    SIGNAL_BALL_SLOT_CENTERS,
    signal_ball_slot_centers,
    slot_index_from_box,
)
from test_support.fakes import FakeContext, make_miss


@pytest.fixture
def layout_context(monkeypatch):
    """Use Maa's real dumper/merge contract without OCR, a controller or game input."""
    nodes = load_combat_pipeline_nodes()
    selected = ["识别信号球", *(f"消球{slot}" for slot in range(1, 9))]
    resource = Resource()
    assert resource.override_pipeline({name: deepcopy(nodes[name]) for name in selected})
    context = FakeContext()
    context.tasker.resource = resource
    monkeypatch.setattr(context, "get_node_data", resource.get_node_data)

    def override_pipeline(patch):
        context.pipeline_overrides.append(deepcopy(patch))
        assert resource.override_pipeline(patch)

    monkeypatch.setattr(context, "override_pipeline", override_pipeline)
    node_map = load_module._get_node_zone_map()
    monkeypatch.setattr(
        load_module, "_get_node_zone_map", lambda: {name: node_map[name] for name in selected}
    )
    return context


def apply_calibration(context, monkeypatch, tmp_path, offset, controller="adb"):
    path = tmp_path / f"{controller}_zone_offset.json"
    monkeypatch.setitem(policy.OFFSET_FILES, controller, path)
    data = {
        key: {"hit": True, "offset": [0, 0, 0, 0]}
        for key, _, _ in policy.zones_for(controller)
    }
    data["signal_zone"]["offset"] = offset
    path.write_text(json.dumps(data), encoding="utf-8")
    load_module.LoadRoiZone().apply_offsets(context, controller)


@pytest.mark.parametrize("offset", [[95, 80, 0, 0], [-95, -80, 0, 0]])
@pytest.mark.parametrize("slot", range(8))
def test_all_eight_shifted_matches_dispatch_to_the_same_calibrated_action(
    layout_context, monkeypatch, tmp_path, slot, offset
):
    context = layout_context
    apply_calibration(context, monkeypatch, tmp_path, offset)
    centers = signal_ball_slot_centers(context)
    base_x, base_y = SIGNAL_BALL_SLOT_CENTERS[slot]
    expected = (base_x + offset[0], base_y + offset[1])
    assert centers[slot] == expected
    x, y = expected
    match = TemplateMatchResult(box=[x - 10, y - 10, 20, 20], score=0.95)
    assert slot_index_from_box(match.box, centers) == slot

    def recognize(entry, image, pipeline_override=None):
        if entry == "识别信号球" and pipeline_override[entry]["template"] == ["red.png"]:
            return SimpleNamespace(hit=True, filtered_results=[match])
        return make_miss()

    monkeypatch.setattr(context, "run_recognition", recognize)
    actions = CombatActions(context)
    actions.template = {
        color: {"识别信号球": {"template": [f"{color}.png"]}}
        for color in ["red", "blue", "yellow"]
    }
    image = object()
    monkeypatch.setattr(actions, "_get_image", lambda: image)
    status = actions._detect_signal_ball_status(image)
    assert status == ["red" if index == slot else None for index in range(8)]

    assert actions.ball_elimination_target(status.index("red") + 1)
    assert context.actions == [f"消球{slot + 1}"]
    action = context.get_node_data(context.actions[-1])["action"]["param"]
    assert tuple(action["target"][i] + action["target_offset"][i] for i in range(2)) == expected


def test_current_context_override_replaces_previous_calibration(layout_context):
    context = layout_context
    for offset in [[-95, 40, 0, 0], [30, -20, 0, 0]]:
        context.override_pipeline({"识别信号球": {"recognition": {"param": {
            "roi_offset": offset,
        }}}})
        assert signal_ball_slot_centers(context) == tuple(
            (x + offset[0], y + offset[1]) for x, y in SIGNAL_BALL_SLOT_CENTERS
        )


def test_valid_node_without_offset_uses_default_layout_without_global_state():
    calibrated = FakeContext(node_data={"识别信号球": {"recognition": {"param": {
        "roi_offset": [90, -70, 0, 0],
    }}}})
    assert signal_ball_slot_centers(calibrated)[0] == (1310, 430)
    default = FakeContext(node_data={"识别信号球": {"recognition": {"param": {}}}})
    assert signal_ball_slot_centers(default) == SIGNAL_BALL_SLOT_CENTERS


@pytest.mark.parametrize("node", [
    None,
    {},
    {"recognition": "TemplateMatch"},
    {"recognition": {"param": None}},
    *({"recognition": {"param": {"roi_offset": offset}}} for offset in [
        None, [1, 2], [1, 2, 3, 4, 5], ["1", 0, 0, 0],
        [True, 0, 0, 0], [float("nan"), 0, 0, 0],
    ]),
])
def test_missing_or_bad_layout_stops_signal_recognition(node, monkeypatch):
    context = FakeContext(node_data={"识别信号球": node})
    recognize = Mock()
    monkeypatch.setattr(context, "run_recognition", recognize)
    assert signal_ball_slot_centers(context) is None
    assert CombatActions(context)._detect_signal_ball_status(object()) == []
    recognize.assert_not_called()
    assert context.actions == []


def test_reloading_calibration_does_not_add_offsets(layout_context, monkeypatch, tmp_path):
    context = layout_context
    offset = [-95, 40, 0, 0]
    apply_calibration(context, monkeypatch, tmp_path, offset)
    first = context.pipeline_overrides[-1]
    centers = signal_ball_slot_centers(context)
    apply_calibration(context, monkeypatch, tmp_path, offset)

    assert context.pipeline_overrides[-1] == first
    assert signal_ball_slot_centers(context) == centers
    for slot in range(1, 9):
        assert context.get_node_data(f"消球{slot}")["action"]["param"]["target_offset"] == offset


def test_roi_size_difference_does_not_scale_slot_spacing(layout_context, monkeypatch, tmp_path):
    context = layout_context
    offset = [-60, 30, 120, -20]
    apply_calibration(context, monkeypatch, tmp_path, offset)

    assert context.get_node_data("识别信号球")["recognition"]["param"]["roi_offset"] == offset
    assert signal_ball_slot_centers(context) == tuple(
        (x - 60, y + 30) for x, y in SIGNAL_BALL_SLOT_CENTERS
    )
    for slot in range(1, 9):
        assert context.get_node_data(f"消球{slot}")["action"]["param"]["target_offset"] == [-60, 30, 0, 0]


def test_win32_keyboard_elimination_is_not_shifted(layout_context, monkeypatch, tmp_path):
    context = layout_context
    root = Path(__file__).resolve().parents[2]
    nodes = json.loads((root / "resource/pgr_win32/pipeline/Auto_Battle_action.jsonc").read_text(encoding="utf-8"))
    context.override_pipeline({f"消球{slot}": nodes[f"消球{slot}"] for slot in range(1, 9)})
    apply_calibration(context, monkeypatch, tmp_path, [80, -30, 0, 0], controller="win32")

    for slot in range(1, 9):
        action = context.get_node_data(f"消球{slot}")["action"]
        assert action["type"] == "ClickKey"
        assert action["param"]["key"] == [48 + slot]
        assert "target_offset" not in action["param"]
        assert "action" not in context.pipeline_overrides[-1][f"消球{slot}"]


def test_generated_map_is_reproducible_and_covers_all_elimination_actions():
    path = Path(load_module.__file__).with_name("combat_roi_zone_map.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    generated = build_node_zone_map()
    assert payload["node_zone_map"] == generated
    assert POINT_ZONE_MARGIN == 48
    assert all(generated[f"消球{slot}"] == "signal_zone" for slot in range(1, 9))
