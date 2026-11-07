"""A detected boss card must enter combat instead of terminating the adventure."""
from pathlib import Path
from threading import Timer

import numpy as np
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker

from test_support.native_lifecycle import stop_and_wait_idle


ROOT = Path(__file__).resolve().parents[2]


def test_detected_boss_clicks_card_and_uses_existing_combat_entry(tmp_path):
    inputs = []

    class Capture(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-r03-boss-route"
        def get_features(self): return 0
        def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
        def click(self, x, y, contact=0, pressure=1):
            inputs.append((x, y))
            return True

    assert Tasker.set_log_dir(tmp_path / "native-log")
    resource, controller, tasker = Resource(), Capture(), Tasker()
    assert resource.set_cpu()
    assert resource.post_bundle(ROOT / "resource/base").wait().succeeded
    assert controller.post_connection().wait().succeeded
    assert tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(15, stop)
    watchdog.start()
    try:
        # Begin after boss recognition, using its actual observed box. The
        # existing entry is ready; legacy exit candidates are also offered as
        # traps. This checks native routing, not boss-image or game acceptance.
        overrides = {
            "BOSS战_矩阵循生": {
                "recognition": {"type": "DirectHit", "param": {"roi": [596, 186, 34, 40]}},
            },
            "进入战斗_矩阵循生": {
                "recognition": {"type": "DirectHit"},
                "action": {"type": "StopTask"},
            },
            **{name: {"recognition": {"type": "DirectHit"},
                      "action": {"type": "StopTask"}}
               for name in ("确认结束购物_矩阵循生", "终止_矩阵循生", "返回")},
            **{name: {"enabled": False} for name in (
                "战斗完成_矩阵循生", "战斗失败", "放弃战斗_矩阵循生",
                "算力单元不足", "重启_寒境曙光", "跳过战斗对话", "战斗中",
                "出击_矩阵循生", "载入中", "通用处理-未下载弹窗-角色皮肤没下载-检测层",
            )},
        }
        job = tasker.post_task("BOSS战_矩阵循生", overrides).wait()
        assert job.succeeded and not expired
        detail = tasker.get_task_detail(job.job_id)
        names = [tasker.get_node_detail(n).name for n in detail.node_id_list]
        assert names == ["BOSS战_矩阵循生", "进入战斗_矩阵循生"]
        assert len(inputs) == 1
        x, y = inputs[0]
        assert 596 <= x < 630 and 186 <= y < 226
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
