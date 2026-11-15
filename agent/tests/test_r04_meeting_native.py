"""R04's title OCR must include the first glyph beyond the color anchor."""
import cv2
import jsonc
import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition
from maa.tasker import Tasker

from test_r01_zero_refresh_native import ROOT, Controller, load_resource
from test_support.native_lifecycle import stop_and_wait_idle


@pytest.mark.parametrize('language',['base','en_US','zh_TW','ja_JP'])
@pytest.mark.parametrize('entry',['寒境曙光','汇合_寒境曙光_yolo'])
def test_meeting_title_routes_to_exploration_without_manual_navigation(tmp_path,language,entry):
    state={'page':'event'}
    clicks=[]
    class Capture(Controller):
        def screencap(self):
            image=np.zeros((720,1280,3),np.uint8)
            if state['page']=='event':
                cv2.putText(image,'RALLY',(700,110),cv2.FONT_HERSHEY_SIMPLEX,1.2,(255,255,255),2)
                cv2.putText(image,'EXPLORE',(740,340),cv2.FONT_HERSHEY_SIMPLEX,.8,(255,255,255),2)
            return image
        def click(self,x,y):
            assert state['page']=='event' and 734<=x<891 and 310<=y<350
            clicks.append((x,y))
            state['page']='done'
            return True
    class Page(CustomRecognition):
        def analyze(self,ctx,argv):
            if state['page']=='event' and argv.node_name=='事件标题_寒境曙光_yolo_颜色':
                return (715,76,63,36)
            if state['page']=='done' and argv.node_name=='离开对话_寒境曙光':
                return (100,10,150,40)
            return None
    resource=load_resource(tmp_path,language)
    assert resource.register_custom_recognition('R04MeetingPage',Page())
    main=jsonc.loads((ROOT/'resource/base/pipeline/Roguelike_4/Roguelike_4.jsonc').read_text(encoding='utf-8'))
    names={n.removeprefix('[JumpBack]') for n in main['寒境曙光']['next']}
    names.update(['事件标题_寒境曙光_yolo_颜色','离开对话_寒境曙光','汇合_对话_寒境曙光'])
    names.remove('汇合_寒境曙光_yolo')
    overrides={name:{'recognition':{'type':'Custom','param':{'custom_recognition':'R04MeetingPage'}},
                    'pre_delay':0,'post_delay':0,'timeout':1000,'rate_limit':1,'on_error':[]}
               for name in names}
    overrides['汇合_寒境曙光_yolo_文本']={'recognition':{'param':{'expected':'RALLY'}}}
    overrides['开始探索_寒境曙光']={'recognition':{'param':{'expected':'EXPLORE'}},'pre_delay':0,'post_delay':0}
    for name in ['寒境曙光','汇合_寒境曙光_yolo']:
        overrides[name]={'pre_delay':0,'post_delay':0,'timeout':1000,'rate_limit':1,'on_error':[]}
    overrides['离开对话_寒境曙光'].update(action={'type':'StopTask'},next=[])
    controller,tasker=Capture(),Tasker()
    assert controller.post_connection().wait().succeeded and tasker.bind(resource,controller)
    try:
        assert tasker.post_task(entry,overrides).wait().succeeded
        assert len(clicks)==1 and state['page']=='done'
    finally:
        stop_and_wait_idle(tasker)
