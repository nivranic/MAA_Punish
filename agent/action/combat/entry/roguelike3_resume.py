"""Restore a missing R03 solo roster from positive, current battle evidence."""
from maa.custom_action import CustomAction

from action.combat.capture import capture_frame
from action.combat.core.provider import (
    CombatCheck, Roguelike3IdentityError, roguelike3_identity_required,
)
from action.combat.core.role_detect import detect_current_role
from action.combat.core.role_factory import ROLE_CLASS_MAP
from action.combat.core.switch import detect_visible_team_colors
from action.combat.core.team import load_team_roster_from_context, publish_team_roster
from action.combat.entry.runner import CombatRunner
from logger_component import LoggerComponent


class R03CombatRunner(CombatRunner):
    def run(self, context, argv) -> CustomAction.RunResult:
        # A confirmed selection remains authoritative. Never rewrite a stale or
        # mismatched published roster to make the existing guard accept it.
        self.last_outcome = None
        try:
            policy = roguelike3_identity_required(context.get_node_data(argv.node_name))
        except Roguelike3IdentityError:
            return super().run(context, argv)
        except BaseException as exc:
            self._record_preflight_outcome("r3_resume_observation_error", "ERROR", type(exc).__name__)
            return CustomAction.RunResult(success=False)
        try:
            if (
                policy and load_team_roster_from_context(context) is None
                and not context.tasker.stopping
            ):
                self._restore_solo_roster(context)
        except BaseException as exc:
            self._record_preflight_outcome("r3_resume_observation_error", "ERROR", type(exc).__name__)
            return CustomAction.RunResult(success=False)
        return super().run(context, argv)

    @staticmethod
    def _restore_solo_roster(context):
        frame = capture_frame(context)
        hp = CombatCheck().read_hp_from_frame(context, frame)
        if not hp.alive:
            return
        # No bench identities may be inferred from QTE colors or cached avatars.
        if any(color != "R" for color in detect_visible_team_colors(context, frame)):
            return
        display, cls_name = detect_current_role(context, frame, on_tick=None)
        if (
            not display or display.strip().casefold() in ("未知", "unknown")
            or cls_name not in ROLE_CLASS_MAP or context.tasker.stopping
        ):
            return
        roster = {"R": cls_name, "B": "", "Y": ""}
        publish_team_roster(context, roster)
        component = LoggerComponent(__name__)
        component.logger.info(
            "R03 空名单同帧恢复: role=%s roster=%s hp=%r", display, roster, hp.text,
        )
