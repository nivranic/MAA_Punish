"""历战映射达标收尾链必须含分数详情页恢复环（缺陷③：2026-11-23 09:44 达标后 24s 三出口全 miss 致任务失败）。

识别历战分数 的 Click 会开出分数详情页（「意外点击_历战分数」恢复节点
所指的 返回+一键领取 页）；收尾链缺该恢复环时，领取/关闭/返回三出口
在详情页上全部 miss，任务在达标后反而失败。
"""
import jsonc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODES = jsonc.loads(
    (ROOT / "resource/base/pipeline/HighRankMapping.jsonc").read_text(encoding="utf-8")
)


def test_score_exit_chain_recovers_score_details_page():
    exit_next = NODES["日常完成_历战映射"]["next"]
    assert "[JumpBack]领取历战奖励" in exit_next
    assert "[JumpBack]意外点击_历战分数" in exit_next
    # 恢复环排在最终出口之前：详情页被关掉后仍能回到领取/返回判定。
    assert exit_next.index("[JumpBack]意外点击_历战分数") < exit_next.index("返回主菜单")


def test_score_probe_marks_and_routes_to_completion():
    probe = NODES["识别历战分数"]
    assert probe["next"] == ["日常完成_历战映射"]
    assert NODES["日常完成_历战映射"]["action"]["param"]["custom_action"] == "DailyProgressMark"
