# Copyright (c) 2024-2025 MAA_Punish
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
MAA_Punish
MAA_Punish 战斗框架入口
作者:overflow65537
"""

from dataclasses import replace

from maa.context import Context
from maa.custom_action import CustomAction

from action.combat.core.provider import CombatCheck
from action.combat.core.fresh_entry import preparation_entry_requested
from action.combat.core.session import (
    CombatOutcome,
    CombatTask,
    emit_combat_outcome,
)
from action.combat.entry.inverse_crown_layout import require_inverse_crown_layout
from action.combat.config.inverse_crown_profile import (
    InverseCrownProfile,
    InverseCrownProfileError,
    InverseCrownUnavailable,
)
from logger_component import LoggerComponent


class CombatRunner(CustomAction):
    """战斗 CustomAction：一次调用内跑完 combat_once。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        self.last_outcome: CombatOutcome | None = None
        combat: CombatTask | None = None
        try:
            profile = InverseCrownProfile.from_node_data(
                context.get_node_data(argv.node_name)
            )
            from_preparation = preparation_entry_requested(
                argv.node_name, getattr(argv, "custom_action_param", ""),
            )
            if from_preparation and not profile.enabled:
                raise InverseCrownProfileError("preparation entry requires enabled profile")
            if profile.enabled:
                require_inverse_crown_layout(context)
            combat_check = CombatCheck()
            combat = CombatTask(context, combat_check, inverse_crown_profile=profile)
            if from_preparation:
                combat.combat_once(from_preparation=True)
            else:
                combat.combat_once()
        except (InverseCrownProfileError, InverseCrownUnavailable) as exc:
            if combat is None:
                self._record_preflight_outcome(
                    "profile_invalid" if isinstance(exc, InverseCrownProfileError) else exc.reason,
                    "REJECTED", type(exc).__name__,
                )
            return CustomAction.RunResult(success=False)
        except BaseException as exc:
            if combat is None:
                self._record_preflight_outcome(
                    "preflight_exception", "ERROR", type(exc).__name__,
                )
            raise
        finally:
            if combat is not None:
                self.last_outcome = combat.outcome
        # Ordinary mode retains its existing callback result contract. This is
        # callback completion, not a claim that the game battle was won.
        return CustomAction.RunResult(success=True)

    def _record_preflight_outcome(
        self, reason: str, callback_status: str, exception_type: str
    ) -> None:
        self.last_outcome = CombatOutcome(
            exit_reason=reason,
            callback_status=callback_status,
            exception_type=exception_type,
        )
        # Preflight errors happen before CombatTask creates its own component.
        try:
            component = LoggerComponent(__name__)
            emit_combat_outcome(component, self.last_outcome)
        except BaseException as exc:
            # A primary preflight error already owns the bool/raise contract.
            # Preserve it and expose only the failed emission's type in memory.
            self.last_outcome = replace(
                self.last_outcome, emission_exception_type=type(exc).__name__,
            )
