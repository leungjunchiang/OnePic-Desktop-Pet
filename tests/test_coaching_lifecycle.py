"""验证训导牌的页面归属、紧凑胶囊、被动显示门禁及双向训导版本边界。"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from onepic_desktop_pet.coaching import projection, remaining_seconds, closed_case_lines, completion_feedback
from onepic_desktop_pet.coaching_ui import CoachingPanel, DesktopCoachingSurface
from onepic_desktop_pet.discipline import DisciplineEngine, DisciplineStore, BEIJING_TIMEZONE
from onepic_desktop_pet.focus_segments import FocusSegment
from onepic_desktop_pet.social_ui import SocialHubDialog
from onepic_desktop_pet.buddy_study_ui import BuddyStudyDialog

NOW = datetime(2026, 10, 1, 10, 30, tzinfo=BEIJING_TIMEZONE)

def case(state="pending", revision=1, **extra):
    return {"id": "case-1", "owner_id": "owner", "supervisor_id": "coach", "source_event_id": "event-1",
            "kind": "late_start", "title": "迟到 47 分钟", "detail": "计划 09:00 · 实际 09:47",
            "state": state, "revision": revision, "required_seconds": 1800,
            "event_date": NOW.date().isoformat(), "accepted_at": (NOW-timedelta(minutes=30)).isoformat(), **extra}

def engine(rows=()):
    value = DisciplineEngine(DisciplineStore("owner", persist=False), focus_sessions_provider=lambda: list(rows), now_provider=lambda: NOW)
    value.apply_supervision({"policy": {"enabled": True, "revision": 1}, "effective_mode": "officer"})
    return value

@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])

def dispose(qt, *widgets):
    for widget in widgets: widget.close(); widget.deleteLater()
    qt.processEvents()

def test_makeup_uses_clipped_union_and_pause_not_wall_clock():
    rows = [FocusSegment("a", "a", NOW-timedelta(minutes=30), NOW-timedelta(minutes=18), "pc1"),
            FocusSegment("b", "b", NOW-timedelta(minutes=29), NOW-timedelta(minutes=18), "pc2")]
    value = engine(rows)
    assert remaining_seconds(value, case("active")) == 18*60
    assert remaining_seconds(value, case("active"), NOW+timedelta(hours=1)) == 18*60

def test_makeup_counts_live_work_and_midnight_without_daily_reset():
    value = engine([FocusSegment("a", "a", NOW-timedelta(minutes=12), None, "pc1")])
    assert remaining_seconds(value, case("active")) == 18*60
    assert remaining_seconds(value, case("active", accepted_at=(NOW+timedelta(days=1)).isoformat())) == 1800
    value.focus_sessions_provider = lambda: [FocusSegment("cross", "cross", NOW-timedelta(hours=12), NOW-timedelta(hours=10), "pc1")]
    assert remaining_seconds(value, case("active", required_seconds=7200, accepted_at=(NOW-timedelta(hours=11)).isoformat())) == 3600

@pytest.mark.parametrize("stamp", [None, "invalid", "2026-10-01T09:00:00"])
def test_missing_acceptance_cannot_claim_old_work(stamp):
    assert remaining_seconds(engine(), case("active", accepted_at=stamp)) == 1800

def test_server_versions_and_accounts_are_authoritative(tmp_path):
    store = DisciplineStore("owner", path=tmp_path/"ledger.json")
    store.merge_coaching({"coaching_cases": [case("explained", 2)], "next_case_revision": 2})
    store.merge_coaching({"case": case("pending", 1)})
    store.merge_coaching({"case": case("forgiven", 9, owner_id="other")})
    assert store.coaching_cases[0]["state"] == "explained"
    assert DisciplineStore("owner", path=store.path).coaching_cursor == 2
    assert "coaching_cases" not in store.sync_payload()

def test_old_json_and_legacy_explanation_cannot_bypass_formal_review(tmp_path):
    path = tmp_path/"old.json"; path.write_text('{"schema_version":2}', encoding="utf-8")
    store = DisciplineStore("owner", path=path)
    assert not store.coaching_cases and store.coaching_cursor == 0
    store.events = [{"id": "event-1", "event_date": NOW.date().isoformat(), "requires_explanation": True}]
    store.pending_explanations = [{"event_id": "event-1"}]
    store.merge_coaching({"case": case()})
    assert not store.explain("event-1", "临时开会") and not store.due_explanations()


def test_cloud_source_alias_keeps_existing_local_ledger_identity():
    value = engine()
    value.store.events = [{"id": "local-event", "event_type": "late_start",
                           "event_date": NOW.date().isoformat(),
                           "metadata": {"rule_key": "day:late_start", "minutes_late": 47},
                           "requires_explanation": True}]
    value.store.pending_explanations = [{"event_id": "local-event"}]
    value.store.merge_coaching({"case": case(source_rule_key="day:late_start")})
    assert value.store.coaching_cases[0]["local_source_event_id"] == "local-event"
    assert not value.store.explain("local-event", "不能绕过搭子审核")
    assert not value.store.due_explanations()
    assert projection(value)["badge"] is None

def test_projection_one_card_one_badge_no_same_case_duplicate():
    value = engine(); value.store.coaching_cases = [case(), case("rejected", id="case-2"), case("active", id="case-3")]
    view = projection(value)
    assert view["card"]["id"] == "case-1" and view["card_count"] == 2
    assert view["badge"]["id"] == "case-3" and view["badge_count"] == 1
    value.store.coaching_cases[0]["state"] = "explained"
    assert projection(value)["badge_count"] == 2

@pytest.mark.parametrize("reason", ["normal", "expired", "paused", "exempt"])
def test_permission_and_exemption_hide_formal_controls(reason):
    value = engine(); value.store.coaching_cases = [case()]
    if reason == "normal": value._remote_mode = "normal"
    elif reason == "expired": value._remote_until = 0
    elif reason == "paused": value.store.coaching_cases[0]["paused"] = True
    else: value.store.rest_days.add(NOW.date().isoformat())
    assert projection(value)["card"] is None

def test_yesterday_observation_is_60_minutes_not_the_real_gap():
    value = engine(); value.store.events = [{"event_type": "focus_shortfall", "event_date": (NOW-timedelta(days=1)).date().isoformat(), "metadata": {"gap_seconds": 7200}}]
    assert "38分钟" in projection(value, 22*60)["badge"]["badge_text"]
    assert projection(value, 3600)["badge"] is None
    assert value.store.events[0]["metadata"]["gap_seconds"] == 7200
    assert not value.store.coaching_cases


def test_formal_yesterday_debt_never_duplicates_the_observation_badge():
    value = engine()
    value.store.events = [{"id": "event-1", "event_type": "focus_shortfall",
                           "event_date": (NOW-timedelta(days=1)).date().isoformat(),
                           "metadata": {"gap_seconds": 7200}}]
    value.store.coaching_cases = [case(kind="focus_shortfall")]
    assert projection(value)["card"] is not None
    assert projection(value)["badge"] is None


def test_corrected_lateness_does_not_leave_an_observation():
    value = engine()
    value.store.events = [{"id": "event-1", "event_type": "late_start",
                           "event_date": NOW.date().isoformat(), "metadata": {"minutes_late": 0}}]
    assert projection(value)["badge"] is None


def test_completion_feedback_distinguishes_approval_makeup_and_forgiveness():
    assert completion_feedback(case("completed")) == "✓ 补时完成"
    assert completion_feedback(case("completed", required_seconds=0), "论文搭子") == "✓ 论文搭子 接受了你的说明"
    assert "放过" in completion_feedback(case("forgiven"))

def make_panel(qt, value, callbacks):
    def rpc(name, body, done, fail): callbacks.append((name, body, done, fail))
    host = QWidget()
    panel = CoachingPanel(lambda: value, lambda: (0, 0), rpc, lambda _: "论文搭子", lambda: None, host)
    # 真实页面中的进度牌属于页面而非可折叠的训导卡，测试同样保留这一层级。
    panel._test_host = host
    panel.destroyed.connect(host.deleteLater)
    host.show(); panel.show(); qt.processEvents(); return panel

def test_owner_responds_inline_then_card_collapses_to_review_badge(qt):
    value = engine(); value.store.coaching_cases = [case(required_seconds=0)]
    calls = []; panel = make_panel(qt, value, calls)
    assert panel.card.isVisible() and not panel.accept.isVisible()
    assert not any(button.text() in {"放过", "退回说明", "要求说明"} for button in panel.findChildren(QPushButton))
    panel.explain.click(); panel.input.setText("上午临时开会。")
    panel.submit.click(); panel.submit.click()
    assert len(calls) == 1 and not panel.submit.isEnabled()
    assert calls[0][1]["p_expected_revision"] == 1
    calls[0][2]({"case": case("explained", 2, required_seconds=0, explanation="上午临时开会。"), "message": "说明已提交"})
    panel._clear_flash()
    assert not panel.card.isVisible() and panel.badge.isVisible() and "说明待处理" in panel.badge.text()
    for _ in range(5): panel.refresh()
    assert len(calls) == 1  # Ordinary rendering never polls or writes.
    dispose(qt, panel.badge, panel)

def test_failed_submission_restores_current_button_and_keeps_draft(qt):
    value = engine(); value.store.coaching_cases = [case()]
    calls = []; panel = make_panel(qt, value, calls)
    panel.explain.click(); panel.input.setText("我的说明")
    panel.submit.click(); calls[0][3]("网络暂时不可用")
    assert panel.submit.isEnabled() and panel.input.text() == "我的说明"
    assert not panel._busy
    dispose(qt, panel.badge, panel)

def test_accepted_makeup_and_rejection_have_different_surfaces(qt):
    value = engine(); value.store.coaching_cases = [case()]
    calls = []; panel = make_panel(qt, value, calls)
    panel.accept.click(); calls[0][2]({"case": case("active", 2), "message": "补时已开始"})
    panel._clear_flash(); assert not panel.card.isVisible() and "补时中" in panel.badge.text()
    value.store.merge_coaching({"case": case("rejected", 3, review_note="再说清楚一点")})
    panel.refresh(); assert panel.card.isVisible() and panel.explain.text() == "重新说明"
    assert "再说清楚一点" in panel.summary.text()
    dispose(qt, panel.badge, panel)

def test_startup_closed_cases_do_not_flash_and_forgiven_is_not_makeup_complete(qt):
    value = engine(); value.store.coaching_cases = [case("forgiven", 2)]
    panel = make_panel(qt, value, [])
    assert not panel._notice_active and not panel.badge.isVisible()
    text = closed_case_lines(value.store, NOW.date())[0]
    assert "已放过" in text and "补时" not in text
    dispose(qt, panel.badge, panel)

def test_desktop_surface_does_not_activate_window(qt):
    calls = []; surface = DesktopCoachingSurface(None, lambda: calls.append(True))
    assert surface.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert surface.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    surface.passive_show(); qt.processEvents(); assert not calls
    surface.open_button.click(); assert calls == [True]
    dispose(qt, surface)


def test_compact_badge_matches_clock_height_and_has_no_rectangular_backfill(qt):
    from onepic_desktop_pet.controls import WorkDurationBubble
    clock = WorkDurationBubble()
    clock.set_session("rest", 8044, True)
    surface = DesktopCoachingSurface(None, lambda: None, compact=True)
    surface.label.setText("⚠ 今天迟到了 · 再认真一会")
    surface.prepare_compact(clock.height())
    surface.passive_show(); qt.processEvents()
    assert surface.height() == clock.height()
    assert surface.layout().contentsMargins().left() == 0
    assert surface.layout().contentsMargins().top() == 0
    image = surface.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(5, image.height() // 2).name() == "#f6fbfb"
    assert surface.open_button.toolTip() == surface.label.text()
    assert surface.open_button.text().startswith("今天迟到了")
    width = surface.width()
    surface.label.setText("✓ 已完成")
    surface.prepare_compact(clock.height())
    assert surface.width() < width
    assert surface.height() == clock.height()
    dispose(qt, surface, clock)


def test_visible_badge_refresh_does_not_reopen_a_native_window(qt):
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    shows = []
    parent._show_nonactivating = lambda widget, **kwargs: (shows.append(widget), widget.show())
    surface = DesktopCoachingSurface(parent, lambda: None, compact=True)
    surface.label.setText("⚠ 今天迟到了 · 再认真一会")
    surface.passive_show(); qt.processEvents()
    surface.passive_show(); surface.passive_show()
    assert shows == [surface]
    assert surface.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    surface.hide(); surface.passive_show()
    assert len(shows) == 2
    dispose(qt, surface, parent)

def test_supervisor_controls_only_appear_in_strict_mode_and_follow_case_state(qt):
    client = SimpleNamespace(signed_in=False, session=SimpleNamespace(user_id="coach"), backend_name="Supabase Direct", backend_endpoint="https://example.invalid")
    hub = SocialHubDialog(client); room = BuddyStudyDialog(hub, {"user_id": "owner"})
    hub.show(); room.show(); room.tabs.setCurrentIndex(3); qt.processEvents()
    overview = {"peer_permission": {"eligible": True, "officer": True}, "active_mode": "officer", "coaching_cases": [case("explained")]}
    room._apply_overview(overview)
    assert room.case_box.isVisible()
    assert room.case_actions["approve"].isVisible() and room.case_actions["reject"].isVisible()
    assert not room.case_actions["request_explanation"].isVisible()
    room._apply_overview({**overview, "active_mode": "normal"})
    assert not room.case_box.isVisible()
    assert len(room.feed.menu().actions()) == 4
    dispose(qt, room, hub)
