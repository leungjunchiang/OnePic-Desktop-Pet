"""验证进程级存活与专注活动分离、后台心跳、退出下线、多设备兼容及过期展示。"""
import os
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from onepic_desktop_pet.social import _heartbeat_payload, _atomic_presence_body, _presence_rpc_path
from onepic_desktop_pet.social import HttpSocialBackend, SocialSession
import onepic_desktop_pet.social as social_module
from onepic_desktop_pet.social_ui import (
    _presence_status, _presence_is_online, _buddy_status_rank,
    SocialHeartbeatWorker, SocialHubDialog, BuddyCardWidget,
)
from test_window import _create_window
from test_social_ui import RoomClient


@pytest.mark.parametrize("activity,status,rank", [("focus","focus",0),("rest","rest",1),("idle","online",2)])
def test_one_resolver_for_activity_ttl_and_sort(activity,status,rank):
    now=datetime.now(timezone.utc)
    row={"presence_state":"online","activity_state":activity,"last_seen_at":now.isoformat()}
    assert _presence_status(row,now)==status
    assert _presence_is_online(row)
    assert _buddy_status_rank(row)==rank
    row["last_seen_at"]=(now-timedelta(seconds=121)).isoformat()
    assert _presence_status(row,now)=="offline"
    assert not _presence_is_online(row)
    assert _buddy_status_rank(row)==3
    row.update(last_seen_at=now.isoformat(),presence_state="offline")
    assert _presence_status(row,now)=="offline"


def test_modern_wire_is_small_and_offline_cannot_retain_a_focus_tuple():
    body=_heartbeat_payload(dict(user_id="a",presence_state="offline",activity_state="idle",
        working=True,session_active=True,session_id="s",session_started_at="2026-10-01T00:00:00Z",
        today_seconds=100,weekly_seconds=999,work_plan={},outfit_key="x",input_idle_seconds=0))
    assert not body["working"] and body["session_id"] is None
    rpc=_atomic_presence_body(body)
    assert set(rpc)=={"p_presence_state","p_activity_state","p_working","p_session_active",
        "p_session_id","p_session_started_at","p_device_id","p_sequence","p_input_idle_seconds"}
    assert _presence_rpc_path(body).endswith("lili_presence_heartbeat")
    assert _presence_rpc_path(_heartbeat_payload({"working":False})).endswith("lili_upsert_focus_presence_v2")


def test_local_activity_and_window_hiding_keep_liveness():
    app,window=_create_window()
    try:
        assert window._build_local_liveness_presence(user_id="a")["activity_state"]=="idle"
        window.start_work_timer()
        assert window._build_local_liveness_presence(user_id="a")["activity_state"]=="focus"
        window.pause_work_timer()
        assert window._build_local_liveness_presence(user_id="a")["activity_state"]=="rest"
        window.finish_work_timer()
        assert window._build_local_liveness_presence(user_id="a")["activity_state"]=="idle"
        window.close_to_tray=True
        window.social_heartbeat_watchdog_timer.start(60_000)
        window.showMinimized(); app.processEvents()
        window.close(); app.processEvents()
        assert not window._close_in_progress
        assert window.social_heartbeat_watchdog_timer.isActive()
        assert window._build_local_liveness_presence(user_id="a")["presence_state"]=="online"
    finally:
        window.application_exit_requested=True
        window.close(); window.deleteLater(); app.processEvents()


def test_background_idle_heartbeat_repeats_with_one_worker_and_final_offline():
    app,window=_create_window()
    sent=threading.Event()
    class Client:
        payloads=[]
        def heartbeat(self,**payload):
            self.payloads.append(payload); sent.set()
    client=Client()
    worker=SocialHeartbeatWorker(client,interval_seconds=5)
    try:
        worker.update_presence(window._build_local_liveness_presence(user_id="idle-a"))
        worker.start(); original=worker._thread
        worker.start(); assert worker._thread is original
        assert sent.wait(2)
        sent.clear(); window.hide_pet()
        # No GUI event loop, focus, visible window or FocusSession is required.
        assert sent.wait(6)
        assert len(client.payloads)>=2
        assert all(p["presence_state"]=="online" and p["activity_state"]=="idle" for p in client.payloads)
        worker.stop({"user_id":"idle-a","presence_state":"offline","activity_state":"idle","working":False})
        assert worker.wait(2000)
        assert client.payloads[-1]["presence_state"]=="offline"
    finally:
        worker.stop(); worker.wait(2000)
        window.close(); window.deleteLater(); app.processEvents()


