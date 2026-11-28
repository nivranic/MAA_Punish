"""诺曼复兴战: a zone with a sweep left is swept before the 「渊山」 name anchor ends the task."""

import jsonc

from test_r01_zero_refresh_native import ROOT

LIST = "日常断点_诺曼复兴战"
ANCHOR = "打通诺曼复兴战"


def graph():
    path = ROOT / "resource/base/pipeline/Renaissance_War.jsonc"
    return jsonc.loads(path.read_text(encoding="utf-8"))


def test_progress_based_handlers_come_before_the_zone_name_anchor():
    candidates = graph()[LIST]["next"]
    assert ANCHOR in candidates
    # The anchor takes the zone on the right of the map by name; on 2026-11-28 that was the locked
    # 「渊山矿区」 next to 「云岛矿区 进度：0/3」. Ahead of the sweep chain it ended the task before any
    # zone was looked at (nothing was swept on 11-27 and 11-28; no sweep was left that day, but a zone
    # with one on the same screen would have been skipped too).
    for handler in ("[JumpBack]选择挑战目标", "打通诺曼复兴战条件2", "未解锁自动扫荡_无提示"):
        assert candidates.index(handler) < candidates.index(ANCHOR), handler


def test_the_anchor_still_ends_the_task_when_nothing_is_left_to_sweep():
    nodes = graph()
    assert nodes[ANCHOR]["next"] == ["返回主菜单"]
    assert nodes["打通诺曼复兴战条件2"]["next"] == ["日常完成_诺曼复兴战"]
