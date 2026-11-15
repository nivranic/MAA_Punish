"""Startup leaves a confirmed matrix partner event without selecting or ending it."""

from threading import Timer

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("start,event,back,collection,team", [
    ("event", True, True, True, True),
    ("event", True, False, True, True),
    ("event", False, True, True, True),
    ("map", False, True, True, True),
    ("map", False, True, False, True),
    ("map", False, True, True, False),
    ("map", False, False, True, True),
])
def test_startup_leaves_only_confirmed_matrix_partner(tmp_path, language, start, event, back, collection, team):
    state = {"page": start}
    clicks = []

    class Capture(Controller):
        def click(self, x, y):
            state["page"] = {"event": "map", "map": "menu", "menu": "home"}[state["page"]]
            clicks.append((x, y))
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            matches = {
                "贴心搭档_矩阵循生": (state["page"] == "event" and event, (280, 80, 70, 60)),
                "肉鸽内部": (state["page"] == "map" and collection, (1030, 650, 70, 30)),
                "更换人物": (state["page"] == "map" and team, (735, 650, 70, 30)),
                "返回_文字": (state["page"] in {"event", "map"} and back, (130, 20, 60, 40)),
                "返回主菜单_文字": (state["page"] == "menu", (285, 10, 75, 55)),
                "检查主界面2": (state["page"] == "home", (50, 50, 50, 50)),
            }
            hit, box = matches.get(argv.node_name, (False, None))
            return box if hit else None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R03StartupPage", Page())
    startup = jsonc.loads((ROOT / "resource/base/pipeline/Start_up.jsonc").read_text(encoding="utf-8"))
    preserved = {"返回主菜单", "启动_从矩阵搭档返回", "启动_从矩阵地图返回"}
    names = {name.removeprefix("[JumpBack]") for name in startup["启动"]["next"]} - preserved
    names.update(["贴心搭档_矩阵循生", "肉鸽内部", "更换人物", "返回_文字", "返回主菜单_文字", "检查主界面2"])
    overrides = {
        name: {"recognition": {"type": "Custom", "param": {"custom_recognition": "R03StartupPage"}},
               "pre_delay": 0, "post_delay": 0, "timeout": 100, "rate_limit": 1, "on_error": []}
        for name in names
    }
    for name in preserved | {"启动"}:
        overrides[name] = {"pre_delay": 0, "post_delay": 0, "timeout": 100, "rate_limit": 1, "on_error": []}
    overrides["检查主界面2"].update(action={"type": "StopTask"}, next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)
    expired = []

    def stop():
        expired.append(True)
        tasker.post_stop()

    watchdog = Timer(15, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        job = tasker.post_task("启动", overrides).wait()
        assert not expired
        succeeds = back and collection and team and (start == "map" or event)
        assert job.succeeded is succeeds
        assert len(clicks) == ((3 if start == "event" else 2) if succeeds else 0)
        assert state["page"] == ("home" if succeeds else start)
        if clicks:
            x, y = clicks[0]
            assert 130 <= x < 190 and 20 <= y < 60
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)


@pytest.mark.parametrize("language", ["base", "en_US", "zh_TW", "ja_JP"])
@pytest.mark.parametrize("entry", ["启动", "返回主菜单"])
@pytest.mark.parametrize("missing", [None, "formation", "budget", "back"])
def test_startup_returns_from_matrix_formation_without_recruiting(tmp_path, language, entry, missing):
    state = {"page": "formation"}
    clicks, wrong, expired = [], [], []
    regions = {
        "formation": (130, 20, 60, 40),
        "menu": (196, 14, 84, 46) if language == "en_US" else (285, 10, 75, 55),
    }

    class Capture(Controller):
        def click(self, x, y):
            box = regions.get(state["page"])
            if box is None or not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
                wrong.append((state["page"], x, y))
                return False
            clicks.append(state["page"])
            state["page"] = {"formation": "menu", "menu": "home"}[state["page"]]
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            gates = {"矩阵循生_编队色位": "formation", "矩阵循生_招募能量_编队": "budget", "返回_文字": "back"}
            if argv.node_name in gates and state["page"] == "formation" and missing != gates[argv.node_name]:
                return regions["formation"]
            if argv.node_name == "返回主菜单_文字" and state["page"] == "menu":
                return regions["menu"]
            if argv.node_name in {"检查主界面", "检查主界面2"} and state["page"] == "home":
                return (50, 50, 50, 50)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition("R03StartupFormation", Page())
    keep = {"启动", "返回主菜单", "返回主菜单_reco", "启动_从矩阵编队返回", "检查主界面", "检查主界面2"}
    patch = {}
    for parent in ("启动", "返回主菜单"):
        for item in resource.get_node_data(parent)["next"]:
            name = (item if isinstance(item, str) else item["name"]).removeprefix("[JumpBack]")
            if name not in keep:
                patch[name] = {"enabled": False}
    timing = dict(pre_delay=0, post_delay=0, rate_limit=1, timeout=100, on_error=[])
    for name in keep:
        if resource.get_node_data(name) is not None:
            patch.setdefault(name, {}).update(timing)
    for name in ("矩阵循生_编队色位", "矩阵循生_招募能量_编队", "返回_文字", "返回主菜单_文字", "检查主界面", "检查主界面2"):
        patch.setdefault(name, {}).update(recognition={"type": "Custom", "param": {"custom_recognition": "R03StartupFormation"}})
    patch[entry].update(recognition="DirectHit", action="DoNothing")
    for name in ("检查主界面", "检查主界面2"):
        patch[name].update(action="StopTask", next=[])
    controller, tasker = Capture(), Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource, controller)

    def stop():
        expired.append(True)
        tasker.post_stop()

    timer = Timer(10, stop)
    timer.daemon = True
    timer.start()
    try:
        job = tasker.post_task(entry, patch).wait()
        assert not expired and not wrong
        assert job.succeeded is (missing is None)
        assert clicks == (["formation", "menu"] if missing is None else [])
        assert state["page"] == ("home" if missing is None else "formation")
    finally:
        timer.cancel()
        timer.join()
        stop_and_wait_idle(tasker)
