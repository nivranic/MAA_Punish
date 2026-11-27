"""Reject a matrix statistics page whose outcome is not established by this task."""

from maa.context import Context
from maa.custom_action import CustomAction


class R03UnconfirmedSettlement(CustomAction):
    def run(self, _context: Context, _argv: CustomAction.RunArg) -> bool:
        # A named FAILED node, not an anonymous next-list timeout or a StopTask success.
        # Do not touch the page: the user may have resumed midway through settlement.
        return False
