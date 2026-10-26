"""信号球八槽：按当前 Context 的生效偏移映射识别框，不修改点击或 ROI。"""

from __future__ import annotations

from typing import Any

SIGNAL_BALL_SLOT_COUNT = 8

# 与 Pipeline 消球1~8 点击位置一致；此处仅用于 TemplateMatch box 映射到槽位编号。
SIGNAL_BALL_SLOT_CENTERS: tuple[tuple[int, int], ...] = (
    (1220, 500),  # 1
    (1111, 500),  # 2
    (1003, 500),  # 3
    (894, 500),  # 4
    (786, 500),  # 5
    (677, 500),  # 6
    (569, 500),  # 7
    (460, 500),  # 8
)


def signal_ball_slot_centers(context: Any) -> tuple[tuple[int, int], ...] | None:
    """读取本 Context 的识别偏移；缺失/坏节点拒绝映射，不复用其它运行的校准。

    有效节点未写 roi_offset 时沿用框架零偏移默认值。这里只平移 dx/dy；
    offset 的宽高变化仍由识别 ROI 消费，不代表八槽间距已完成缩放校准。
    """
    node = context.get_node_data("识别信号球")
    if not isinstance(node, dict):
        return None
    recognition = node.get("recognition")
    if not isinstance(recognition, dict):
        return None
    param = recognition.get("param")
    if not isinstance(param, dict):
        return None
    offset = param.get("roi_offset", [0, 0, 0, 0])
    if (
        not isinstance(offset, list)
        or len(offset) != 4
        or any(type(value) is not int for value in offset)
    ):
        return None
    dx, dy = offset[:2]
    return tuple((x + dx, y + dy) for x, y in SIGNAL_BALL_SLOT_CENTERS)


def slot_index_from_box(
    box: Any,
    slot_centers: tuple[tuple[int, int], ...] = SIGNAL_BALL_SLOT_CENTERS,
) -> int:
    """识别 box 落在哪一号槽（0~7）；无匹配或解析失败返回 -1。"""
    try:
        x, y, w, h = box
        for idx, (pos_x, pos_y) in enumerate(slot_centers):
            if x <= pos_x <= x + w and y <= pos_y <= y + h:
                return idx
        return -1
    except (TypeError, ValueError):
        return -1