def test_self_card_does_not_inherit_remote_offline_or_old_ttl(monkeypatch):
    app=QApplication.instance() or QApplication([])
    client=RoomClient()
    dialog=SocialHubDialog(client)
    captured=[]
    monkeypatch.setattr(dialog,"_render_room_people",lambda rows:captured.extend(rows))
    data=client.dashboard()
    data["rooms"][0]["id"]="room-1"
    dialog.current_room_id="room-1"
    dialog._room_selection_explicit=True
    data["me_presence"]={"online":False,"presence_state":"offline","status":"offline",
        "last_seen_at":"2026-01-01T00:00:00Z","last_confirmed_at":"2026-01-01T00:00:00Z","stale_presence":True}
    dialog._focus_snapshot=SimpleNamespace(status="idle",is_running=False,session_seconds=0,today_seconds=0)
    try:
        dialog.apply_dashboard(data)
        own=next(row for row in captured if row.get("is_self"))
        assert _presence_status(own)=="online"
        card=BuddyCardWidget(own)
        assert "在线" in card._headline_label.text()
        assert card._confirmation_label.isHidden()
        card.deleteLater()
    finally:
        dialog.close(); dialog.deleteLater(); app.processEvents()


def test_real_quit_waits_for_offline_ack_without_blocking_qt(monkeypatch):
    app,window=_create_window()
    sent=threading.Event(); quitting=threading.Event(); release=threading.Event()
    class Client:
        payloads=[]
        def heartbeat(self,**payload):
            self.payloads.append(payload)
            if payload["presence_state"]=="offline":
                quitting.set(); release.wait(2)
            sent.set()
    client=Client(); worker=SocialHeartbeatWorker(client)
    window._social_heartbeat_thread=worker
    monkeypatch.setattr(window,"_current_social_user_id",lambda:"exit-a")
    try:
        worker.update_presence(window._build_local_liveness_presence(user_id="exit-a")); worker.start()
        assert sent.wait(2)
        window.close_to_tray=True; window.application_exit_requested=True
        assert window.close() is False  # asynchronous exit drain, no UI socket wait
        assert quitting.wait(2)
        assert window._close_retry_scheduled
        release.set(); assert worker.wait(2000)
        QTest.qWait(300)
        assert not window.isVisible()
        assert sum(p["presence_state"]=="offline" for p in client.payloads)==1
    finally:
        release.set(); worker.stop(); worker.wait(2000)
        window.close(); window.deleteLater(); app.processEvents()


def test_direct_http_heartbeat_preserves_explicit_state_with_no_readback(monkeypatch,tmp_path):
    monkeypatch.setattr(social_module,"account_local_data_path",lambda name,account:tmp_path/name)
    social_module._PRESENCE_DEVICE_STATE_CACHE.clear()
    backend=HttpSocialBackend("https://example.test",client_key="test-public",persist_tokens=False,transport="direct")
    backend.session=SocialSession("a","r","test-presence-user",9_999_999_999)
    calls=[]
    monkeypatch.setattr(backend,"_raw",lambda method,path,body=None,**kwargs:calls.append((method,path,body)) or {"accepted":True})
    backend.heartbeat(working=False,presence_state="online",activity_state="idle")
    backend.heartbeat(working=False,presence_state="offline",activity_state="idle")
    assert len(calls)==2 and all(method=="POST" for method,_,_ in calls)
    assert all(path.endswith("lili_presence_heartbeat") for _,path,_ in calls)
    assert calls[0][2]["p_presence_state"]=="online"
    assert calls[1][2]["p_presence_state"]=="offline"
    assert calls[1][2]["p_sequence"]>calls[0][2]["p_sequence"]
    assert len(calls[0][2])==9
