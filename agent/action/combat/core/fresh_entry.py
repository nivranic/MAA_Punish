"""One invocation's preparation-page transition, never a saved fresh flag.

The normal HUD entry does not call this module. Android/game acceptance is
still pending; both existing strategy-not-ready guards remain in place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from action.combat.config.inverse_crown_profile import (
    InverseCrownProfileError, InverseCrownUnavailable,
)


PREPARE_NODE = "逆冕准备页开始"
START_NODE = "逆冕本次作战开始点击"
_PREPARE_MAX_AGE = 0.75


def preparation_entry_requested(node_name, raw_param) -> bool:
    """Only the dedicated opt-in node can request a battle start."""
    try:
        param = json.loads(raw_param) if isinstance(raw_param, str) and raw_param else raw_param
    except (TypeError, ValueError) as exc:
        raise InverseCrownProfileError("invalid combat entry parameter") from exc
    if param is None or param == "":
        param = {}
    if type(param) is not dict:
        raise InverseCrownProfileError("invalid combat entry parameter")
    if param == {} and node_name != PREPARE_NODE:
        return False
    if node_name == PREPARE_NODE and param == {"entry_mode": "preparation"}:
        return True
    raise InverseCrownProfileError("unsupported combat entry mode")


def _refuse(reason):
    raise InverseCrownUnavailable(f"fresh_entry_{reason}")


def _rect(box):
    if hasattr(box, "x"):
        box = (box.x, box.y, box.w, box.h)
    if not isinstance(box, (tuple, list)) or len(box) != 4 or any(type(v) is not int for v in box):
        _refuse("box_invalid")
    x, y, w, h = box
    if not (967 <= x < x + w <= 1180 and 608 <= y < y + h <= 720):
        _refuse("box_outside_preparation")
    return tuple(box)


def _profile_supported(profile):
    return (profile.enabled and profile.scene == "single" and profile.source == "self"
            and profile.signature_weapon_confirmed)


@dataclass(slots=True)
class FreshEntryCandidate:
    """A successful Start is only a candidate until this invocation sees HUD."""

    combat: object
    context: object
    scope: object
    generation: int
    profile: object
    start_action_id: int
    start_sequence: int
    started_at: float
    role: object = None
    retired: bool = False

    def retire(self):
        self.retired = True

    def _same_owner(self, combat):
        scope = combat.input_scope
        return (
            not self.retired and combat is self.combat and combat.context is self.context
            and scope is self.scope and scope.context is self.context
            and scope.generation == self.generation and not scope.closed and not scope.blocked
            and not scope.stop_unconfirmed and scope.pending_capture is None
            and not self.context.tasker.stopping
            and combat.inverse_crown_profile == self.profile and _profile_supported(self.profile)
            and scope.owned_contact is None and not scope.release_pending and not scope.capture_in_flight
        )

    def bind_first_hud(self, combat):
        """Called once, immediately after the existing pure-observation load_team."""
        from action.combat.core.role_factory import require_inverse_crown_identity

        role = combat.get_current_role()
        metadata = self.scope.frame_metadata
        if (self.role is not None or not self._same_owner(combat)
                or role is None or combat.field_identity != "confirmed"
                or not combat.combat_ui_visible or combat.frame is None
                or metadata is None or metadata.generation != self.generation
                or metadata.sequence <= self.start_sequence or metadata.captured_at < self.started_at):
            self.retire()
            _refuse("first_hud_unconfirmed")
        try:
            require_inverse_crown_identity(role)
        except BaseException:
            self.retire()
            raise
        image = combat.frame
        hud = self.context.run_recognition("战斗中", image)
        hp = combat.combat_check.read_hp_from_frame(self.context, image)
        if (hud is None or hud.hit is not True or not hp.alive
                or not self._same_owner(combat) or combat.frame is not image
                or self.scope.frame_metadata is not metadata
                or not 0 <= self.scope.monotonic() - metadata.captured_at <= _PREPARE_MAX_AGE):
            self.retire()
            _refuse("first_hud_unconfirmed")
        self.role = role
        combat.logger.info("inverse_crown initial_basis=same_call_entry_rule_inference start_action=%s generation=%s",
                           self.start_action_id, self.generation)

    def available(self, combat, role):
        if (not self._same_owner(combat) or self.role is not role
                or combat.get_current_role() is not role or combat.field_identity != "confirmed"
                or not combat.combat_ui_visible or role.phase != "prepare"
                or role._settlement_pending):
            self.retire()
            return False
        return True


def begin_from_preparation(combat) -> FreshEntryCandidate:
    """At most one native Click; the caller's existing readiness guard runs first."""
    profile = combat.inverse_crown_profile
    if not _profile_supported(profile):
        _refuse("battle_facts_required")
    if combat._entry_start_attempted:
        _refuse("start_already_attempted")
    context, scope = combat.context, combat.input_scope
    scope.assert_active()
    generation = scope.generation
    image = combat._capture_frame()
    metadata = scope.frame_metadata
    if image.shape != (720, 1280, 3):
        _refuse("screen_unsupported")
    preparation = context.get_node_data(PREPARE_NODE)
    recognition = preparation.get("recognition", {}) if isinstance(preparation, dict) else {}
    reco_param = recognition.get("param", {}) if isinstance(recognition, dict) else {}
    if (not isinstance(recognition, dict) or not isinstance(reco_param, dict)
            or recognition.get("type") != "And"
            or reco_param.get("all_of") != ["作战开始识别", "作战开始背景颜色识别"]):
        _refuse("preparation_node_invalid")
    # Recognition only: do not run either node's action or follow its next.
    hud = context.run_recognition("战斗中", image)
    restart = context.run_recognition("战斗重启1", image)
    ready = context.run_recognition(PREPARE_NODE, image)
    if (hud is None or type(hud.hit) is not bool or hud.hit
            or restart is None or type(restart.hit) is not bool or restart.hit
            or ready is None or ready.hit is not True or ready.best_result is None):
        _refuse("preparation_unconfirmed")
    box = _rect(ready.best_result.box)
    node = context.get_node_data(START_NODE)
    action = node.get("action", {}) if isinstance(node, dict) else {}
    param = action.get("param", {}) if isinstance(action, dict) else {}
    if (not isinstance(action, dict) or not isinstance(param, dict)
            or action.get("type") != "Click" or param.get("target") is not True
            or param.get("target_offset", [0, 0, 0, 0]) != [0, 0, 0, 0]
            or param.get("contact", 0) != 0 or node.get("repeat", 1) != 1
            or node.get("pre_delay", 0) != 0 or node.get("post_delay", 0) != 0):
        _refuse("start_action_invalid")
    scope.assert_active()
    if (combat.input_scope is not scope or combat.context is not context
            or scope.generation != generation or combat.inverse_crown_profile != profile
            or combat.frame is not image or scope.frame_metadata is not metadata
            or not 0 <= scope.monotonic() - metadata.captured_at <= _PREPARE_MAX_AGE
            or scope.owned_contact is not None or scope.release_pending or scope.capture_in_flight):
        _refuse("preparation_changed")
    # MaaFramework 5.14.2 Context.run_action returns ActionDetail, does not run
    # next, and uses the supplied recognition box for this target=true Click.
    # No retry: an exception or failed result has already spent this attempt.
    combat._entry_start_attempted = True
    result = context.run_action(START_NODE, box=box)
    if (result is None or getattr(result, "success", None) is not True
            or getattr(result, "name", None) != START_NODE
            or getattr(result, "action", None) != "Click"
            or _rect(getattr(result, "box", None)) != box):
        _refuse("start_unconfirmed")
    action_id = getattr(result, "action_id", None)
    if type(action_id) is not int or action_id <= 0:
        _refuse("start_unconfirmed")
    scope.assert_active()
    if (combat.input_scope is not scope or combat.context is not context
            or scope.generation != generation or combat.inverse_crown_profile != profile):
        _refuse("start_owner_changed")
    return FreshEntryCandidate(combat, context, scope, generation, profile,
                               action_id, metadata.sequence, scope.monotonic())
