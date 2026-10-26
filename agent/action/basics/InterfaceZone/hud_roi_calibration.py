"""Explicit HUD rectangles stored beside the existing eight button zones.

The source hash is provenance only. Neither it nor these geometry checks prove
that an image contains the named HUD element or that a game signal is ready.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from action.basics.InterfaceZone.roi_zone_controller import (
    ZONES_BY_CONTROLLER,
    zones_for,
)

HUD_CALIBRATION_KEY = "hud_roi_calibration"
HUD_NODE_NAMES = frozenset(("检查血量", "检查逆冕能量空"))
_PAYLOAD_KEYS = frozenset(("schema", "screen_size", "source_frame_sha256", "nodes"))
_SAVED_KEYS = _PAYLOAD_KEYS | {"controller", "layout_fingerprint"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class HudRoiCalibrationError(ValueError):
    """A requested HUD calibration is absent, malformed or stale."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise HudRoiCalibrationError(reason)


def _screen_size(value: Any) -> list[int]:
    _require(
        isinstance(value, (list, tuple)) and len(value) == 2
        and all(type(item) is int and item > 0 for item in value),
        "hud_screen_size_invalid",
    )
    return list(value)


def _rect(value: Any, screen_size: list[int], reason: str) -> list[int]:
    _require(
        isinstance(value, list) and len(value) == 4
        and all(type(item) is int for item in value), reason,
    )
    x, y, width, height = value
    _require(
        x >= 0 and y >= 0 and width > 0 and height > 0
        and x + width <= screen_size[0] and y + height <= screen_size[1],
        reason,
    )
    return list(value)


def _layout_fingerprint(zone_data: Any, controller: str, screen_size: list[int]) -> str:
    _require(type(controller) is str and controller in ZONES_BY_CONTROLLER,
             "hud_controller_invalid")
    _require(isinstance(zone_data, dict), "hud_layout_invalid")
    normalized = {}
    for name, _, default in zones_for(controller):
        zone = zone_data.get(name)
        _require(isinstance(zone, dict) and zone.get("hit") is True,
                 f"hud_layout_zone_missing:{name}")
        stored_default = _rect(zone.get("default"), screen_size,
                               f"hud_layout_default_invalid:{name}")
        _require(stored_default == default, f"hud_layout_reference_mismatch:{name}")
        recognized = _rect(zone.get("recognized"), screen_size,
                           f"hud_layout_recognition_invalid:{name}")
        offset = zone.get("offset")
        _require(isinstance(offset, list) and len(offset) == 4
                 and all(type(item) is int for item in offset),
                 f"hud_layout_offset_invalid:{name}")
        _require(recognized == [base + shift for base, shift in zip(default, offset)],
                 f"hud_layout_geometry_mismatch:{name}")
        normalized[name] = dict(default=stored_default, recognized=recognized,
                                offset=list(offset), hit=True)
    encoded = json.dumps(dict(controller=controller, zones=normalized),
                         ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_hud_calibration(payload: Any, controller: str, screen_size: Any,
                         zone_data: Any) -> dict:
    """Validate explicit rectangles and bind a copied block to these eight zones."""
    size = _screen_size(screen_size)
    _require(isinstance(payload, dict) and set(payload) == _PAYLOAD_KEYS,
             "hud_payload_fields_invalid")
    _require(type(payload["schema"]) is int and payload["schema"] == 1,
             "hud_schema_invalid")
    _require(isinstance(payload["screen_size"], list)
             and _screen_size(payload["screen_size"]) == size,
             "hud_screen_size_mismatch")
    source = payload["source_frame_sha256"]
    _require(type(source) is str and _SHA256.fullmatch(source) is not None,
             "hud_source_hash_invalid")
    nodes = payload["nodes"]
    _require(isinstance(nodes, dict) and set(nodes) == HUD_NODE_NAMES,
             "hud_node_fields_invalid")
    rectangles = {name: _rect(nodes[name], size, f"hud_roi_invalid:{name}")
                  for name in sorted(HUD_NODE_NAMES)}
    fingerprint = _layout_fingerprint(zone_data, controller, size)
    return dict(schema=1, controller=controller, screen_size=size,
                source_frame_sha256=source, layout_fingerprint=fingerprint,
                nodes=rectangles)


def hud_overrides_from_cache(cache_data: Any, controller: str,
                             screen_size: Any) -> dict:
    """Require the exact saved block and return only the two ROI overrides.

    This function does not capture, read files, obtain a Controller or change
    pipeline thresholds. Callers supply the already observed screen geometry.
    """
    _require(isinstance(cache_data, dict), "hud_cache_invalid")
    saved = cache_data.get(HUD_CALIBRATION_KEY)
    _require(isinstance(saved, dict) and set(saved) == _SAVED_KEYS,
             "hud_saved_fields_invalid")
    _require(saved["controller"] == controller, "hud_controller_mismatch")
    expected = make_hud_calibration(
        {key: saved[key] for key in _PAYLOAD_KEYS}, controller, screen_size,
        cache_data,
    )
    _require(type(saved["layout_fingerprint"]) is str
             and saved["layout_fingerprint"] == expected["layout_fingerprint"],
             "hud_layout_fingerprint_mismatch")
    return {
        name: {"recognition": {"param": {"roi": list(rect),
                                          "roi_offset": [0, 0, 0, 0]}}}
        for name, rect in expected["nodes"].items()
    }
