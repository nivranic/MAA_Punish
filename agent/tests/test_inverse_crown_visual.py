"""Restricted observations; real ROI/synthetic contexts are not device acceptance."""

from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from action.basics.InterfaceZone import roi_zone_controller as zones
from action.basics.InterfaceZone.hud_roi_calibration import HUD_CALIBRATION_KEY, make_hud_calibration
from action.combat.config.inverse_crown_profile import InverseCrownProfile, InverseCrownUnavailable
from action.combat.core.provider import CombatCheck
from action.combat.core.session import CombatTask
from action.combat.input_control import CombatInputError, CombatInputScope
from action.combat.roles import inverse_crown as module
from test_support.fakes import FakeBestResult, FakeClock, FakeContext, FakeController, FakeRecognitionResult


YES, UNKNOWN = module._Signal.YES, module._Signal.UNKNOWN


def appearance_frame(geometry, *, resource="full", blue=True):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, width, height = geometry.resource_roi
    image[y:y + height, x:x + width] = (50, 70, 180) if resource == "full" else (60, 60, 80)
    image[y:y + height, [x - 1, x + width]] = (50, 70, 180)
    if blue:
        x, y, width, height = geometry.skill_roi
        cx, cy = geometry.skill_center
        yy, xx = np.ogrid[y:y + height, x:x + width]
        radius = np.hypot(xx - cx, yy - cy)
        patch = image[y:y + height, x:x + width]
        patch[(radius >= 0.41 * min(width, height)) & (radius <= 0.50 * min(width, height))] = (220, 150, 50)
        patch[radius < 10] = (230, 230, 230)
    return image


@pytest.fixture
def calibrated(tmp_path, monkeypatch):
    path = tmp_path / "adb_zone_offset.json"
    monkeypatch.setitem(zones.OFFSET_FILES, "adb", path)
    cache = {name: {"default": list(default), "recognized": list(default),
                    "offset": [0, 0, 0, 0], "hit": True}
             for name, _, default in zones.zones_for("adb")}
    cache["skill_zone"].update(recognized=[942, 594, 124, 126], offset=[89, 23, 0, 0])
    # Different positions from the private PNGs prove that coordinates come
    # from the supplied calibration rather than production sample constants.
    payload = {"schema": 1, "screen_size": [1280, 720], "source_frame_sha256": "a" * 64,
               "nodes": {"检查血量": [20, 10, 140, 29], "检查逆冕能量空": [80, 50, 213, 3]}}
    cache[HUD_CALIBRATION_KEY] = make_hud_calibration(payload, "adb", (1280, 720), cache)
    path.write_text(json.dumps(cache), encoding="utf-8")
    nodes = {
        "覆盖布局菜单": {"recognition": {"type": "Custom", "param": {
            "custom_recognition": "CheckROIZoneCache", "custom_recognition_param": {"controller": "adb"}}}},
        "识别区域": {"action": {"type": "Custom", "param": {
            "custom_action": "LoadROIZone", "custom_action_param": {"controller": "adb"}}}},
        "技能": {"action": {"type": "Click", "param": {
            "target": [915, 634], "target_offset": [89, 23, 0, 0]}}},
    }
    for name, kind in (("检查血量", "OCR"), ("检查逆冕能量空", "ColorMatch")):
        nodes[name] = {"recognition": {"type": kind, "param": {
            "roi": list(payload["nodes"][name]), "roi_offset": [0, 0, 0, 0]}}}
    context = FakeContext(node_data=nodes, recognition_map={"检查血量": FakeRecognitionResult(
        hit=True, best_result=FakeBestResult(box=(20, 10, 140, 29), text="100/100"))})
    geometry = module._StationaryVisualGeometry((1280, 720), (80, 50, 213, 3),
                                               (942, 594, 124, 126), (1004, 657),
                                               tuple(cache["corepass_zone"]["recognized"]))
    image = appearance_frame(geometry)
    controller = FakeController(image=image)
    context.tasker.controller = controller
    clock = FakeClock()
    combat = CombatTask(context, CombatCheck(), inverse_crown_profile=InverseCrownProfile(enabled=True, scene="single"))
    combat.input_scope = CombatInputScope(context, combat, generation=23,
                                         monotonic=clock.monotonic, sleep=clock.sleep)
    combat.input_scope.capture_frame()
    role = module.InverseCrown(combat, "R", "InverseCrown")
    role._enter_stationary("prepare")
    monkeypatch.setattr(controller, "post_screencap", Mock(side_effect=AssertionError("no additional capture")))
    return SimpleNamespace(role=role, combat=combat, context=context, controller=controller,
                           clock=clock, geometry=geometry, image=combat.frame, cache=cache, path=path)


