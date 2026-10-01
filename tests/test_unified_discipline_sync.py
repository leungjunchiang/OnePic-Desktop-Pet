"""验证统一 TTL、内容高度、06:00 真实开工、纪律增量确认和低频读取。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem
from onepic_desktop_pet.discipline import DisciplineStore, DisciplineEngine, get_actual_work_start
from onepic_desktop_pet.social_ui import BuddyCardWidget, SocialHubDialog, _presence_status
from onepic_desktop_pet.social import SupabaseFirstSocialClient, HttpSocialBackend, SocialSession


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def at(hour, minute=0):
    return datetime(2026, 9, 30, hour, minute).astimezone()


def test_actual_start_uses_first_real_event_after_six_and_is_shared_with_summary():
    store = DisciplineStore("a", persist=False)
    engine = DisciplineEngine(store)
    engine.record_work_event("start_work", 0, 0, at(2))
    engine.record_work_event("start_work", 0, 0, at(7, 30), metadata={"source": "restore"})
    assert get_actual_work_start(store.events, at(12).date()) is None
    engine.record_work_event("start_work", 0, 0, at(7, 42))
    engine.record_work_event("start_work", 3600, 3600, at(9, 35))
    assert get_actual_work_start(store.events, at(12).date()) == at(7, 42)
    assert engine.daily_summary(at(12).date(), 3600, 3600)["actual_start"] == "07:42"
    assert len([row for row in store.events if row["event_type"] == "start_work"]) == 1


def test_actual_start_uses_beijing_even_when_legacy_caller_requests_host_timezone():
    zone = timezone(timedelta(hours=-7))
    day = at(12).date()
    rows = [{"event_type": "start_work", "occurred_at": "2026-09-30T12:59:00+00:00"},
            {"event_type": "start_work", "occurred_at": "2026-09-30T14:30:00+00:00"}]
    assert get_actual_work_start(rows, day, tz=zone).strftime("%H:%M") == "20:59"


def test_legacy_two_am_event_does_not_prevent_real_final_lateness():
    store=DisciplineStore("a",persist=False);store.update_settings({"mode":"officer"})
    store.append_event("start_work",at(2))
    engine=DisciplineEngine(store)
    engine.record_work_event("start_work",0,0,at(9,26))
    assert engine.daily_summary(at(12).date(),0,0)["actual_start"]=="09:26"
    assert [row["metadata"]["minutes_late"] for row in store.events if row["event_type"]=="late_start"]==[26]


def test_no_final_lateness_is_written_before_real_start_and_pause_is_local():
    store = DisciplineStore("a", persist=False)
    store.update_settings({"mode": "officer", "late_grace_minutes": 0})
    engine = DisciplineEngine(store)
    for minute in range(60): engine.evaluate(0, 0, at(10, minute))
    assert store.events == []
    engine.record_work_event("start_work", 0, 0, at(11, 26))
    engine.record_work_event("start_break", 0, 0, at(12), metadata={"session_key": "b"})
    engine.record_work_event("end_break", 0, 0, at(12, 5), metadata={"session_key": "b"})
    types = [row["event_type"] for row in store.sync_payload()["p_events"]]
    assert types == ["start_work", "late_start"]
    assert engine.daily_summary(at(12).date(), 0, 0)["lateness_minutes"] == 146


def test_incremental_ack_is_durable_and_late_ack_preserves_new_explanation(tmp_path):
    store = DisciplineStore("a", path=tmp_path / "discipline.json")
    row = store.append_event("late_start", at(10), requires_explanation=True)
    sent = store.sync_payload()
    store.explain(row["id"], "commute")
    store.acknowledge_sync(sent, {"acknowledged_ids": [row["id"]], "next_revision": 7})
    assert len(store.sync_payload()["p_events"]) == 1
    sent = store.sync_payload()
    store.acknowledge_sync(sent, {"acknowledged_ids": [row["id"]], "next_revision": 8})
    reloaded = DisciplineStore("a", path=store.path)
    assert reloaded.sync_payload()["p_events"] == []
    assert reloaded.sync_cursor == 8
    assert reloaded.sync_payload()["p_settings"] is None


def test_remote_merge_never_acknowledges_unsent_new_explanation():
    store = DisciplineStore("a", persist=False)
    row = store.append_event("late_start", at(10), requires_explanation=True)
    store.explain(row["id"], "urgent")
    store.merge_remote({"events": [row]})
    assert store.sync_payload()["p_events"][0]["explanation"]["reason"] == "urgent"


def test_earlier_device_start_corrects_summary_without_overwriting_new_local_events():
    store = DisciplineStore("a", persist=False)
    engine = DisciplineEngine(store)
    engine.record_work_event("start_work", 0, 0, at(10))
    remote = deepcopy(store.events[0]); remote["id"] = "another-device"
    remote["occurred_at"] = at(7, 30).isoformat()
    store.merge_remote({"events": [remote]})
    assert engine.daily_summary(at(12).date(), 0, 0)["actual_start"] == "07:30"
    assert store.sync_payload()["p_events"] == []


@pytest.mark.parametrize("working", [True, False])
def test_ttl_beats_rest_work_and_profile_update(working):
    now = datetime.now(timezone.utc)
    old = {"online": True, "working": working, "last_seen_at": (now - timedelta(days=19)).isoformat(),
           "updated_at": now.isoformat(), "presence_transport_stale": True}
    assert _presence_status(old, now) == "offline"
    old["last_seen_at"] = (now - timedelta(seconds=120)).isoformat()
    old["presence_transport_stale"] = False
    assert _presence_status(old, now) == ("focus" if working else "rest")
    old["last_seen_at"] = (now - timedelta(seconds=121)).isoformat()
    assert _presence_status(old, now) == "offline"
    assert _presence_status({"nickname": "a"}, now) == "unknown"


def test_single_offline_card_does_not_absorb_tall_viewport(app):
    listing = QListWidget(); listing.resize(600, 950); listing.show()
    buddy = {"nickname": "小号", "private_note_name": "搭子", "online": False, "working": False,
             "last_seen_at": (datetime.now(timezone.utc)-timedelta(days=19)).isoformat(), "outfit_key": "hour-03"}
    item = QListWidgetItem(listing); card = BuddyCardWidget(buddy)
    listing.setItemWidget(item, card); app.processEvents()
    SocialHubDialog._set_buddy_item_height(item, card); app.processEvents()
    assert item.sizeHint().height() < 260
    assert card._footer_label.y() - card._confirmation_label.geometry().bottom() < 20
    assert card.study_button.geometry().bottom() < 260
    listing.close(); listing.deleteLater(); app.processEvents()


class ReaderBackend:
    signed_in = True
    session = SimpleNamespace(user_id="reader-a")
    backend_name = "Supabase Direct"
    backend_endpoint = "https://example.test"
    def __init__(self):
        self.calls = []
        self.active = self
    def request(self, method, name, body):
        self.calls.append(name)
        if name == "lili_buddy_reminder_snapshot": return {"subscriptions": [{"buddy_id":"b","on_focus_start":True}], "events": []}
        if name == "lili_buddy_reminder_events": return {"events": [{"id":"e"}]}
        return {"devices": []}


def test_notification_poll_does_not_reread_subscriptions_and_account_cache_isolated(monkeypatch):
    backend = ReaderBackend()
    client = SupabaseFirstSocialClient(persist_tokens=False, backend=backend)
    ticks = [100.0]; monkeypatch.setattr("onepic_desktop_pet.social.time.monotonic", lambda: ticks[0])
    client.buddy_reminder_snapshot(); client.buddy_reminder_snapshot()
    assert backend.calls == ["lili_buddy_reminder_snapshot"]
    ticks[0] += 61
    result = client.buddy_reminder_snapshot()
    assert result["subscriptions"][0]["on_focus_start"]
    assert backend.calls[-1] == "lili_buddy_reminder_events"
    backend.session.user_id = "reader-b"
    client.buddy_reminder_snapshot()
    assert backend.calls[-1] == "lili_buddy_reminder_snapshot"
    client.focus_live_projection(); client.focus_live_projection()
    assert backend.calls.count("lili_focus_live_projection") == 1


def test_bounded_queue_sends_results_only_and_never_clears_on_missing_ack():
    store = DisciplineStore("a", persist=False)
    for number in range(150): store.append_event("long_break", at(12), metadata={"number":number})
    sent = store.sync_payload()
    assert len(sent["p_events"]) == 100
    store.acknowledge_sync(sent, {"acknowledged_ids": []})
    assert store.sync_payload()["p_events"] == sent["p_events"]
    store.acknowledge_sync(sent, {"acknowledged_ids": [row["id"] for row in sent["p_events"]]})
    assert len(store.sync_payload()["p_events"]) == 50


def test_report_and_records_share_actual_start_without_changing_natural_day_totals(tmp_path):
    from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
    from onepic_desktop_pet.work_timer import WorkTimerModel
    from onepic_desktop_pet.diary import DailyCompanionStats
    from onepic_desktop_pet.work_report import build_work_report
    analytics = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: at(12), persist=False)
    analytics.record_session(3600, started_at=at(2), completed=True)
    analytics.record_session(1800, started_at=at(7, 42), completed=True)
    timer = WorkTimerModel(path=tmp_path / "timer.json", now_provider=lambda: at(12), persist=False)
    diary = DailyCompanionStats(path=tmp_path / "diary.json", now_provider=lambda: at(12), persist=False)
    store = DisciplineStore("a", persist=False); engine = DisciplineEngine(store)
    engine.record_work_event("start_work", 0, 0, at(7, 42))
    report = build_work_report(analytics, timer, diary, work_events=store.events, now=at(12))
    assert report["day"]["total_seconds"] == 5400  # 02:00 duration remains a natural-day fact
    assert report["day"]["actual_work_start"] == engine.daily_summary(at(12).date(),5400,5400)["actual_start"] == "07:42"
    assert build_work_report(analytics,timer,diary,now=at(12))["day"]["actual_work_start"] == "07:42"


def test_record_tabs_are_equal_and_history_groups_events_by_day(app):
    from onepic_desktop_pet.discipline_ui import DisciplineWorkspace
    store=DisciplineStore("a",persist=False);store.update_settings({"mode":"officer"})
    engine=DisciplineEngine(store)
    engine.record_work_event("start_work",0,0,at(10))
    engine.record_work_event("finish_work",3600,3600,at(18))
    panel=DisciplineWorkspace(store,engine,lambda:(3600,3600));panel.resize(960,750);panel.show()
    panel.tabs.setCurrentWidget(panel.records);app.processEvents()
    bar=panel.records.tabBar()
    assert max(bar.tabRect(i).width() for i in range(3))-min(bar.tabRect(i).width() for i in range(3))<=1
    assert 46<=bar.height()<=52
    assert len(panel.discipline_metrics)==4
    panel.records.setCurrentIndex(2);app.processEvents()
    assert panel.ledger.rowCount()==1  # start, lateness, gap and settlement are one date
    panel.ledger.selectRow(0);app.processEvents()
    assert panel.history_events.count()>0
    assert "实际开工" in panel.history_detail.text()
    panel.close();panel.deleteLater();app.processEvents()


@pytest.mark.parametrize("buddy_count",[1,10])
def test_real_dashboard_path_has_constant_round_trips_and_no_per_card_detail_load(buddy_count):
    class Direct(HttpSocialBackend):
        def __init__(self):
            super().__init__("https://example.test",client_key="test",persist_tokens=False,transport="direct")
            self.session=SocialSession("a","r","u",9_999_999_999);self.calls=[]
        def _raw(self,method,path,body=None,**kwargs):
            self.calls.append(path.rsplit("/",1)[-1])
            if path.endswith("lili_dashboard"):
                return {"me":{"user_id":"u"},"buddies":[{"user_id":str(i),"online":True,"working":False} for i in range(buddy_count)],"rooms":[],"requests":[],"room_people":[]}
            if path.endswith("lili_buddy_requests"): return {"incoming":[],"outgoing":[]}
            if path.endswith("lili_buddy_reminder_snapshot"): return {"subscriptions":[],"events":[]}
            return {}
    direct=Direct()
    class Manager:
        signed_in=True;backend_name="Supabase Direct";backend_endpoint="https://example.test";active=direct
        def request(self,method,*args,**kwargs): return getattr(direct,method)(*args,**kwargs)
    client=SupabaseFirstSocialClient(persist_tokens=False,backend=Manager())
    result=client.dashboard();client.buddy_reminder_snapshot()
    assert len(result["buddies"])==buddy_count
    assert direct.calls==["lili_dashboard","lili_buddy_requests","lili_buddy_private_notes","lili_buddy_reminder_snapshot"]
    client.dashboard();client.buddy_reminder_snapshot()
    assert len(direct.calls)==4  # concurrent windows' ordinary refreshes reuse account cache


def test_later_finish_updates_settlement_and_resolves_stale_explanations():
    store=DisciplineStore("a",persist=False)
    store.update_settings({"mode":"officer","weekly_target_minutes":600,"workdays":["wed"],"planned_finish_enabled":True,"finish_time":"18:00"})
    engine=DisciplineEngine(store)
    engine.record_work_event("start_work",0,0,at(9))
    engine.record_work_event("finish_work",3600,3600,at(12))
    first=[row for row in store.events if row["event_type"] in {"focus_shortfall","early_finish","weekly_shortfall"}]
    assert len(first)==3 and all(row["requires_explanation"] for row in first)
    engine.record_work_event("start_work",3600,3600,at(13))
    engine.record_work_event("finish_work",36000,36000,at(19))
    final=[row for row in store.events if row["event_type"] in {"focus_shortfall","early_finish","weekly_shortfall"}]
    assert len(final)==3 and all(not row["requires_explanation"] for row in final)
    assert store.pending_explanations==[]
    assert next(row for row in final if row["event_type"]=="focus_shortfall")["metadata"]["gap_seconds"]==0
    assert {row["id"] for row in first}=={row["id"] for row in final}
