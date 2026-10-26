"""Guard daily purchase actions, including direct Pipeline entry."""

from maa.custom_action import CustomAction
from maa.pipeline import JActionType, JClick

from audit_commit import CommitDenied, PurchaseCommit, canonical
from logger_component import LoggerComponent


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

            def click():
                # MaaFramework resolves Custom.target/target_offset to argv.box
                # before invoking us. Reusing that rectangle avoids applying its
                # offset twice; native Click still chooses its own random point.
                # This action exists only in the cloned Context, never as a
                # Pipeline node that could bypass the guard via direct entry.
                cloned = context.clone()
                result = cloned.run_action_direct(
                    JActionType.Click, JClick(target=tuple(argv.box)), box=argv.box,
                    reco_detail=canonical(argv.reco_detail.raw_detail).decode("utf-8"),
                )
                return result is not None and result.success is True

            result = PurchaseCommit(self._logger_component).execute(context, argv, node, click)
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
