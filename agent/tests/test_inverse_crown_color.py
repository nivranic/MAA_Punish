"""Color-only producer contracts; all images here are synthetic, not device proof."""

from copy import deepcopy
from dataclasses import fields
from types import SimpleNamespace

import numpy as np
import pytest

from action.combat.capture import FrameMetadata
from action.combat.config.inverse_crown_profile import InverseCrownProfile, InverseCrownUnavailable
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.input_control import CombatInputScope
from action.combat.roles.inverse_crown import (
    InverseCrown, _Signal, _stationary_special_color,
)
from test_support.fakes import (
    FakeBestResult, FakeClock, FakeContext, FakeController, FakeRecognitionResult,
)


def node(roi=None, offset=None):
    return {"recognition": {"type": "TemplateMatch", "param": {
        "roi": [10, 10, 104, 109] if roi is None else roi,
        "roi_offset": [0, 0, 0, 0] if offset is None else offset,
    }}}


def synthetic(color, *, image_shape=(150, 180, 3), rect=(10, 10, 104, 109)):
    """A colored center with a white diagonal mark and a contrary outer ring."""
    image = np.full(image_shape, 100, dtype=np.uint8)
    x, y, width, height = rect
    patch = image[y:y + height, x:x + width]
    patch[:] = (40, 40, 220) if color == "blue" else (220, 70, 40)
    yy, xx = np.ogrid[:height, :width]
    center = (xx - (width - 1) / 2) ** 2 + (yy - (height - 1) / 2) ** 2 < (min(width, height) * 0.25) ** 2
    patch[center] = (220, 140, 60) if color == "blue" else (70, 70, 220)
    patch[center & (np.abs(xx - yy + 2.5) < 4)] = (245, 245, 245)
    return image


@pytest.mark.parametrize("color", ["blue", "red"])
def test_synthetic_color_uses_center_not_contrary_outer_ring(color):
    assert _stationary_special_color(synthetic(color), node()) == color


@pytest.mark.parametrize("bgr", [
    (0, 0, 0), (100, 100, 100), (255, 255, 255), (0, 220, 220),
    (0, 220, 0), (75, 30, 0), (0, 30, 75), (200, 50, 200),
])
def test_synthetic_absent_weak_or_ambiguous_color_is_unknown(bgr):
    assert _stationary_special_color(np.full((150, 180, 3), bgr, dtype=np.uint8), node()) == "unknown"


@pytest.mark.parametrize("color", ["red", "blue"])
@pytest.mark.parametrize("obstruction", ["half_gray", "mixed", "quarter_gray"])
def test_synthetic_mixed_or_partly_obscured_center_is_unknown(color, obstruction):
    image = synthetic(color)
    if obstruction == "half_gray":
        image[10:119, 62:114] = 100
    elif obstruction == "mixed":
        image[10:119, 62:114] = (70, 70, 220) if color == "blue" else (220, 140, 60)
    else:
        image[64:119, 62:114] = 100
    assert _stationary_special_color(image, node()) == "unknown"


@pytest.mark.parametrize("bad_node", [
    None, [], {}, {"recognition": []}, {"recognition": {"param": None}},
    {"recognition": {"param": {"roi": [10, 10, 104, 109]}}},
    node(roi=[True, 10, 104, 109]), node(roi=[10.0, 10, 104, 109]),
    node(roi=[10, 10, 104]), node(roi=[10, 10, -104, 109]),
    node(roi=[10, 10, 19, 19]), node(roi=[-1, 10, 104, 109]),
    node(roi=[100, 10, 104, 109]), node(roi=[10, 100, 104, 109]),
    node(offset=[0, False, 0, 0]), node(offset=[0, 0, 1, 0]),
    node(offset=[0, 0, 0, -1]), node(offset=(0, 0, 0, 0)),
])
def test_bad_geometry_is_unknown_without_clipping(bad_node):
    assert _stationary_special_color(synthetic("blue"), bad_node) == "unknown"


