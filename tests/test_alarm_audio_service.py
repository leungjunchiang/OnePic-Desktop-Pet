"""本地播放所有权、已关闭 occurrence、无变化保存与诊断隐私回归。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from datetime import datetime
from PySide6.QtWidgets import QApplication
from onepic_desktop_pet.alarm_audio_service import AlarmAudioService
from onepic_desktop_pet.alarm_manager import AlarmManager
from onepic_desktop_pet import lifecycle_log

def app():
    return QApplication.instance() or QApplication([])

def test_occurrence_plays_once_and_cannot_restart_after_stop():
    app(); service=AlarmAudioService(); owner=SimpleNamespace(); calls=[]
    assert service.play(owner,"day:slot",lambda:calls.append("play"),lambda:calls.append("stop"))
    assert not service.play(owner,"day:slot",lambda:calls.append("repeat"),lambda:None)
    assert service.stop(owner)
    assert not service.play(owner,"day:slot",lambda:calls.append("repeat"),lambda:None)
    assert calls==["play","stop"] and service.state=="stopped"
    assert service.play(owner,"next-day:slot",lambda:calls.append("next"),lambda:None)
    service.stop(owner)

def test_handoff_waits_for_native_stop_and_old_owner_cannot_stop_new():
    app(); service=AlarmAudioService(); calls=[]
    backend=SimpleNamespace(closed=False,playback_stopped=False)
    first=SimpleNamespace(_windows_preview_audio=backend); second=SimpleNamespace()
    service.play(first,"preview",lambda:calls.append("preview"),lambda:calls.append("stop-preview"),priority=0)
    service.play(second,"alarm",lambda:calls.append("alarm"),lambda:calls.append("stop-alarm"))
    assert calls==["preview","stop-preview"] and service.state=="stopping"
    backend.playback_stopped=True;service._advance()
    assert calls[-1]=="alarm" and service.state=="playing"
    assert not service.stop(first)
    assert service.current[0] is second
    service.stop(second)

def test_preview_cannot_interrupt_alarm_and_pending_can_cancel():
    app(); service=AlarmAudioService(); alarm=SimpleNamespace(); preview=SimpleNamespace();calls=[]
    service.play(alarm,"a",lambda:None,lambda:None)
    assert not service.play(preview,"p",lambda:calls.append("preview"),lambda:None,priority=0)
    service.stop(alarm)
    blocker=SimpleNamespace(closed=False,playback_stopped=False)
    service.stopping=[blocker]
    service.play(preview,"q",lambda:calls.append("play"),lambda:calls.append("cancel"))
    assert service.stop(preview)
    blocker.closed=True;service._advance()
    assert calls==["cancel"]

def test_unchanged_save_does_not_write_or_clear_claim(tmp_path,monkeypatch):
    now=datetime(2026,10,1,11,30)
    store=AlarmManager(tmp_path / "alarms.json",now_provider=lambda:now)
    alarm=store.add("test",now,sound_enabled=True)
    assert store.claim_due(now=now)==[alarm]
    store.dismiss(alarm.id)
    before=(alarm.schedule_generation,alarm.last_triggered_slot)
    writes=[];monkeypatch.setattr(store,"_save",lambda:writes.append(True))
    store.update(alarm.id,title=alarm.title,trigger_at=alarm.trigger_at,enabled=alarm.enabled)
    assert not writes and before==(alarm.schedule_generation,alarm.last_triggered_slot)
    assert not store.claim_due(now=now)

def test_label_or_volume_edit_does_not_replay_closed_daily_alarm(tmp_path):
    now=datetime(2026,10,1,11,30)
    store=AlarmManager(tmp_path / "alarms.json",now_provider=lambda:now)
    alarm=store.add("test",now,repeat_rule="daily")
    store.claim_due(now=now);store.dismiss(alarm.id)
    consumed=alarm.last_triggered_slot
    store.update(alarm.id,title="new title",volume=42)
    assert alarm.last_triggered_slot==consumed
    assert not store.claim_due(now=now)
    assert store.claim_due(now=datetime(2026,10,2,11,30))==[alarm]

def test_network_metrics_are_opt_in_and_do_not_record_query_or_user_path(monkeypatch):
    rows=[];monkeypatch.setattr(lifecycle_log,"lifecycle_log",lambda *a,**kw:rows.append((a,kw)))
    monkeypatch.delenv("LILI_NETWORK_DIAGNOSTICS",raising=False)
    lifecycle_log.network_response_log("POST","/rooms/private-user?token=secret",123,0)
    assert not rows
    monkeypatch.setenv("LILI_NETWORK_DIAGNOSTICS","1")
    lifecycle_log.network_response_log("POST","/rooms/private-user?token=secret",123,0)
    assert rows[0][1]["route"]=="/rooms" and rows[0][1]["response_bytes"]==123
    assert "secret" not in str(rows) and "private-user" not in str(rows)


def test_wave_repeat_is_not_attempted_and_failed_mci_file_is_cached(tmp_path):
    from onepic_desktop_pet.alarm_ui import _WindowsAlarmAudio
    wav=_WindowsAlarmAudio(str(tmp_path/'tone.wav'),volume=0,on_finished=lambda:None,on_error=lambda e:None)
    wav._mci=lambda *a:0
    assert not wav.available
    path=tmp_path/'tone.mp3';path.write_bytes(b'fixture')
    errors=[]
    first=_WindowsAlarmAudio(str(path),volume=0,on_finished=lambda:None,on_error=errors.append)
    first._mci=lambda *a:0
    first._send=lambda command:277 if command.startswith('open') else 0
    first._start_worker()
    assert errors==['mci open failed: 277']
    second=_WindowsAlarmAudio(str(path),volume=0,on_finished=lambda:None,on_error=lambda e:None)
    second._mci=lambda *a:0
    assert not second.available
    path.write_bytes(b'changed fixture')
    third=_WindowsAlarmAudio(str(path),volume=0,on_finished=lambda:None,on_error=lambda e:None)
    third._mci=lambda *a:0
    assert third.available
    for b in (wav,second,third):b._mark_closed()


def test_qt_handoff_waits_for_cleanup_and_pending_replacement_is_closed():
    app(); service=AlarmAudioService(); calls=[]
    job=SimpleNamespace(closed=False)
    first=SimpleNamespace(_qt_stop_job=job);second=SimpleNamespace();third=SimpleNamespace()
    service.play(first,"first",lambda:calls.append("first"),lambda:calls.append("stop-first"))
    service.play(second,"second",lambda:calls.append("second"),lambda:calls.append("cancel-second"))
    service.play(third,"third",lambda:calls.append("third"),lambda:calls.append("stop-third"))
    assert calls==["first","stop-first","cancel-second"]
    job.closed=True;service._advance()
    assert calls[-1]=="third" and service.current[0] is third
    assert not service.stop(first)
    service.stop(third)
