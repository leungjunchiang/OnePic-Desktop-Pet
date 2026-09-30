"""验证矢量勾选的真实渲染/状态绑定，以及缓存 FocusSession 历史开工与计划基准。"""

from datetime import datetime, timedelta, timezone
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory, QListWidgetItem
from onepic_desktop_pet.check_controls import AppCheckBox, CheckListWidget
from onepic_desktop_pet.discipline import DisciplineStore, DisciplineEngine, get_actual_work_start
from onepic_desktop_pet.focus_segments import FocusSegment


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def at(hour, minute=0, day=30):
    return datetime(2026,9,day,hour,minute).astimezone()


def fact(hour, minute=0, *, day=30, session=None, device="pc1", seconds=600):
    start=at(hour,minute,day)
    return FocusSegment(f"fact:{day}:{hour}:{minute}",session or f"s:{day}:{hour}:{minute}",start,start+timedelta(seconds=seconds),device)


@pytest.mark.parametrize("theme",["Fusion","Windows"])
def test_checkbox_paints_all_states_and_preserves_label_keyboard_boolean(app,theme):
    style=QStyleFactory.create(theme)
    if style is None: pytest.skip("Native Windows style unavailable on this runner")
    check=AppCheckBox("权限");check.setStyle(style);check.resize(180,40);check.show();app.processEvents()
    for enabled, checked, fill in [(True,False,"#ffffff"),(True,True,"#087f91"),(False,False,"#edf1f2"),(False,True,"#66858d")]:
        check.setEnabled(enabled);check.setChecked(checked);app.processEvents()
        if enabled and check.underMouse(): fill = "#056777" if checked else "#e0f1f3"
        shot=check.grab();image=shot.toImage();dpr=shot.devicePixelRatio()
        assert image.pixelColor(round(4*dpr),round(16*dpr)).name()==fill
        assert check.isChecked()==checked
    check.setEnabled(True);check.setChecked(False)
    QTest.mouseClick(check,Qt.MouseButton.LeftButton,pos=QPoint(65,20))
    assert check.isChecked()  # text is part of the click target
    check.setFocus();QTest.keyClick(check,Qt.Key.Key_Space);assert not check.isChecked()
    check.close()


def test_list_check_is_visible_when_selected_and_disabled_and_model_changes(app):
    listing=CheckListWidget();listing.resize(420,110)
    item=QListWidgetItem("指定搭子",listing);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Checked);listing.setCurrentItem(item);listing.show();app.processEvents()
    image=listing.viewport().grab().toImage()
    colors=[image.pixelColor(x,y).name() for x in range(8,40) for y in range(5,35)]
    assert "#087f91" in colors and "#ffffff" in colors
    listing.setEnabled(False);app.processEvents();image=listing.viewport().grab().toImage()
    assert any(image.pixelColor(x,y).name()=="#66858d" for x in range(8,40) for y in range(5,35))
    assert item.checkState()==Qt.CheckState.Checked
    listing.setEnabled(True);rect=listing.visualItemRect(item)
    QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.left()+10,rect.center().y()))
    assert item.checkState()==Qt.CheckState.Unchecked
    listing.close()


def test_sessions_override_wrong_legacy_start_and_keep_true_checkpoint_start():
    events=[{"event_type":"start_work","occurred_at":at(16,26).isoformat(),"metadata":{"actual_start":"16:26"}}]
    sessions=[fact(0,18),fact(8,52,session="s"),fact(9,7,session="s"),fact(9,14)]
    assert get_actual_work_start(events,at(20).date(),sessions=sessions,now=at(20)).strftime("%H:%M")=="08:52"


def test_before_six_session_checkpoint_and_midnight_tail_are_not_new_starts():
    sessions=[fact(5,40,session="early",seconds=1800),fact(6,10,session="early"),
              fact(23,50,day=29,session="overnight"),fact(6,15,session="overnight"),fact(8,12)]
    assert get_actual_work_start([],at(20).date(),sessions=sessions,now=at(20)).strftime("%H:%M")=="08:12"
    assert get_actual_work_start([],at(20).date(),sessions=sessions[:-1],now=at(20)) is None


def test_same_session_id_from_two_devices_does_not_hide_real_start():
    sessions=[fact(5,40,session="s",device="pc1"),fact(8,52,session="s",device="pc2")]
    assert get_actual_work_start([],at(20).date(),sessions=sessions,now=at(20)).strftime("%H:%M")=="08:52"


def test_presence_restore_and_future_rows_cannot_infer_start():
    sessions=[FocusSegment("display-live-device:pc2","s",at(8),None,"pc2"),
              {"start_at":at(7).isoformat(),"end_at":at(8).isoformat(),"source":"heartbeat"},fact(21)]
    assert get_actual_work_start([],at(20).date(),sessions=sessions,now=at(20)) is None


def test_local_timezone_is_applied_to_raw_start_before_six_filter():
    zone=timezone(timedelta(hours=-7));start=datetime(2026,9,30,15,52,tzinfo=timezone.utc)
    segment=FocusSegment("f","s",start,start+timedelta(minutes=10))
    assert get_actual_work_start([],start.date(),tz=zone,sessions=[segment],now=start+timedelta(hours=1)).strftime("%H:%M")=="08:52"


