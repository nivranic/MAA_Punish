"""Shared warning reaction; role strategies own interruption boundaries."""
from maa.custom_action import CustomAction

from logger_component import LoggerComponent


def _protected(combat) -> bool:
    return combat is not None and combat.is_observation_protected()


def _succeeded(result) -> bool:
    if isinstance(result, bool):
        return result
    return result is not None and result.success is True


def execute_dodge(context, *, combat=None, logger=None) -> bool:
    """React immediately to an already observed warning, unless interrupted."""
    if context.tasker.stopping or _protected(combat):
        return False
    result = context.run_action("闪避")
    if combat is not None:
        if combat.input_scope is not None:
            combat.input_scope.invalidate_frame()
        combat.frame = None
    success = _succeeded(result)
    if logger is not None:
        if success:
            logger.info("自动闪避")
        else:
            logger.warning("自动闪避动作未成功")
    return success


def auto_dodge(context, image, *, combat=None, logger=None) -> bool:
    """Use the existing warning model and recheck protection after detection."""
    if context.tasker.stopping or _protected(combat):
        return False
    warning = context.run_recognition("自动闪避", image)
    return bool(warning and warning.hit) and execute_dodge(
        context, combat=combat, logger=logger,
    )


class DodgeAfterWarning(CustomAction):
    """Execute the standalone Pipeline warning edge through shared actions."""

    def __init__(self):
        super().__init__()
        self._logger_component = LoggerComponent(__name__)

    def run(self, context, argv):
        if not argv.reco_detail or not argv.reco_detail.hit:
            return False
        # The parent And already ran the warning model on the current frame.
        # Repeating it here would postpone the reaction by another inference.
        if not execute_dodge(context, logger=self._logger_component.logger):
            return False
        if context.tasker.stopping:
            return False
        return _succeeded(context.run_action("攻击"))
