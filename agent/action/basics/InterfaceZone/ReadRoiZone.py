"""
MAA_Punish
在布局界面读取各识别区并写入偏移缓存
作者:overflow65537
"""

from maa.context import Context
from maa.define import Rect
import copy
import json
import os
from pathlib import Path
import tempfile

from action.combat.capture import CombatCaptureError, capture_frame
from action.basics.InterfaceZone.hud_roi_calibration import (
    HUD_CALIBRATION_KEY,
    HudRoiCalibrationError,
    hud_overrides_from_cache,
    make_hud_calibration,
)

from action.basics.InterfaceZone.roi_zone_controller import (
    offset_path,
    zones_for,
)


def capture_roi_zones(context: Context, controller: str, *,
                      hud_calibration: dict | None = None) -> bool:
    zones = zones_for(controller)
    output_path = offset_path(controller)

    if context.tasker.stopping:
        return False
    try:
        image = capture_frame(context)
    except CombatCaptureError:
        _send_msg(context, "读取ROI偏移配置失败，当前截图无效，未更新缓存")
        return False
    screen_size = (int(image.shape[1]), int(image.shape[0]))
    result: dict = {}
    failed = False

    for key, entry, default in zones:
        if context.tasker.stopping:
            return False
        reco = context.run_recognition(entry, image)
        recognized = (
            _normalize_box(reco.box, screen_size) if reco and reco.hit else None
        )
        if recognized is not None:
            offset = [recognized[i] - default[i] for i in range(4)]
            result[key] = {
                "default": default,
                "recognized": recognized,
                "offset": offset,
                "hit": True,
            }
            print(f"{key} recognized={recognized} offset={offset}")
        else:
            result[key] = {
                "default": default,
                "recognized": None,
                "offset": None,
                "hit": False,
            }
            _send_msg(context, f"{key} 读取失败")
            failed = True

    if context.tasker.stopping:
        return False
    if failed:
        _send_msg(context, "读取ROI偏移配置失败，未更新缓存，请检查布局界面及引导遮挡")
        return False
    try:
        if hud_calibration is not None:
            result[HUD_CALIBRATION_KEY] = make_hud_calibration(
                hud_calibration, controller, screen_size, result,
            )
        else:
            # A fresh eight-zone capture must not erase a still-applicable HUD
            # block. Any screen/controller/layout change invalidates that block.
            try:
                old = json.loads(output_path.read_text(encoding="utf-8"))
                candidate = dict(result)
                candidate[HUD_CALIBRATION_KEY] = old.get(HUD_CALIBRATION_KEY)
                hud_overrides_from_cache(candidate, controller, screen_size)
            except (OSError, UnicodeError, json.JSONDecodeError,
                    AttributeError, HudRoiCalibrationError):
                pass
            else:
                result[HUD_CALIBRATION_KEY] = copy.deepcopy(old[HUD_CALIBRATION_KEY])
    except HudRoiCalibrationError as error:
        _send_msg(context, f"HUD校准无效，未更新缓存: {error}")
        return False

    if not _save_cache(context, output_path, result):
        return False

    print(f"capture_roi_zones controller={controller} saved to {output_path}")
    return True


def _normalize_box(value, screen_size: tuple[int, int]) -> list[int] | None:
    # RecognitionDetail.box is a Rect in MaaFramework; only known box types
    # may be converted, so arbitrary iterables never reach list(value).
    if not isinstance(value, (list, tuple, Rect)):
        return None
    box = list(value)
    if len(box) != 4 or any(type(item) is not int for item in box):
        return None
    x, y, width, height = box
    if (x >= 0 and y >= 0 and width > 0 and height > 0
            and x + width <= screen_size[0] and y + height <= screen_size[1]):
        return box
    return None


def _save_cache(context: Context, output_path: Path, data: dict) -> bool:
    temporary = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(data, output, indent=4, ensure_ascii=False, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if context.tasker.stopping:
            return False
        os.replace(temporary, output_path)
        temporary = None
        return True
    except OSError as error:
        _send_msg(context, f"更新ROI偏移缓存失败，原缓存保持: {type(error).__name__}")
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _send_msg(context: Context, msg: str):
    msg_node = {
        "发送消息_这是程序自动生成的node所以故意写的很长来防止某一天想不开用了这个名字导致报错": {
            "focus": {"Node.Recognition.Succeeded": msg}
        }
    }
    context.run_task(
        "发送消息_这是程序自动生成的node所以故意写的很长来防止某一天想不开用了这个名字导致报错",
        pipeline_override=msg_node,
    )
