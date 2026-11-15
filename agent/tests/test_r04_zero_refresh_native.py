"""Exhausted recruitment refreshes restart R04 instead of claiming completion."""
import jsonc
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize('language',['base','en_US','zh_TW','ja_JP'])
def test_no_supported_recruit_abandons_round_then_restarts(language,tmp_path):
    state={'page':'recruit','delay':0}
    clicks=[]
    class Capture(Controller):
        def screencap(self):
            if state['page']=='fading':
                state['delay']-=1
                if state['delay']==0: state['page']='menu'
            return super().screencap()
        def click(self,x,y):
            page=state['page']
            clicks.append(page)
            if page=='recruit': state.update(page='fading',delay=3)
            elif page=='menu': state['page']='confirm'
            elif page=='confirm': state['page']='result'
            elif page=='result': state['page']='ready'
            else: raise AssertionError(page)
            return True
    class Pages(CustomRecognition):
        def analyze(self,ctx,argv):
            page,name=state['page'],argv.node_name
            hits={
                'recruit':{'选择人物_寒境曙光','无刷新次数_寒境曙光','返回_文字'},
                'menu':{'终止_寒境曙光'},
                'confirm':{'确定终止_寒境曙光'},
                'result':{'退出_文字'},
                'ready':{'寒境曙光主界面_重开'},
            }
            if name in hits.get(page,set()): return (840,610,160,70)
            if page=='ready' and name=='寒境曙光菜单启程_寒境曙光': return (1030,590,240,100)
            return None
    resource=load_resource(tmp_path,language)
    assert resource.register_custom_recognition('R04Pages',Pages())
    nodes=jsonc.loads((ROOT/'resource/base/pipeline/Roguelike_4/Roguelike_4.jsonc').read_text(encoding='utf-8'))
    flow=['寒境曙光','选择人物_寒境曙光','刷新次数用尽失败_寒镜曙光',
          '重开游戏_寒境曙光','完成所有任务_重开']
    names={'无刷新次数_寒境曙光','返回_文字','终止_寒境曙光',
           '确定终止_寒境曙光','退出_文字','寒境曙光主界面_重开'}
    for name in flow:
        names.update(n.removeprefix('[JumpBack]') for n in nodes[name].get('next',[]))
    overrides={name:{'recognition':{'type':'Custom','param':{'custom_recognition':'R04Pages'}},
                    'pre_delay':0,'post_delay':0,'timeout':500,'rate_limit':1,'on_error':[]}
               for name in names}
    for name in flow:
        overrides.setdefault(name,{})
        overrides[name].update(pre_delay=0,post_delay=0,timeout=500,rate_limit=1,on_error=[])
    # Keep production composites and their real actions for the return/restart chain.
    for name in ['刷新次数用尽失败_寒镜曙光','重开游戏_寒境曙光','完成所有任务_重开']:
        overrides[name]['recognition']=nodes[name]['recognition']
    overrides['寒境曙光菜单启程_寒境曙光'].update(action={'type':'StopTask'},next=[])
    controller,tasker=Capture(),Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource,controller)
    try:
        job=tasker.post_task('选择人物_寒境曙光',overrides).wait()
        assert job.succeeded,(clicks,state)
        visited=[tasker.get_node_detail(n).name for n in tasker.get_task_detail(job.job_id).node_id_list]
        assert clicks==['recruit','menu','confirm','result']
        assert visited[-1]=='寒境曙光菜单启程_寒境曙光'
        assert '寒境曙光_日耗尽停止' not in visited
    finally:
        stop_and_wait_idle(tasker)
