"""Read-only, task-local evidence for D08's map-to-settlement edge."""

from maa.context import Context
from maa.custom_recognition import CustomRecognition
from maa.define import NodeDetail


ENTRY = "历战映射"
MAP_START = "开始挑战"
HUD = "战斗中"
HISTORY_LIMIT = 64
# A later defeat, retry, cleanup, or settlement invalidates an older HUD.
_ROUND_BOUNDARIES = {"战斗失败_历战", "继续挑战", "历战映射_异常收尾", "结算"}


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


def _completed(node: NodeDetail, algorithm: str, action_type: str) -> bool:
    reco, act = node.recognition, node.action
    return (
        node.completed is True
        and reco is not None and _positive_id(reco.reco_id)
        and reco.name == node.name and reco.algorithm == algorithm and reco.hit is True
        and act is not None and _positive_id(act.action_id)
        and act.name == node.name and act.action == action_type and act.success is True
    )


def _configured_battle_owner(context: Context) -> bool:
    # Maa 5.14.2 records ActionDetail(Custom) without the callback name.
    # Match the actual history types above and check the task-effective name.
    node = context.get_node_data(HUD)
    if context.tasker.stopping or not isinstance(node, dict):
        return False
    reco, action = node.get("recognition"), node.get("action")
    param = action.get("param") if isinstance(action, dict) else None
    return (
        node.get("enabled", True) is True and node.get("inverse", False) is False
        and isinstance(reco, dict) and reco.get("type") == "TemplateMatch"
        and isinstance(param, dict) and action.get("type") == "Custom"
        and param.get("custom_action") == "CombatRunner"
    )


class D08MapSettlement(CustomRecognition):
    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        try:
            tasker = context.tasker
            detail = argv.task_detail  # The SDK obtains this for the callback's native task ID.
            if (tasker.stopping or detail is None or detail.entry != ENTRY
                    or not _positive_id(detail.task_id) or detail.status.running is not True):
                return None
            # Do not use detail.nodes: it eagerly fetches the entire task over IPC.
            ids = detail.node_id_list[-HISTORY_LIMIT:]
            if not all(_positive_id(i) for i in ids) or len(set(ids)) != len(ids):
                return None
            current_hud = False
            for node_id in reversed(ids):
                if tasker.stopping:
                    return None
                node = tasker.get_node_detail(node_id)
                if tasker.stopping or node is None or node.node_id != node_id or not node.name:
                    return None
                if node.name == MAP_START:
                    # Stop on the *latest* Start, even if it was incomplete or failed.
                    if current_hud and _completed(node, "OCR", "Click") and _configured_battle_owner(context):
                        if not tasker.stopping:
                            # Only a predicate: the pipeline obtains the real box again.
                            return (0, 0, 1, 1)
                    return None
                if not current_hud:
                    if node.name in _ROUND_BOUNDARIES:
                        return None
                    if node.name == HUD:
                        if not _completed(node, "TemplateMatch", "Custom"):
                            return None
                        current_hud = True
        except Exception:
            # A missing/malformed native detail or IPC failure is not ownership.
            return None
        return None
