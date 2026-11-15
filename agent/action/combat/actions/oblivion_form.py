"""Positive orb-form evidence for Oblivion, after the caller verifies the HUD."""
from __future__ import annotations

from typing import Literal

import numpy as np

OblivionForm = Literal["normal", "main", "unknown"]


def _hsv_channels(bgr):
    """8-bit HSV channels using NumPy, available in the Android agent.

    Preserve the existing H=0..179/S,V=0..255 thresholds and integer rounding.
    OpenCV is a desktop test dependency, not an Android runtime dependency.
    """
    blue, green, red = np.moveaxis(bgr.astype(np.int32), -1, 0)
    value = np.maximum(np.maximum(blue, green), red)
    delta = value - np.minimum(np.minimum(blue, green), red)
    saturation_scale = np.rint((255 << 12) / np.maximum(value, 1)).astype(np.int32)
    hue_scale = np.rint((180 << 12) / (6 * np.maximum(delta, 1))).astype(np.int32)
    saturation = (delta * saturation_scale + (1 << 11)) >> 12
    hue = np.where(
        value == red, green - blue,
        np.where(value == green, blue - red + 2 * delta, red - green + 4 * delta),
    )
    hue = (hue * hue_scale + (1 << 11)) >> 12
    hue = np.where(hue < 0, hue + 180, hue)
    return hue, saturation, value


def classify_oblivion_form(
    image, centers, ball_count: int, *, allow_sparse_normal: bool = False,
    allow_sparse_main: bool = False,
) -> OblivionForm:
    """Require three agreeing slots, except explicitly gated sparse forms.

    初星只产生黄球，主序产生红/蓝球。仅供已确认终焉身份和同帧战斗
    HUD 的调用方使用；菜单背景的颜色本身不构成形态证据。
    大招循环后可能只剩1—2个黄球；调用方在开大前确认同帧能量已满，
    或二段已请求且同帧能量已空时，允许全部占用槽为黄色来确认常态。
    二段已请求但同帧能量仍就绪时，允许全部1—2占用槽为红/蓝来确认主序。
    """
    if (
        not isinstance(image, np.ndarray) or image.dtype != np.uint8
        or image.ndim != 3 or image.shape[2] != 3
        or centers is None or len(centers) != 8
        or type(ball_count) is not int or not 1 <= ball_count <= 16
        or (ball_count < 3 and not (allow_sparse_normal or allow_sparse_main))
    ):
        return "unknown"
    height, width = image.shape[:2]
    colors = []
    # Slots are ordered from the first (rightmost) orb. The count includes
    # queued orbs beyond the eight visible slots; empty slots show the arena.
    for x, y in centers[:min(ball_count, len(centers))]:
        if x - 22 < 0 or y - 22 < 0 or x + 22 > width or y + 22 > height:
            return "unknown"
        hue, saturation, value = _hsv_channels(image[y-22:y+22, x-22:x+22])
        visible = (saturation >= 80) & (value >= 100)
        color_masks = [
            visible & (hue >= 18) & (hue <= 40),
            visible & ((hue <= 12) | (hue >= 165)),
            visible & (hue >= 90) & (hue <= 125),
        ]
        counts = [int(np.count_nonzero(mask)) for mask in color_masks]
        order = np.argsort(counts)
        if counts[order[-1]] >= 35 and counts[order[-1]] >= 2 * counts[order[-2]]:
            # Dark arena warnings can occupy more pixels than the bright orb.
            # Require the same color to dominate brightness too; disagreement
            # leaves this slot unknown, without promoting a different color.
            brightness = [int(value[mask].sum()) for mask in color_masks]
            winner = int(order[-1])
            runner_up = max(brightness[index] for index in range(3) if index != winner)
            if brightness[winner] >= 2 * runner_up:
                colors.append("YRB"[winner])
    yellow = colors.count("Y")
    main = colors.count("R") + colors.count("B")
    sparse_normal = allow_sparse_normal and ball_count <= 2 and yellow == ball_count
    sparse_main = allow_sparse_main and ball_count <= 2 and main == ball_count
    if (yellow >= 3 or sparse_normal) and main == 0:
        return "normal"
    if (main >= 3 or sparse_main) and yellow == 0:
        return "main"
    return "unknown"
