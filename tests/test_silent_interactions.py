"""验证免打扰到达时的独立标记、首次处理、跨设备回执、失败恢复和既有窗口抑制。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from onepic_desktop_pet.social import SocialError
from onepic_desktop_pet.social_ui import SocialHubDialog
from onepic_desktop_pet.window import PetWindow
from onepic_desktop_pet.time_service import now_beijing

class Client:
    signed_in=True
    def __init__(self):
        self.session=SimpleNamespace(user_id='me')
        self.calls=[]; self.release=threading.Event(); self.fail=False; self.first=True
    def rpc(self,name,body):
        self.calls.append((name,body)); self.release.wait(2)
        if self.fail: raise SocialError('断网',kind='network')
        if name=='lili_handle_silent_interaction':
            result={'event_id':body['p_event_id'],'first_handled':self.first,'event_type':'tease','taunt_state':{'active':True}}
            self.first=False
            return result
        return 'sent-id'

@pytest.fixture
def dialog():
    app=QApplication.instance() or QApplication([])
    client=Client(); widget=SocialHubDialog(client); widget._initial_refresh_timer.stop()
    widget.refresh=lambda:None
    yield widget,client,app
    client.release.set()
    wait(app,lambda:not widget._buddy_rpc_threads)
    widget.close();widget.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete)

def wait(app,condition):
    for _ in range(400):
        app.processEvents()
        if condition():return
        QTest.qWait(5)
    assert condition()

def row(**changes):
    return {'event_id':'visit:original','sender_id':'buddy','nickname':'搭子','event_type':'tease',
            'source':'visit','received_silent':True,'handled_at':None,'created_at':now_beijing().isoformat(),**changes}

def test_first_response_applies_original_effect_before_reply_and_only_once(dialog):
    widget,client,app=dialog; client.release.set(); effects=[]; outcomes=[]
    widget.silent_interaction_handled.connect(effects.append)
    original=row(_response_done=outcomes.append);widget._interaction_rows=[row()]
    widget._interaction_response(original,'taunt')
    wait(app,lambda:not widget._buddy_rpc_threads)
    assert [n for n,b in client.calls]==['lili_handle_silent_interaction','lili_send_interaction']
    assert len(effects)==1 and effects[0]['event_type']=='tease'
    assert outcomes==[True] and widget._interaction_rows[0]['handled_at']
    widget._interaction_response(original,'taunt') # Second computer's stale unhandled row.
    wait(app,lambda:not widget._buddy_rpc_threads)
    assert len(effects)==1 and outcomes==[True,True]

@pytest.mark.parametrize('values',[{'received_silent':False},{'handled_at':'already-handled'},{'received_silent':None}])
def test_normal_and_previously_handled_events_never_replay(dialog,values):
    widget,client,app=dialog;client.release.set();effects=[]
    widget.data={'me':{'buddy_interaction_mode':'do_not_disturb'}} # Current mode is irrelevant.
    widget.silent_interaction_handled.connect(effects.append)
    widget._interaction_response(row(**values),'cheer')
    wait(app,lambda:not widget._buddy_rpc_threads)
    assert len(client.calls)==1 and client.calls[0][0]=='lili_send_interaction' and effects==[]

def test_double_click_cannot_claim_same_event_twice(dialog):
    widget,client,app=dialog;widget._interaction_response(row(),'taunt')
    widget._interaction_response(row(),'cheer')
    wait(app,lambda:bool(client.calls));assert len(client.calls)==1
    client.release.set();wait(app,lambda:not widget._buddy_rpc_threads)
    assert len(client.calls)==2

def test_failed_claim_restores_button_without_reply_or_effect(dialog):
    widget,client,app=dialog;client.fail=True;effects=[]
    widget.silent_interaction_handled.connect(effects.append)
    feed=widget.interaction_feed;feed.render([row()]);button=feed._response_buttons[('visit:original','taunt')]
    button.click();assert not button.isEnabled()
    client.release.set();wait(app,lambda:not widget._buddy_rpc_threads)
    assert button.isEnabled() and button.text()=='嘲讽回去' and effects==[]
    assert len(client.calls)==1 and not widget._silent_interaction_pending

def test_acknowledgement_also_claims_first_effect_and_settles_feedback(dialog):
    widget,client,app=dialog;client.release.set()
    feed=widget.interaction_feed;feed.render([row(event_type='flower')]);button=feed._response_buttons[('visit:original','ack')]
    button.click();assert not button.isEnabled()
    wait(app,lambda:not widget._buddy_rpc_threads)
    assert button.text()=='✓ 已处理' and len(client.calls)==1

def test_account_switch_does_not_apply_old_accounts_claim(dialog):
    widget,client,app=dialog;effects=[];outcomes=[]
    widget.silent_interaction_handled.connect(effects.append)
    widget._interaction_response(row(_response_done=outcomes.append),'taunt')
    wait(app,lambda:bool(client.calls));client.session=SimpleNamespace(user_id='other');client.release.set()
    wait(app,lambda:not widget._buddy_rpc_threads)
    assert effects==[] and outcomes==[False] and len(client.calls)==1

def test_opening_pending_food_details_is_not_handling(dialog):
    widget,client,app=dialog
    widget._interaction_response(row(event_type='food_tea',requires_action=True),'handle')
    assert client.calls==[]

def test_welcome_mode_does_not_auto_accept_food_received_silently(dialog):
    widget,client,app=dialog
    widget.data={'me':{'buddy_interaction_mode':'welcome'},'visits':[{'id':'food','kind':'food_tea','payload':{'received_silent':True}}]}
    widget._auto_accept_light_food_interactions();assert client.calls==[]

def test_silent_delivery_revision_invalidates_inbox_cache_without_polling(dialog):
    widget,client,app=dialog
    data={'me':{'user_id':'me','buddy_interaction_mode':'do_not_disturb'},'buddies':[],
          'requests':[],'visits':[],'rooms':[],'room_people':[], 'interaction_delivery_revision':'event-1'}
    widget.apply_dashboard(data);widget._interaction_loaded=True;widget._interaction_dirty=False
    widget.apply_dashboard(data)
    assert not widget._interaction_dirty and client.calls==[]
    widget.apply_dashboard({**data,'interaction_delivery_revision':'event-2'})
    assert widget._interaction_dirty and client.calls==[]

@pytest.mark.parametrize('kind,state',[('tease','taunt_state'),('cheer','encouragement_state')])
def test_explicit_effect_reuses_existing_state_without_showing_fullscreen_pet(kind,state):
    calls=[]
    holder=SimpleNamespace(_taunt_active=False,_apply_taunt_state=lambda s:calls.append(('taunt',s)) or True,
        _apply_encouragement_state=lambda s:calls.append(('cheer',s)) or True,
        _passive_surfaces_blocked=lambda:True,_show_interaction_hint=lambda *a:pytest.fail('Fullscreen hint shown'),
        _set_temporary_activity=lambda *a:calls.append(('temporary',a)))
    PetWindow._handle_silent_interaction(holder,{'first_handled':True,'event_type':kind,state:{'active':True}})
    assert calls==[('taunt' if kind=='tease' else 'cheer',{'active':True})]
    PetWindow._handle_silent_interaction(holder,{'first_handled':False,'event_type':kind,state:{'active':True}})
    assert len(calls)==1

def test_dnd_room_events_do_not_change_pet_state():
    holder=SimpleNamespace(_social_notification_dnd=True,_set_temporary_activity=lambda *a:pytest.fail('Silent room effect played'))
    PetWindow._room_event_received(holder,{'kind':'cheer'})

def test_handled_effect_consumes_passive_notice_eligibility():
    manager=SimpleNamespace(shown_event_ids=set())
    holder=SimpleNamespace(notification_manager=manager,_apply_taunt_state=lambda s:True,
        _passive_surfaces_blocked=lambda:True)
    PetWindow._handle_silent_interaction(holder,{'first_handled':True,'event_id':'visit:original',
        'event_type':'tease','taunt_state':{'id':'reaction','active':True}})
    assert manager.shown_event_ids=={'visit:original','taunt:reaction'}
