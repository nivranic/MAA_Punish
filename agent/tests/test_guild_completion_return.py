"""指挥局收尾必须能离开大厅顶栏，并在已经回到主界面时安静结束。"""

from pathlib import Path

import jsonc

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "resource/base/pipeline"
GUILD = jsonc.loads((PIPELINE / "Guild.jsonc").read_text(encoding="utf-8"))
SHARED = jsonc.loads((PIPELINE / "Common/Shared_Recognition.jsonc").read_text(encoding="utf-8"))


def _recognition(name):
    return GUILD.get(name, SHARED.get(name))["recognition"]


def test_completion_keeps_real_mark_action():
    node = GUILD["日常完成_指挥局"]
    assert node["recognition"] == {"type": "DirectHit", "param": {}}
    assert node["action"] == {"type": "Custom", "param": {"custom_action": "DailyProgressMark"}}
    assert node["attach"] == {"daily_progress_key": "指挥局"}


def test_completion_tries_home_return_first_then_hub_fallback():
    # 返回主菜单 先试是因为它最便宜，且在其它收尾界面仍然有效；
    # 大厅里它永远不会命中，靠后面的候选兜底。
    assert GUILD["日常完成_指挥局"]["next"] == [
        "返回主菜单",
        "[JumpBack]指挥局_返回",
        "指挥局_已在主界面",
    ]


def test_hub_fallback_is_gated_on_title_and_back_button():
    node = GUILD["指挥局_返回"]
    recognition = node["recognition"]
    assert recognition["type"] == "And"
    assert recognition["param"]["all_of"] == ["检查是否在指挥局内部", "返回_文字"]
    # 点击落点必须是返回按钮（第二个子结果），不是大厅标题。
    assert recognition["param"]["box_index"] == 1
    assert node["action"] == {"type": "Click"}
    # 点击后要等界面切换，否则下一轮候选会在旧画面上重试。
    assert node["post_delay"] >= 1500


def test_back_button_roi_stays_left_of_the_hub_title():
    # 两个子树读不同的横向区域：标题在中部，返回在最左；并且返回识别区
    # 不能覆盖标题文字，否则「大厅内」这个前提会被标题自己满足。
    title = _recognition("检查是否在指挥局内部")["param"]
    back = _recognition("返回_文字")["param"]
    assert title["expected"] == "新版指挥局"
    assert back["expected"] == ["返回", "暂离"]
    title_x, _, title_w, _ = title["roi"]
    back_x, _, back_w, _ = back["roi"]
    assert back_x + back_w <= title_x < title_x + title_w


def test_home_terminator_verifies_main_screen_without_clicking():
    # 回到主界面后「返回主菜单」仍然不命中（主界面没有那四个字），必须有
    # 一个能在主界面命中的终态候选，否则会空转到节点超时。
    assert "指挥局_已在主界面" in GUILD["日常完成_指挥局"]["next"]
    terminator = GUILD["指挥局_已在主界面"]
    assert terminator["recognition"]["param"]["all_of"] == ["主界面_战斗节点"]
    # 框架对不写 action 的节点默认就是 DoNothing（13:35:50 的 run_action 实测），
    # 这里仍显式写出来，免得以后被当成「会点一下」而改坏。
    assert terminator["action"] == {"type": "DoNothing"}
