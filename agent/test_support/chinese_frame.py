"""Draw Chinese text on synthetic frames for host-only OCR regressions."""

import os
from pathlib import Path

import numpy as np
import pytest


def chinese_frame(language, captions, *, font_size=31):
    image_module = pytest.importorskip("PIL.Image")
    image_draw = pytest.importorskip("PIL.ImageDraw")
    image_font = pytest.importorskip("PIL.ImageFont")
    bundled = os.environ.get("MAA_TEST_CJK_FONT")
    fonts = Path(os.environ.get("WINDIR", "")) / "Fonts"
    candidates = ([Path(bundled)] if bundled else []) + [
        fonts / ("msjh.ttc" if language == "zh_TW" else "msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    face = next((path for path in candidates if path.is_file()), None)
    if face is None:
        pytest.skip("Chinese font needed to draw synthetic OCR frames")
    image = image_module.new("RGB", (1280, 720), (43, 43, 43))
    draw = image_draw.Draw(image)
    font = image_font.truetype(str(face), font_size)
    for position, text in captions:
        draw.text(position, text, font=font, fill="white")
    return np.asarray(image).copy()
