# Copyright (c) 2024-2025 MAA_Punish
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""QTE 技能 ready 识别与点击（独立于 core 包，避免循环 import）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maa.context import Context

# QTE.onnx：1/3/5 为 *_qte_ready，对应 Pipeline「释放*QTE」节点
COLOR_TO_RELEASE_NODE: dict[str, str] = {
    "R": "释放红色QTE",
    "Y": "释放黄色QTE",
    "B": "释放蓝色QTE",
}

_AUTO_RELEASE_SCAN_ORDER = ("R", "B", "Y")


def _normalize_color(color: str) -> str:
    return color.upper()


def _box_center(box: Any) -> tuple[int, int]:
    return int(box[0] + box[2] / 2), int(box[1] + box[3] / 2)


def _click_box(context: Context, box: Any) -> bool:
    if context.tasker.stopping:
        return False
    x, y = _box_center(box)
    return bool(context.tasker.controller.post_click(x, y).wait().succeeded)


def recognize_release_qte(context: Context, color: str, image: Any) -> Any | None:
    """在同一帧上识别指定色位 QTE 技能 ready。"""
    if context.tasker.stopping:
        return None
    node = COLOR_TO_RELEASE_NODE.get(_normalize_color(color))
    if not node:
        return None
    result = context.run_recognition(node, image)
    if not context.tasker.stopping and result and result.hit and result.best_result:
        return result
    return None


def click_release_qte_if_ready(context: Context, color: str, image: Any) -> bool:
    """识别 ready 则点击，不再走 run_action 二次识别。"""
    result = recognize_release_qte(context, color, image)
    if not result:
        return False
    return _click_box(context, result.best_result.box)  # type: ignore[attr-defined]


def click_any_release_qte(
    context: Context,
    image: Any,
    colors: tuple[str, ...] = _AUTO_RELEASE_SCAN_ORDER,
    *,
    tracker: Any | None = None,
    frame_token: Any = None,
) -> str | None:
    """单次截屏下按顺序检测 QTE ready，命中即点击。返回命中的色位或 None。

    传入 tracker 时按方案 3.4 记账：每个被观测色位先结算待确认请求
    （ready 消失=已确认、超窗仍在=未确认），命中后只点「非同帧、不在
    冷却内」的辅助；点击失败即返回 None，不改试其他色位的既有契约。
    """
    for color in colors:
        if context.tasker.stopping:
            break
        key = "qte-" + _normalize_color(color)
        result = recognize_release_qte(context, color, image)
        if tracker is not None:
            tracker.observe_slot(key, bool(result))
        if not result:
            continue
        if tracker is not None and not tracker.can_request(key, frame_token):
            continue
        if _click_box(context, result.best_result.box):
            if tracker is not None:
                tracker.record_request(key, frame_token)
            return _normalize_color(color)
        return None
    return None
