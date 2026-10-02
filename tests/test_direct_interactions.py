"""验证直接回应继承原发送者、单次异步 RPC、防连点、失败恢复和投喂复用。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from onepic_desktop_pet.social import SocialError
from onepic_desktop_pet.social_ui import SocialHubDialog
from onepic_desktop_pet.interaction_center import InteractionFeed
from onepic_desktop_pet.time_service import now_beijing
from onepic_desktop_pet.window import PetWindow

@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

def wait_for(app, condition):
    for _ in range(400):
        app.processEvents()
        if condition(): return
        QTest.qWait(5)
    assert condition()

class Client:
    signed_in = True
    session = SimpleNamespace(user_id="me")
    def __init__(self):
        self.calls = []
        self.release = threading.Event()
        self.error = False
    def rpc(self, name, body):
        self.calls.append((name, body))
        self.release.wait(2)
        if self.error: raise SocialError("对方暂不接受互动")
        return "sent-id"

@pytest.mark.parametrize("action,kind", [("taunt","tease"),("cheer","cheer"),("flower","flower"),("visit","visit")])
def test_reply_uses_original_sender_without_any_room_reads(app, action, kind):
    client = Client()
    dialog = SocialHubDialog(client)
    dialog.current_room_id = None
    dialog._send_room_interaction = lambda *a: pytest.fail("Reply entered room path")
    dialog._refresh_selected_room = lambda: pytest.fail("Reply refreshed room")
    outcomes = []
    dialog._interaction_response({"event_id":"taunt:original", "sender_id":"original-sender",
        "nickname":"重名搭子", "_response_done":outcomes.append}, action)
    wait_for(app, lambda: bool(client.calls))
    assert outcomes == []
    client.release.set()
    wait_for(app, lambda: not dialog._buddy_rpc_threads)
    assert outcomes == [True]
    assert len(client.calls) == 1
    name, body = client.calls[0]
    assert name == "lili_send_interaction"
    assert body["p_target"] == "original-sender"
    assert body["p_kind"] == kind
    assert body["p_room_id"] is None
    assert body["p_payload"]["reply_to_event_id"] == "taunt:original"
    assert "共同房间" not in dialog.status_label.text()
    dialog.close(); dialog.deleteLater()

def test_response_button_immediate_feedback_survives_render_and_failure(app):
    client = Client(); client.error = True
    dialog = SocialHubDialog(client)
    # This test owns the interaction RPC lifecycle only. Stop the unrelated
    # 50ms bootstrap dashboard refresh so slower macOS runners cannot replace
    # the transient failure status while this assertion is observing it.
    dialog._initial_refresh_timer.stop()
    row = {"event_id":"visit:1", "sender_id":"b", "nickname":"搭子", "event_type":"cheer", "created_at":now_beijing().isoformat()}
    feed = dialog.interaction_feed
    feed.render([row])
    button = feed._response_buttons[("visit:1","cheer")]
    button.click()
    assert button.text() == "正在发送…" and not button.isEnabled()
    button.click()
    wait_for(app, lambda: bool(client.calls))
    feed.render([{**row,"unread":True}])
    button = feed._response_buttons[("visit:1","cheer")]
    assert button.text() == "正在发送…" and not button.isEnabled()
    client.release.set()
    wait_for(app, lambda: not dialog._buddy_rpc_threads)
    assert button.text() == "回个加油" and button.isEnabled()
    assert len(client.calls) == 1
    assert dialog._status_timer.isActive() and dialog._status_timer.interval() == 4000
    dialog._status_timer.timeout.emit()
    assert dialog.status_label.isHidden()
    dialog._set_status("新状态")
    assert not dialog._status_timer.isActive() and not dialog.status_label.isHidden()
    dialog.close(); dialog.deleteLater()

def test_reply_success_stays_disabled_then_restores(app):
    captured = []
    feed = InteractionFeed(lambda row, action: captured.append(row["_response_done"]))
    feed.render([{"event_id":"visit:2","event_type":"tease","created_at":now_beijing().isoformat()}])
    button = feed._response_buttons[("visit:2","taunt")]
    button.click(); button.click()
    assert len(captured) == 1
    captured[0](True)
    assert button.text() == "✓ 已回击" and not button.isEnabled()
    QTest.qWait(2150)
    assert button.text() == "嘲讽回去" and button.isEnabled()
    feed.close(); feed.deleteLater()

def test_account_switch_ignores_old_result_but_restores_button(app):
    client = Client(); client.session = SimpleNamespace(user_id="me")
    dialog = SocialHubDialog(client)
    outcomes = []
    dialog._interaction_response({"event_id":"visit:3","sender_id":"b","_response_done":outcomes.append},"cheer")
    wait_for(app, lambda: bool(client.calls))
    client.session = SimpleNamespace(user_id="other")
    client.release.set()
    wait_for(app, lambda: not dialog._buddy_rpc_threads)
    assert outcomes == [False]
    assert "✓ 已向" not in dialog.status_label.text()
    dialog.close(); dialog.deleteLater()

@pytest.mark.parametrize("kind", ["praise","knock_desk","remind_start","flower","taunt"])
def test_ordinary_buddy_actions_default_to_direct(app, kind, monkeypatch):
    monkeypatch.setattr("onepic_desktop_pet.social_ui._taunt_window_open",lambda:True)
    client = Client(); client.release.set()
    dialog = SocialHubDialog(client)
    dialog.current_room_id = None
    dialog._send_interaction({"user_id":"b"},kind)
    wait_for(app,lambda:not dialog._buddy_rpc_threads)
    assert len(client.calls)==1 and client.calls[0][1]["p_room_id"] is None
    dialog.close();dialog.deleteLater()

def test_idle_cheer_is_not_reinterpreted_as_taunt(app):
    client=Client();client.release.set()
    dialog=SocialHubDialog(client)
    dialog._send_interaction({"user_id":"b","status":"offline","online":False},"cheer")
    wait_for(app,lambda:not dialog._buddy_rpc_threads)
    assert client.calls[0][1]["p_kind"]=="cheer"
    dialog.close();dialog.deleteLater()

def food_holder(balance=100):
    requests = []; charges = []; outcomes = []
    economy = SimpleNamespace(balance=balance,catalog=lambda:{"coffee":{"price":5}},
        record_food_gift_sent=lambda *a,**k: charges.append((a,k)) or SimpleNamespace(as_dict=lambda:{}))
    holder = SimpleNamespace(social_client=SimpleNamespace(signed_in=True),economy=economy,
        _current_social_user_id=lambda:"me",show_speech=lambda *a:None,
        _discipline_rpc=lambda *a,**k: requests.append((a,k)),_sync_economy_events=lambda *a:None,
        _set_social_food_activity=lambda *a:None)
    peer = {"user_id":"original", "_reply_to_event_id":"visit:food", "_response_done":lambda ok,msg="":outcomes.append(ok)}
    return holder,peer,requests,charges,outcomes

def test_food_reply_keeps_economy_and_no_room_required(app):
    holder,peer,requests,charges,outcomes = food_holder()
    PetWindow._send_food_interaction(holder,peer,"food_coffee")
    assert charges == [] and outcomes == []
    args, kwargs = requests[0]
    assert args[0] == "lili_send_interaction"
    assert args[1]["p_target"] == "original" and args[1]["p_room_id"] is None
    assert args[1]["p_payload"]["reply_to_event_id"] == "visit:food"
    assert "_response_done" not in args[1]["p_payload"]
    args[2]("event")
    kwargs["finally_callback"]()
    assert len(charges) == 1 and outcomes == [True] and not holder._pending_food_gifts

@pytest.mark.parametrize("mode",["balance","failure","account_switch"])
def test_food_failure_paths_release_reply_feedback(app,mode):
    holder,peer,requests,charges,outcomes=food_holder(0 if mode=="balance" else 100)
    PetWindow._send_food_interaction(holder,peer,"food_coffee")
    if requests:
        args,kwargs=requests[0]
        if mode=="failure":args[3](RuntimeError("断网"))
        kwargs["finally_callback"]()
    assert outcomes==[False] and charges==[]
    assert not getattr(holder,"_pending_food_gifts",{})
