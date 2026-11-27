"""Guard daily purchase actions, including direct Pipeline entry."""

import re

from maa.custom_action import CustomAction
from maa.pipeline import JActionType, JClick

from action.basics.daily_progress import LedgerError, require_current_purchase_day
from audit_commit import CommitDenied, PurchaseCommit, canonical
from logger_component import LoggerComponent

LIMIT_LABEL = "还可购买"
LIMIT_COUNT = re.compile(r"还可购买([0-9]{1,3})\Z")


def purchase_limit(argv):
    """Return the page's own ``还可购买 N`` count, or None when it is not readable.

    The purchase page prints that count beside the submit button, so the OCR
    entry that already matched the button reads it too; this asks for no extra
    screenshot and never guesses. Only the literal label with digits counts:
    an unreadable, reworded or absent line stays None so the caller keeps the
    ordinary click instead of inventing a count.
    """
    detail = getattr(getattr(argv, "reco_detail", None), "best_result", None)
    for child in getattr(detail, "sub_results", None) or ():
        tokens = [getattr(item, "text", None)
                  for item in getattr(child, "all_results", None) or ()]
        for index, token in enumerate(tokens):
            if type(token) is not str:
                continue
            text = re.sub(r"\s+", "", token)
            match = LIMIT_COUNT.fullmatch(text)
            if match is not None:
                return int(match[1])
            # One line may arrive as a label box plus a separate number box.
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if (text == LIMIT_LABEL and type(following) is str
                    and re.fullmatch(r"[0-9]{1,3}", following.strip()) is not None):
                return int(following.strip())
    return None


class AuditPurchase(CustomAction):
    def __init__(self):
        super().__init__()
        self._logger_component = LoggerComponent(__name__)

    def run(self, context, argv):
        try:
            # The live node is authoritative; a resident Agent never caches run
            # identity or accepts it from process-start environment variables.
            node = context.get_node_data(argv.node_name)
            if argv.node_name not in {"购买", "购买_宿舍", "点击购买_宿舍"} or type(node) is not dict:
                raise CommitDenied("unexpected_purchase_node")
            action = node.get("action", {})
            if (action.get("type") != "Custom" or
                    action.get("param", {}).get("custom_action") != "AuditPurchase"):
                raise CommitDenied("purchase_wrapper_missing")

            guard = PurchaseCommit(self._logger_component)

            def click():
                # MaaFramework resolves Custom.target/target_offset to argv.box
                # before invoking us. Reusing that rectangle avoids applying its
                # offset twice; native Click still chooses its own random point.
                # This action exists only in the cloned Context, never as a
                # Pipeline node that could bypass the guard via direct entry.
                cloned = context.clone()
                click_param = JClick(target=tuple(argv.box))
                reco_detail = canonical(argv.reco_detail.raw_detail).decode("utf-8")
                if not guard.config.enabled:
                    try:
                        require_current_purchase_day(context, argv)
                    except LedgerError as error:
                        # Refuse this node without stopping the entire tasker.
                        try:
                            self._logger_component.logger.error("Ordinary purchase click blocked: %s", error)
                        except Exception:
                            pass
                        return False
                    # A page that already reads 还可购买 0 cannot accept a
                    # purchase: the click changes nothing, so no input is sent
                    # and the day is still settled, because no retry can succeed
                    # while the count stays zero. An unreadable count keeps the
                    # ordinary click; this never turns a guess into a skip.
                    if purchase_limit(argv) == 0:
                        try:
                            self._logger_component.logger.warning(
                                "Ordinary purchase click skipped: %s reads 还可购买 0", argv.node_name)
                        except Exception:
                            pass
                        return True
                result = cloned.run_action_direct(JActionType.Click, click_param, box=argv.box, reco_detail=reco_detail)
                return result is not None and result.success is True

            selection = node.get("attach", {}).get("audit_quantity_role")
            if selection is not None:
                if (selection != {"case_id": "D14", "role": "skip_max_in_audit"} or
                        argv.node_name != "购买_宿舍" or action["param"].get("target") != [643, 671, 1, 1]):
                    raise CommitDenied("invalid_quantity_selection_entry")
                if guard.config.enabled:
                    return CustomAction.RunResult(success=guard.skip_audit_maximum_selection(context, argv, node))
            result = guard.execute(context, argv, node, click)
            return CustomAction.RunResult(success=result)
        except Exception as error:
            # Do not wait on post_stop from inside the current task's callback.
            # Failure to log cannot turn a rejected purchase into an input.
            context.tasker.post_stop()
            try:
                self._logger_component.logger.error("Purchase blocked: %s", error)
            except Exception:
                pass
            return CustomAction.RunResult(success=False)