def test_historical_start_without_plan_does_not_use_current_plan_or_write_back():
    store=DisciplineStore("a",persist=False);store.update_settings({"start_time":"11:00"})
    engine=DisciplineEngine(store,focus_sessions_provider=lambda:[fact(9,37,day=20)],now_provider=lambda:at(20))
    summary=engine.daily_summary(at(12,day=20).date(),600,600)
    assert summary["actual_start"]=="09:37" and summary["lateness_minutes"] is None
    assert not summary["historical_plan_known"] and store.events==[]


def test_historical_plan_snapshot_recomputes_lateness_and_exemption_suppresses_it():
    store=DisciplineStore("a",persist=False);store.update_settings({"start_time":"11:00"})
    store.append_event("daily_report",at(18,day=20),metadata={"planned_start":"09:00"})
    engine=DisciplineEngine(store,focus_sessions_provider=lambda:[fact(9,37,day=20)],now_provider=lambda:at(20))
    summary=engine.daily_summary(at(12,day=20).date(),600,600)
    assert summary["actual_start"]=="09:37" and summary["lateness_minutes"]==37
    store.rest_days.add("2026-09-20")
    assert engine.daily_summary(at(12,day=20).date(),600,600)["lateness_minutes"]==0


def test_history_can_discover_session_only_dates_from_cache_without_ledger_writes(app):
    from onepic_desktop_pet.discipline_ui import DisciplineWorkspace
    store=DisciplineStore("a",persist=False)
    engine=DisciplineEngine(store,focus_sessions_provider=lambda:[fact(7,26,day=20)],now_provider=lambda:at(20))
    panel=DisciplineWorkspace(store,engine,lambda:(0,0));panel.show();app.processEvents()
    assert panel.ledger.rowCount()==0
    panel.tabs.setCurrentWidget(panel.records);panel.records.setCurrentIndex(2);app.processEvents()
    assert panel.ledger.rowCount()==1 and "07:26" in panel.ledger.item(0,1).text()
    panel.ledger.selectRow(0);app.processEvents()
    assert "缺少历史计划" in panel.history_detail.text() and "07:26" in panel.history_detail.text()
    assert store.events==[]
    panel.close()


def test_policy_boolean_and_list_selection_match_backend_and_are_readable(app):
    from onepic_desktop_pet.supervision_ui import SupervisionPolicyWidget
    store=DisciplineStore("a",persist=False);engine=DisciplineEngine(store)
    policy={"policy":{"enabled":True,"revision":1,"scope":"selected","selected_ids":["b"],"officer_scope":"selected","officer_ids":["b"]},"effective_mode":"off"}
    panel=SupervisionPolicyWidget(lambda:engine,lambda:[{"user_id":"b","nickname":"搭子"}],lambda n,b,ok,fail:ok(policy),lambda *args:None)
    panel.show();app.processEvents()
    assert panel.enabled.isChecked() and panel.selected.item(0).checkState()==Qt.CheckState.Checked
    assert panel.officers.item(0).checkState()==Qt.CheckState.Checked
    assert panel._form_policy()["selected_ids"]==["b"]
    assert not panel.dirty
    panel.close()


def test_report_and_every_summary_use_same_cached_facts_despite_legacy_1626(tmp_path):
    from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
    from onepic_desktop_pet.work_timer import WorkTimerModel
    from onepic_desktop_pet.diary import DailyCompanionStats
    from onepic_desktop_pet.work_report import build_work_report
    analytics=FocusAnalyticsStore(path=tmp_path/"facts.json",now_provider=lambda:at(20),persist=False)
    for hour,minute in [(0,18),(8,52),(9,14)]: analytics.record_session(600,started_at=at(hour,minute),completed=True)
    store=DisciplineStore("a",persist=False);store.append_event("start_work",at(16,26))
    store.append_event("late_start",at(16,26),metadata={"minutes_late":446,"planned_start":at(9).isoformat()},requires_explanation=True)
    engine=DisciplineEngine(store,focus_sessions_provider=lambda:analytics.range_segments(at(0),at(20)),now_provider=lambda:at(20))
    timer=WorkTimerModel(path=tmp_path/"timer.json",now_provider=lambda:at(20),persist=False)
    diary=DailyCompanionStats(path=tmp_path/"diary.json",now_provider=lambda:at(20),persist=False)
    report=build_work_report(analytics,timer,diary,work_events=store.events,now=at(20))
    summary=engine.daily_summary(at(20).date(),1800,1800)
    assert report["day"]["actual_work_start"]==summary["actual_start"]=="08:52"
    assert summary["lateness_minutes"]==0 and summary["unexplained_count"]==0
    assert not [row for row in summary["events"] if row["event_type"]=="late_start"]
    assert store.events[0]["occurred_at"]==at(16,26).isoformat()  # lazy projection, no old-row rewrite
