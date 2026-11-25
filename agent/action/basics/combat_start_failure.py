"""Finish an unconfirmed shared start without clicking through an unknown page."""

from maa.context import Context
from maa.custom_action import CustomAction


class CombatStartFailure(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> bool:
        if context.tasker.stopping:
            return False
        # The Android App classifies this exact marker as a task failure. Bypass
        # its normal Back/pause/confirm cleanup only in this terminating task.
        if not context.override_next("任务失败优雅退出", ["停止任务"]):
            return False
        return not context.tasker.stopping
