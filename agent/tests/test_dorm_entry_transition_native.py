"""A stale home frame during dorm loading must not cause another entry click."""
from threading import Timer

import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize('language', ['base', 'en_US', 'zh_TW', 'ja_JP'])
@pytest.mark.parametrize('case', ['stale_home', 'slow_load', 'never_load', 'already_inside', 'wrong_room'])
def test_dorm_entry_waits_for_overview_before_returning_to_parent(tmp_path, language, case):
    state = {'page': {'already_inside': 'overview', 'wrong_room': 'room'}.get(case, 'home'),
             'frames': 0}
    clicks = []
    threshold = {'stale_home': 4, 'slow_load': 8, 'never_load': 10000}.get(case, 4)

    class Capture(Controller):
        def screencap(self):
            if state['page'] == 'loading':
                state['frames'] += 1
                if state['frames'] >= threshold:
                    state['page'] = 'overview'
            return super().screencap()

        def click(self, x, y):
            clicks.append((x, y))
            state['page'] = 'loading' if len(clicks) == 1 else 'room'
            return True

    class Page(CustomRecognition):
        def analyze(self, context, argv):
            if argv.node_name == '打开宿舍' and state['page'] in {'home', 'loading'}:
                return (830, 393, 45, 29)
            if argv.node_name == '宿舍内部' and state['page'] == 'overview':
                return (152, 651, 59, 29)
            return None

    resource = load_resource(tmp_path, language)
    assert resource.register_custom_recognition('DormEntryPage', Page())
    timing = dict(pre_delay=0, post_delay=0, timeout=180, rate_limit=1, on_error=[])
    overrides = {name: dict(timing) for name in [
        '宿舍任务', '打开宿舍', '宿舍内部',
    ]}
    if '打开宿舍_已进入总览' in resource.node_list:
        overrides['打开宿舍_已进入总览'] = dict(timing)
    for name in ['打开宿舍', '宿舍内部']:
        overrides[name]['recognition'] = {'type': 'Custom', 'param': {'custom_recognition': 'DormEntryPage'}}
    overrides['宿舍内部'].update(action={'type': 'StopTask'}, next=[])
    overrides['意外点击_主界面'] = {'enabled': False}
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
        job = tasker.post_task('宿舍任务', overrides).wait()
        assert not expired
        assert job.succeeded is (case in {'stale_home', 'slow_load', 'already_inside'})
        assert len(clicks) == (0 if case in {'already_inside', 'wrong_room'} else 1)
        assert state['page'] != 'room' or case == 'wrong_room'
        if clicks:
            x, y = clicks[0]
            assert 830 <= x < 875 and 393 <= y < 422
    finally:
        watchdog.cancel()
        watchdog.join()
        stop_and_wait_idle(tasker)