def test_same_scope_positive_hp_produces_appearance_without_actions(calibrated):
    case = calibrated
    before = case.image.copy()
    assert case.role._stationary_visual_geometry(case.image) == case.geometry
    snapshot = case.role._observe_stationary(case.image)
    assert snapshot.hp.alive and snapshot.resource_appearance == "full" and snapshot.skill_blue_ring is YES
    assert snapshot.initial_basis == "unknown"
    assert all(getattr(snapshot, item.name) is UNKNOWN for item in fields(snapshot)
               if item.name not in {"generation", "sequence", "captured_at", "hp", "special_color", "initial_basis",
                                    "resource_appearance", "skill_blue_ring"})
    assert np.array_equal(case.image, before)
    case.controller.post_screencap.assert_not_called()
    assert case.controller.touches == case.controller.clicks == case.context.actions == case.context.pipeline_overrides == []


@pytest.mark.parametrize("resource", ["empty", "full"])
def test_complete_supported_bar_has_only_positive_appearance(calibrated, resource):
    case = calibrated
    image = appearance_frame(case.geometry, resource=resource)
    assert module._stationary_resource_appearance(image, case.geometry) == resource
    assert module._stationary_skill_blue_ring(image, case.geometry) is YES


@pytest.mark.parametrize("obstruction", ["black", "white", "one_dark_column", "partial_gray", "missing_boundary"])
def test_resource_uncertainty_does_not_become_full_or_empty(calibrated, obstruction):
    geometry = calibrated.geometry
    image = appearance_frame(geometry, resource="empty" if obstruction == "missing_boundary" else "full")
    x, y, width, height = geometry.resource_roi
    if obstruction == "black":
        image[:] = 0
    elif obstruction == "white":
        image[:] = 255
    elif obstruction == "one_dark_column":
        image[y:y + height, x + width - 1] = (60, 60, 80)
    elif obstruction == "partial_gray":
        image[y:y + height, x + width // 2:x + width // 2 + 4] = 150
    else:
        image[y:y + height, x - 1] = 0
    assert module._stationary_resource_appearance(image, geometry) == "unknown"
    if obstruction in {"black", "white"}:
        assert module._stationary_skill_blue_ring(image, geometry) is UNKNOWN


def test_local_blue_patch_cannot_establish_a_distributed_ring(calibrated):
    geometry = calibrated.geometry
    image = appearance_frame(geometry, blue=False)
    x, y, _, _ = geometry.skill_roi
    image[y:y + 30, x:x + 30] = (220, 150, 50)
    assert module._stationary_skill_blue_ring(image, geometry) is UNKNOWN


@pytest.mark.parametrize("drift", ["roi", "offset", "skill_target", "cache_binding"])
def test_current_context_or_cache_drift_discards_geometry(calibrated, drift):
    case = calibrated
    assert case.role._read_stationary_signals(case.image).resource_appearance == "full"
    if drift == "roi":
        case.context.node_data["检查逆冕能量空"]["recognition"]["param"]["roi"][0] += 1
    elif drift == "offset":
        case.context.node_data["检查血量"]["recognition"]["param"]["roi_offset"][0] = 1
    elif drift == "skill_target":
        case.context.node_data["技能"]["action"]["param"]["target"][1] += 1
    else:
        case.cache["skill_zone"]["recognized"][0] += 1
        case.path.write_text(json.dumps(case.cache), encoding="utf-8")
    snapshot = case.role._read_stationary_signals(case.image)
    assert snapshot.resource_appearance == "unknown" and snapshot.skill_blue_ring is UNKNOWN
    case.controller.post_screencap.assert_not_called()
    assert not case.controller.touches and not case.context.actions and not case.context.pipeline_overrides


def test_unsupported_dimensions_are_unknown_in_pure_helpers(calibrated):
    case = calibrated
    geometry = replace(case.geometry, resource_roi=(80, 50, 212, 3))
    assert module._stationary_resource_appearance(case.image, geometry) == "unknown"
    assert module._stationary_skill_blue_ring(case.image, geometry) is UNKNOWN
    assert module._stationary_resource_appearance(case.image[:, :-1], case.geometry) == "unknown"
    assert module._stationary_skill_blue_ring(case.image[:, :-1], case.geometry) is UNKNOWN


@pytest.mark.parametrize("drift", ["resource_roi", "hp_offset", "controller_binding"])
def test_later_skill_read_cannot_reuse_earlier_hud_or_controller_binding(calibrated, monkeypatch, drift):
    case = calibrated
    read_node = case.context.get_node_data

    def drift_on_skill(name):
        node = read_node(name)
        if name == "技能":
            if drift == "resource_roi":
                case.context.node_data["检查逆冕能量空"]["recognition"]["param"]["roi"][0] += 1
            elif drift == "hp_offset":
                case.context.node_data["检查血量"]["recognition"]["param"]["roi_offset"][0] = 1
            else:
                case.context.node_data["识别区域"]["action"]["param"]["custom_action_param"]["controller"] = "win32"
        return node

    monkeypatch.setattr(case.context, "get_node_data", drift_on_skill)
    snapshot = case.role._observe_stationary(case.image)
    assert snapshot.resource_appearance == "unknown" and snapshot.skill_blue_ring is UNKNOWN
    case.controller.post_screencap.assert_not_called()
    assert case.controller.touches == case.context.actions == case.context.pipeline_overrides == []


@pytest.mark.parametrize("fault", ["hp", "stale", "frame_identity", "stop"])
def test_failed_observation_never_reaches_visual_reader_or_new_input(calibrated, monkeypatch, fault):
    case = calibrated
    if fault == "hp":
        case.context.recognition_map["检查血量"] = FakeRecognitionResult(hit=False)
    elif fault == "stale":
        case.clock.now = 1.1
    elif fault == "frame_identity":
        case.combat.frame = case.image.copy()
    else:
        case.context.tasker.stopping = True
    reader = Mock(side_effect=AssertionError("visual reader must follow same-frame positive HP"))
    monkeypatch.setattr(case.role, "_read_stationary_signals", reader)
    with pytest.raises(CombatInputError):
        case.role._observe_stationary(case.image)
    reader.assert_not_called()
    case.controller.post_screencap.assert_not_called()
    assert not case.controller.touches and not case.context.actions


def test_appearance_field_domains_are_strict():
    for values in ({"resource_appearance": True}, {"resource_appearance": "FULL"},
                   {"resource_appearance": module._Signal.YES}, {"skill_blue_ring": "yes"},
                   {"skill_blue_ring": module._Signal.NO}):
        with pytest.raises(ValueError):
            module._StationarySignals(1, 1, 0.0, **values)


def test_stop_during_context_read_rejects_even_positive_appearance(calibrated, monkeypatch):
    case = calibrated
    read_node = case.context.get_node_data

    def stop_on_skill(name):
        node = read_node(name)
        if name == "技能":
            case.context.tasker.stopping = True
        return node

    monkeypatch.setattr(case.context, "get_node_data", stop_on_skill)
    with pytest.raises(CombatInputError):
        case.role._observe_stationary(case.image)
    case.controller.post_screencap.assert_not_called()
    assert not case.controller.touches and not case.context.actions


def test_full_bar_and_blue_ring_cannot_issue_down_or_unlock_gates(calibrated):
    case = calibrated
    case.role.do_perform()
    assert case.role.phase == "prepare"
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        case.role.perform()
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        case.combat.combat_once()
    case.controller.post_screencap.assert_not_called()
    assert case.controller.touches == case.controller.clicks == case.context.actions == []


PHASE_FIXTURES = Path(__file__).parent / "fixtures" / "inverse_crown_phase_shape"
PHASE_SAMPLES = json.loads((PHASE_FIXTURES / "manifest.json").read_text("utf-8"))["samples"]


def phase_roi(name):
    sample = next(item for item in PHASE_SAMPLES if item["file"] == name + ".png")
    raw = (PHASE_FIXTURES / sample["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sample["fixture_sha256"]
    roi = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert roi.shape == (sample["roi"][3], sample["roi"][2], 3)
    assert hashlib.sha256(roi.tobytes()).hexdigest() == sample["fixture_bgr_sha256"]
    return roi


@pytest.fixture
def phase_calibrated(calibrated):
    """Real core pixels in a synthetic canvas/HP/Context, not a live frame."""
    case = calibrated
    payload = {"schema": 1, "screen_size": [1280, 720], "source_frame_sha256": "b" * 64,
               "nodes": {"检查血量": [70, 15, 140, 29], "检查逆冕能量空": [76, 62, 213, 3]}}
    case.cache[HUD_CALIBRATION_KEY] = make_hud_calibration(payload, "adb", (1280, 720), case.cache)
    case.path.write_text(json.dumps(case.cache), encoding="utf-8")
    for name, roi in payload["nodes"].items():
        case.context.node_data[name]["recognition"]["param"]["roi"] = roi
    case.geometry = replace(case.geometry, resource_roi=(76, 62, 213, 3))
    case.image[:] = appearance_frame(case.geometry)
    case.image[619:638, 539:605] = phase_roi("a7_smooth_long")
    return case


@pytest.mark.parametrize("sample", PHASE_SAMPLES, ids=lambda sample: sample["file"])
def test_real_core_roi_candidates_preserve_unknown_negatives(phase_calibrated, sample):
    case = phase_calibrated
    case.image[619:638, 539:605] = phase_roi(Path(sample["file"]).stem)
    case.role._enter_stationary("special_hold")
    before = case.image.copy()
    snapshot = case.role._observe_stationary(case.image)
    assert snapshot.abyss_phase.value == sample["candidate_signal"]
    assert snapshot.initial_basis == "unknown"
    assert all(getattr(snapshot, item.name) is UNKNOWN for item in fields(snapshot)
               if item.name not in {"generation", "sequence", "captured_at", "hp", "special_color", "initial_basis",
                                    "resource_appearance", "skill_blue_ring", "abyss_phase"})
    assert np.array_equal(case.image, before)
    case.controller.post_screencap.assert_not_called()
    assert case.controller.touches == case.controller.clicks == case.context.actions == case.context.pipeline_overrides == []


@pytest.mark.parametrize("phase", ["special_hold", "attack_settlement"])
def test_positive_phase_is_consumed_only_in_own_single_action_context(phase_calibrated, phase):
    case = phase_calibrated
    case.role._enter_stationary(phase)
    assert case.role._observe_stationary(case.image).abyss_phase is YES
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        case.role.perform()
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        case.combat.combat_once()
    assert not case.controller.touches and not case.context.actions


@pytest.mark.parametrize("phase", ["idle", "prepare", "ultimate_charge", "ultimate_finish",
                                   "slash_attack", "recover", "special_press", "special_attack", "skill"])
def test_shared_core_shape_has_no_phase_meaning_in_other_contexts(phase_calibrated, phase):
    case = phase_calibrated
    case.role.phase = phase
    assert case.role._read_stationary_signals(case.image).abyss_phase is UNKNOWN


@pytest.mark.parametrize("profile", [InverseCrownProfile(), InverseCrownProfile(enabled=True, scene="multi")])
def test_shape_cannot_bypass_profile_or_scene(phase_calibrated, profile):
    case = phase_calibrated
    case.role._enter_stationary("special_hold")
    case.role.inverse_crown_profile = profile
    assert case.role._read_stationary_signals(case.image).abyss_phase is UNKNOWN


@pytest.mark.parametrize("drift", ["resource", "skill", "core", "missing_core", "dimensions", "dtype"])
def test_core_shape_rejects_unverified_geometry(phase_calibrated, drift):
    case = phase_calibrated
    image, geometry = case.image, case.geometry
    if drift == "resource":
        geometry = replace(geometry, resource_roi=(77, 62, 213, 3))
    elif drift == "skill":
        geometry = replace(geometry, skill_roi=(943, 594, 124, 126), skill_center=(1005, 657))
    elif drift == "core":
        geometry = replace(geometry, core_roi=(494, 602, 294, 50))
    elif drift == "missing_core":
        geometry = replace(geometry, core_roi=None)
    elif drift == "dimensions":
        image = image[:, :-1]
    else:
        image = image.astype(np.float32)
    assert module._stationary_abyss_shape(image, geometry) is UNKNOWN


def test_core_roi_is_read_from_current_valid_cache(phase_calibrated):
    case = phase_calibrated
    case.cache["corepass_zone"]["recognized"][0] += 1
    case.cache["corepass_zone"]["offset"][0] += 1
    payload = {"schema": 1, "screen_size": [1280, 720], "source_frame_sha256": "c" * 64,
               "nodes": {name: list(case.context.node_data[name]["recognition"]["param"]["roi"])
                         for name in ("检查血量", "检查逆冕能量空")}}
    case.cache[HUD_CALIBRATION_KEY] = make_hud_calibration(payload, "adb", (1280, 720), case.cache)
    case.path.write_text(json.dumps(case.cache), encoding="utf-8")
    case.role._enter_stationary("special_hold")
    assert case.role._stationary_visual_geometry(case.image).core_roi == (494, 602, 294, 50)
    assert case.role._observe_stationary(case.image).abyss_phase is UNKNOWN
    case.controller.post_screencap.assert_not_called()
    assert not case.controller.touches and not case.context.actions


@pytest.mark.parametrize("change", ["shift_x", "shift_y", "mirror", "missing_root", "filled_notch", "white", "grey"])
def test_smooth_shape_needs_hilt_root_and_unobscured_contour(phase_calibrated, change):
    case = phase_calibrated
    patch = case.image[619:638, 539:605]
    if change == "shift_x":
        patch[:] = np.roll(patch, 3, axis=1)
    elif change == "shift_y":
        patch[:] = np.roll(patch, 2, axis=0)
    elif change == "mirror":
        patch[:] = patch[::-1].copy()
    elif change == "missing_root":
        patch[:, 43:58] = 0
    elif change == "filled_notch":
        patch[9, 19:23] = patch[9, 4]
    elif change == "white":
        patch[:] = 255
    else:
        patch[:] = cv2.cvtColor(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    assert module._stationary_abyss_shape(case.image, case.geometry) is UNKNOWN


def test_phase_change_during_existing_geometry_read_discards_shape(phase_calibrated, monkeypatch):
    case = phase_calibrated
    case.role._enter_stationary("special_hold")
    read_node = case.context.get_node_data

    def change_phase(name):
        if name == "技能":
            case.role._enter_stationary("ultimate_charge")
        return read_node(name)

    monkeypatch.setattr(case.context, "get_node_data", change_phase)
    assert case.role._observe_stationary(case.image).abyss_phase is UNKNOWN
    case.controller.post_screencap.assert_not_called()
    assert not case.controller.touches and not case.context.actions


READY_FIXTURES = Path(__file__).parent / "fixtures" / "inverse_crown_ready"
READY_SAMPLES = json.loads((READY_FIXTURES / "manifest.json").read_text("utf-8"))["samples"]


def apply_ready_sample(case, sample):
    """Retain real bar/ring pixels on the existing synthetic Context canvas."""
    for crop in sample["crops"]:
        raw = (READY_FIXTURES / crop["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == crop["sha256"]
        pixels = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        x, y, width, height = crop["roi"]
        assert pixels.shape == (height, width, 3)
        assert hashlib.sha256(pixels.tobytes()).hexdigest() == crop["bgr_sha256"]
        case.image[y:y + height, x:x + width] = pixels


@pytest.fixture
def ready_calibrated(phase_calibrated):
    case = phase_calibrated
    apply_ready_sample(case, next(sample for sample in READY_SAMPLES if sample["name"] == "full_clear_hud"))
    return case


@pytest.mark.parametrize("sample", READY_SAMPLES, ids=lambda sample: sample["name"])
def test_real_ready_pair_keeps_partial_or_obstructed_samples_unknown(phase_calibrated, sample):
    case = phase_calibrated
    apply_ready_sample(case, sample)
    before = case.image.copy()
    snapshot = case.role._observe_stationary(case.image)
    assert snapshot.resource_appearance == sample["resource_appearance"]
    assert snapshot.skill_blue_ring.value == sample["skill_blue_ring"]
    assert snapshot.ultimate_ready.value == sample["candidate_ready"]
    assert snapshot.initial_basis == "unknown"
    assert all(getattr(snapshot, item.name) is UNKNOWN for item in fields(snapshot)
               if item.name not in {"generation", "sequence", "captured_at", "hp", "special_color", "initial_basis",
                                    "resource_appearance", "skill_blue_ring", "ultimate_ready"})
    assert np.array_equal(case.image, before)
    case.controller.post_screencap.assert_not_called()
    assert case.controller.touches == case.controller.clicks == case.context.actions == case.context.pipeline_overrides == []


@pytest.mark.parametrize("phase", ["special_hold", "attack_settlement", "ultimate_charge", "ultimate_finish", "slash_attack", "skill"])
def test_ready_pair_has_no_availability_meaning_outside_prepare(ready_calibrated, phase):
    case = ready_calibrated
    case.role.phase = phase
    snapshot = case.role._read_stationary_signals(case.image)
    assert snapshot.resource_appearance == "full" and snapshot.skill_blue_ring is YES
    assert snapshot.ultimate_ready is UNKNOWN


@pytest.mark.parametrize("profile", [InverseCrownProfile(), InverseCrownProfile(enabled=True, scene="multi")])
def test_ready_pair_does_not_extend_profile_or_scene(ready_calibrated, profile):
    case = ready_calibrated
    case.role.inverse_crown_profile = profile
    assert case.role._read_stationary_signals(case.image).ultimate_ready is UNKNOWN


@pytest.mark.parametrize("zone", ["resource", "skill", "core"])
def test_valid_shifted_layout_keeps_appearance_but_not_ready_semantics(ready_calibrated, zone):
    case = ready_calibrated
    if zone == "resource":
        case.image[62:65, 77:290] = case.image[62:65, 76:289].copy()
        case.context.node_data["检查逆冕能量空"]["recognition"]["param"]["roi"][0] += 1
    elif zone == "skill":
        case.image[594:720, 943:1067] = case.image[594:720, 942:1066].copy()
        case.cache["skill_zone"]["recognized"][0] += 1
        case.cache["skill_zone"]["offset"][0] += 1
        case.context.node_data["技能"]["action"]["param"]["target_offset"][0] += 1
    else:
        case.cache["corepass_zone"]["recognized"][0] += 1
        case.cache["corepass_zone"]["offset"][0] += 1
    payload = {"schema": 1, "screen_size": [1280, 720], "source_frame_sha256": "d" * 64,
               "nodes": {name: list(case.context.node_data[name]["recognition"]["param"]["roi"])
                         for name in ("检查血量", "检查逆冕能量空")}}
    case.cache[HUD_CALIBRATION_KEY] = make_hud_calibration(payload, "adb", (1280, 720), case.cache)
    case.path.write_text(json.dumps(case.cache), encoding="utf-8")
    assert case.role._stationary_visual_geometry(case.image) is not None
    snapshot = case.role._observe_stationary(case.image)
    assert snapshot.resource_appearance == "full" and snapshot.skill_blue_ring is YES
    assert snapshot.ultimate_ready is UNKNOWN


def test_prepare_change_during_geometry_read_discards_ready(ready_calibrated, monkeypatch):
    case = ready_calibrated
    read_node = case.context.get_node_data

    def change_phase(name):
        if name == "技能":
            case.role._enter_stationary("special_hold")
        return read_node(name)

    monkeypatch.setattr(case.context, "get_node_data", change_phase)
    snapshot = case.role._observe_stationary(case.image)
    assert snapshot.resource_appearance == "full" and snapshot.skill_blue_ring is YES
    assert snapshot.ultimate_ready is UNKNOWN


@pytest.mark.parametrize("pending,settlement", [(False, UNKNOWN), (False, YES), (True, module._Signal.NO)])
def test_actual_ready_producer_cannot_bypass_necessary_settlement(ready_calibrated, monkeypatch, pending, settlement):
    case = ready_calibrated
    read = case.role._read_stationary_signals
    observed = []

    def add_independent_settlement(image):
        snapshot = read(image)
        observed.append(snapshot.ultimate_ready)
        return replace(snapshot, settlement_required=settlement)

    monkeypatch.setattr(case.role, "_read_stationary_signals", add_independent_settlement)
    case.role._settlement_pending = pending
    case.role.do_perform()
    assert observed == [YES]
    assert case.role._settlement_pending is (pending or settlement is YES)
    assert case.controller.touches == case.controller.clicks == case.context.actions == []
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        case.role.perform()
    with pytest.raises(InverseCrownUnavailable, match="strategy_not_ready"):
        case.combat.combat_once()
    case.controller.post_screencap.assert_not_called()