@pytest.mark.parametrize("image", [
    None, [], np.zeros((0, 0, 3), dtype=np.uint8),
    np.zeros((150, 180), dtype=np.uint8), np.zeros((150, 180, 4), dtype=np.uint8),
    np.zeros((150, 180, 3), dtype=np.float32),
])
def test_invalid_image_fails_closed(image):
    assert _stationary_special_color(image, node()) == "unknown"


def test_effective_roi_applies_current_context_offset_once_without_mutating_it():
    image = synthetic("blue", image_shape=(180, 220, 3), rect=(70, 40, 104, 109))
    current = node(roi=[10, 10, 104, 109], offset=[60, 30, 0, 0])
    before = deepcopy(current)
    for _ in range(2):
        assert _stationary_special_color(image, current) == "blue"
        assert current == before
    assert _stationary_special_color(image, node()) == "unknown"
    current["recognition"]["param"]["roi_offset"] = [0, 0, 0, 0]
    assert _stationary_special_color(image, current) == "unknown"


def test_producer_only_reads_supplied_frame_and_one_context_node():
    image = synthetic("blue")
    before = image.copy()
    metadata = FrameMetadata(23, 11, 3.5, 99)
    reads = []

    class NodeOnlyContext:
        def get_node_data(self, name):
            reads.append(name)
            assert name == "检查逆冕特殊球"
            return node()

        def __getattr__(self, name):
            pytest.fail(f"producer accessed forbidden Context member: {name}")

    role = InverseCrown.__new__(InverseCrown)
    scope = SimpleNamespace(frame_metadata=metadata)
    role.combat = SimpleNamespace(context=NodeOnlyContext(), input_scope=scope, frame=image)
    snapshot = role._read_stationary_signals(image)
    assert snapshot.special_color == "blue" and snapshot.hp is None
    assert (snapshot.generation, snapshot.sequence, snapshot.captured_at) == (23, 11, 3.5)
    assert scope.frame_metadata is metadata and role.combat.frame is image
    assert np.array_equal(before, image) and reads == ["检查逆冕特殊球"]
    assert snapshot.initial_basis == "unknown"
    assert all(getattr(snapshot, field.name) is _Signal.UNKNOWN for field in fields(snapshot)
               if field.name not in {"generation", "sequence", "captured_at", "hp", "special_color", "initial_basis", "resource_appearance"})


def test_context_read_failure_is_unknown_without_retaining_previous_geometry():
    role = InverseCrown.__new__(InverseCrown)
    context = FakeContext(node_data={"检查逆冕特殊球": node()})
    role.combat = SimpleNamespace(context=context, input_scope=SimpleNamespace(
        frame_metadata=FrameMetadata(1, 1, 0.0, 1)))
    image = synthetic("red")
    assert role._read_stationary_signals(image).special_color == "red"

    def failed(_):
        raise RuntimeError("Context unavailable")

    context.get_node_data = failed
    assert role._read_stationary_signals(image).special_color == "unknown"


@pytest.mark.parametrize("color", ["blue", "red"])
def test_color_alone_cannot_emit_down_or_unlock_either_live_gate(color, monkeypatch):
    clock = FakeClock()
    controller = FakeController(image=synthetic(color))
    context = FakeContext(
        node_data={"检查逆冕特殊球": node()},
        recognition_map={"检查血量": FakeRecognitionResult(hit=True,
            best_result=FakeBestResult(box=(1, 1, 10, 10), text="100/100"))},
    )
    context.tasker.controller = controller
    combat = CombatTask(context, CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=True, scene="single"))
    combat.input_scope = CombatInputScope(context, combat, generation=23,
        monotonic=clock.monotonic, sleep=clock.sleep)
    combat.input_scope.capture_frame()
    monkeypatch.setattr(controller, "post_screencap", lambda: pytest.fail("unexpected extra capture"))
    role = InverseCrown(combat, "R", "InverseCrown")
    assert role._read_stationary_signals(combat.frame).special_color == color
    role.do_perform()
    assert role.phase == "prepare"
    assert controller.touches == controller.clicks == context.actions == []
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        role.perform()
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        combat.combat_once()
    assert controller.touches == controller.clicks == context.actions == []
