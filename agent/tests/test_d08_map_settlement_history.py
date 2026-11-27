"""Fail-closed cases for the D08 recent-history IPC boundary."""

from types import SimpleNamespace as Obj

import pytest
from maa.define import NodeDetail

from recognition.exclusives.D08MapSettlement import D08MapSettlement, ENTRY, HISTORY_LIMIT, HUD, MAP_START


def occurrence(node_id, name, algorithm, action_type):
    return NodeDetail(
        node_id, name,
        Obj(reco_id=node_id + 200, name=name, algorithm=algorithm, hit=True),
        Obj(action_id=node_id + 300, name=name, action=action_type, success=True),
        True,
    )


class History:
    def __init__(self):
        self.stopping = False
        self.rows = {
            1: occurrence(1, MAP_START, "OCR", "Click"),
            2: occurrence(2, HUD, "TemplateMatch", "Custom"),
        }
        self.task = Obj(task_id=9, entry=ENTRY, node_id_list=[1, 2], status=Obj(running=True))
        self.reads = []
        self.stop_on = None
        self.effective = {
            "recognition": {"type": "TemplateMatch"},
            "action": {"type": "Custom", "param": {"custom_action": "CombatRunner"}},
        }

    def get_node_detail(self, node_id):
        self.reads.append(node_id)
        if self.stop_on == node_id:
            self.stopping = True
        return self.rows.get(node_id)

    def get_node_data(self, name):
        self.reads.append(name)
        if self.stop_on == name:
            self.stopping = True
        assert name == HUD
        return self.effective

    def recognized(self):
        return D08MapSettlement().analyze(Obj(tasker=self, get_node_data=self.get_node_data), Obj(task_detail=self.task))


def test_d08_queries_only_the_late_evidence_and_effective_hud():
    history = History()
    history.task.node_id_list = [100000, 1, 2]  # Entry-era detail is neither queried nor necessary.
    assert history.recognized() == (0, 0, 1, 1)
    assert history.reads == [2, 1, HUD]


@pytest.mark.parametrize("issue", [
    "foreign_entry", "zero_task", "not_running", "zero_node", "duplicate_node", "missing_detail", "anonymous_tail",
    "expired_start", "failed_hud", "unfinished_hud", "missing_hud_action", "false_hud", "wrong_hud_algo",
    "failed_map", "wrong_map_algo", "bad_action_id", "wrong_name", "failed_later_hud",
    "redo_without_hud", "aftermath_without_hud", "second_map_no_hud",
])
def test_d08_refuses_insufficient_or_prior_round_ownership(issue):
    h = History()
    start, hud = h.rows[1], h.rows[2]
    if issue == "foreign_entry":
        h.task.entry = "幻痛囚笼"
    elif issue == "zero_task":
        h.task.task_id = 0
    elif issue == "not_running":
        h.task.status.running = False
    elif issue == "zero_node":
        h.task.node_id_list.append(0)
    elif issue == "duplicate_node":
        h.task.node_id_list.append(2)
    elif issue == "missing_detail":
        h.task.node_id_list.append(3)
    elif issue == "anonymous_tail":
        h.task.node_id_list.append(3)
        h.rows[3] = NodeDetail(3, "", None, None, False)
    elif issue == "expired_start":
        for i in range(3, 3 + HISTORY_LIMIT - 1):
            h.task.node_id_list.append(i)
            h.rows[i] = occurrence(i, "载入中", "OCR", "DoNothing")
    elif issue == "failed_hud":
        hud.action.success = False
    elif issue == "unfinished_hud":
        hud.completed = False
    elif issue == "missing_hud_action":
        hud.action = None
    elif issue == "false_hud":
        hud.recognition.hit = False
    elif issue == "wrong_hud_algo":
        hud.recognition.algorithm = "DirectHit"
    elif issue == "failed_map":
        start.action.success = False
    elif issue == "wrong_map_algo":
        start.recognition.algorithm = "Custom"
    elif issue == "bad_action_id":
        hud.action.action_id = 0
    elif issue == "wrong_name":
        hud.action.name = "not HUD"
    else:
        h.task.node_id_list.append(3)
        if issue == "failed_later_hud":
            h.rows[3] = occurrence(3, HUD, "TemplateMatch", "Custom")
            h.rows[3].action.success = False
        elif issue == "second_map_no_hud":
            h.rows[3] = occurrence(3, MAP_START, "OCR", "Click")
        else:
            retry_node = "继续挑战" if issue == "redo_without_hud" else "战斗失败_历战"
            h.rows[3] = occurrence(3, retry_node, "OCR", "Click")
    assert h.recognized() is None
    assert HUD not in h.reads
    if issue == "expired_start":
        assert len(h.reads) == HISTORY_LIMIT and 1 not in h.reads


@pytest.mark.parametrize("stop_on,reads", [(None, []), (2, [2]), (1, [2, 1]), (HUD, [2, 1, HUD])])
def test_d08_cancels_on_stop_before_or_during_ipc(stop_on, reads):
    history = History()
    history.stopping = stop_on is None
    history.stop_on = stop_on
    assert history.recognized() is None
    assert history.reads == reads


@pytest.mark.parametrize("config", [None, {}, {"enabled": False}, {"inverse": True},
                                    {"action": {"type": "Custom", "param": {"custom_action": "Other"}}}])
def test_d08_rejects_missing_or_mismatched_effective_owner(config):
    history = History()
    history.effective = {**history.effective, **config} if config else config
    assert history.recognized() is None and history.reads == [2, 1, HUD]


def test_d08_retry_may_admit_a_new_completed_hud():
    history = History()
    history.task.node_id_list += [3, 4]
    history.rows[3] = occurrence(3, "继续挑战", "OCR", "Click")
    history.rows[4] = occurrence(4, HUD, "TemplateMatch", "Custom")
    assert history.recognized() is not None
    assert history.reads == [4, 3, 2, 1, HUD]
