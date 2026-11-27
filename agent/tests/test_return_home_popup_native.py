"""A home-return subchain must consume refreshed login popups itself."""
from threading import Timer

import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from action.basics.ClickAnchor import ClickAnchor
from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize('language', ['base', 'en_US', 'zh_TW', 'ja_JP'])
@pytest.mark.parametrize('entry', ['启动', '返回主菜单'])
@pytest.mark.parametrize('popup_count,recognized', [(1, True), (2, True), (1, False)])
def test_return_home_handles_popups_after_reward_without_exiting_parent(
    tmp_path, language, entry, popup_count, recognized,
):
    state = {'page': 'submenu', 'remaining': popup_count}
    clicks = []
    home_region = (196, 14, 84, 46) if language == 'en_US' else (260, 10, 70, 50)

    class Capture(Controller):
        def click(self, x, y):
            clicks.append((state['page'], x, y))
            if state['page'] == 'submenu':
                state['page'] = 'reward'
            elif state['page'] == 'reward':
                state['page'] = 'popup'
            elif state['page'] == 'popup':
                state['remaining'] -= 1
                if state['remaining'] == 0:
                    state['page'] = 'home'
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            matches = {
                '返回主菜单_文字': state['page'] == 'submenu',
                '关闭奖励_文字': state['page'] == 'reward',
                '关闭奖励_颜色': state['page'] == 'reward',
                '关闭公告': state['page'] == 'popup' and recognized,
                '主界面_战斗节点': state['page'] == 'home',
            }
            if not matches.get(argv.node_name):
                return None
            return home_region if argv.node_name == '返回主菜单_文字' else (260, 10, 70, 50)

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition('HomePopupPage', Page())
    assert resource.register_custom_action('ClickAnchor', ClickAnchor())
    startup = jsonc.loads((ROOT / 'resource/base/pipeline/Start_up.jsonc').read_text('utf-8'))
    preserved = {'返回主菜单', '关闭点击空白弹窗', '检查主界面'}
    names = {name.removeprefix('[JumpBack]') for name in startup['启动']['next']} - preserved
    names.update(['返回主菜单_文字', '关闭奖励_文字', '关闭奖励_颜色',
                  '关闭公告', '关闭拟真围剿通知', '月卡续期',
                  '主界面_战斗节点', '确定_文字'])
    names.discard('关闭奖励')
    timing = dict(pre_delay=0, post_delay=0, timeout=150, rate_limit=1,
                  pre_wait_freezes=0, post_wait_freezes=0, on_error=[])
    overrides = {
        name: dict(timing, recognition={'type': 'Custom', 'param': {'custom_recognition': 'HomePopupPage'}})
        for name in names
    }
    for name in preserved | {'启动', '关闭奖励', '返回主菜单_reco', '确定'}:
        overrides[name] = dict(timing)
    overrides['检查主界面2'] = dict(timing, action={'type': 'StopTask'}, next=[])
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
        job = tasker.post_task(entry, overrides).wait()
        assert not expired
        assert job.succeeded is recognized
        assert [page for page, _, _ in clicks] == ['submenu', 'reward'] + (
            ['popup'] * popup_count if recognized else []
        )
        assert state['page'] == ('home' if recognized else 'popup')
        x, y, w, h = home_region
        assert clicks[0] == ('submenu', x + w, y + h // 2)
        assert all((x, y) == (640, 10) for page, x, y in clicks if page == 'popup')
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
