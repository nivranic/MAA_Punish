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

"""
MAA_Punish
MAA_Punish 截图
作者:overflow65537
"""

from maa.context import Context
from maa.custom_action import CustomAction
import os
from pathlib import Path
import re
import time
import struct
import zlib
import json
from uuid import uuid4

from action.combat.capture import capture_frame
from audit_config import AUDIT_CONFIG


def _clear_old_screenshots(debug_dir: Path) -> None:
    """Keep the existing ordinary-mode retention policy out of audit sessions."""
    three_days_ago = time.time() - 3 * 24 * 3600
    if debug_dir.exists():
        for entry in os.scandir(debug_dir):
            if (
                entry.is_file()
                and entry.name.lower().endswith(".png")
                and entry.stat(follow_symlinks=False).st_mtime < three_days_ago
            ):
                try:
                    os.remove(entry.path)
                except OSError:
                    pass


class ScreenShot(CustomAction):
    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        custom_action_param: dict = json.loads(argv.custom_action_param)
        # Job.get() reads MaaFramework's shared cache, so it is safe only after
        # checking this job. Acquisition exceptions retain their original cause.
        image = capture_frame(context)

        debug_dir = Path("debug")
        if AUDIT_CONFIG.enabled:
            # The host can register this exact directory before invoking a
            # ScreenShot node. This action does not establish host persistence.
            debug_dir = debug_dir / "audit" / AUDIT_CONFIG.session_id / "screenshots"
        else:
            _clear_old_screenshots(debug_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)

        height, width, _ = image.shape
        image_type = re.sub(r"[^\w-]", "_", str(custom_action_param.get("type", "")))[:80]
        filename = f"{image_type}_{time.strftime('%Y-%m-%d_%H-%M-%S')}_{time.time_ns()}_{uuid4().hex}.png"
        debug_path = debug_dir / filename

        def png_chunk(chunk_type, data):
            chunk = chunk_type + data
            return (
                struct.pack("!I", len(data))
                + chunk
                + struct.pack("!I", zlib.crc32(chunk))
            )

        # Convert BGR to RGB
        image = image[:, :, [2, 1, 0]]

        raw_data = b"".join(b"\x00" + image[i, :, :].tobytes() for i in range(height))
        ihdr = struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0)
        idat = zlib.compress(raw_data, level=9)

        created = False
        try:
            # Exclusive creation also prevents a filename collision from
            # replacing existing evidence, even across same-session restarts.
            with debug_path.open("xb") as f:
                created = True
                f.write(b"\x89PNG\r\n\x1a\n")
                f.write(png_chunk(b"IHDR", ihdr))
                f.write(png_chunk(b"IDAT", idat))
                f.write(png_chunk(b"IEND", b""))
        except Exception:
            if created:
                try:
                    debug_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

        return CustomAction.RunResult(success=True)
