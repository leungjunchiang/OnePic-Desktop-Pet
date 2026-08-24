"""验证搭子自习室四标签布局和未登录交互反馈。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTabWidget, QSizePolicy

from onepic_desktop_pet.social import SignupResult, SocialError
from onepic_desktop_pet.social_ui import (
    BuddyCardWidget,
    BuddyProfileDialog,
    BuddyVisitWindow,
    IncomingVisitNotice,
    SocialHubDialog,
    SocialSignupThread,
    SocialVisitResponseThread,
)


class SignedOutClient:
    signed_in = False


class SignedInClient:
    signed_in = True

    def dashboard(self):
        return {
            "me": {
                "nickname": "六毛搭子",
                "invite_code": "AB12CD34",
                "visibility": "friends",
                "show_exact_time": True,
                "allow_visits": True,
            },
            "buddies": [],
            "room_people": [],
            "requests": [],
            "visits": [],
            "rooms": [{"name": "安静工作间", "members": 1, "invite_code": "ROOM1234"}],
            "active_visits": [],
        }


class OfflineCachedClient(SignedInClient):
    def dashboard(self):
        data = super().dashboard()
        data.update(
            {
                "rooms": [],
                "room_people": [],
                "_connection_state": "OFFLINE",
                "_sync_offline": True,
                "_sync_age_minutes": 1,
            }
        )
        return data


class RoomClient(SignedInClient):
    def dashboard(self):
        data = super().dashboard()
        data["me"]["today_seconds"] = 1860
        data["room_people"] = [
            {"user_id": "buddy-1", "nickname": "胡老师", "online": True, "working": True, "today_seconds": 900},
        ]
        data["room_goal"] = {"target_seconds": 4800, "completed_seconds": 2100}
        data["room_activity"] = ["16:32 胡老师开始专注", "16:49 六毛送来一杯奶茶"]
        return data


class PrivateNoteRoomClient(RoomClient):
    def dashboard(self):
        data = super().dashboard()
        data["buddies"] = [
            {
                "user_id": "buddy-1",
                "private_note_name": "论文搭子",
                "nickname": "公开昵称",
                "online": True,
                "working": False,
                "status": "rest",
                "today_seconds": 120,
            }
        ]
        return data


class UncertainRoomClient(RoomClient):
    def dashboard(self):
        data = super().dashboard()
        data["rooms"] = [{"id": "room-1", "name": "安静工作间", "members": 2, "invite_code": "ROOM1234"}]
        data["room_people"] = [
            {
                "user_id": "buddy-1",
                "nickname": "胡老师",
                "owner_nickname": "胡老师",
                "online": True,
                "working": True,
                "status": "focus",
                "presence_uncertain": True,
                "presence_age_seconds": 90,
                "today_seconds": 900,
            },
        ]
        data["_connection_state"] = "DEGRADED"
        data["_sync_offline"] = True
        data["_presence_grace_active"] = True
        return data


def test_social_hub_has_four_function_pages_and_compact_auth_tabs() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedOutClient())

    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "首页", "互动", "专注", "我的"
    ]
    assert dialog.tabs.tabBar().expanding() is True
    assert dialog.tabs.tabBar().sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    dialog.show()
    app.processEvents()
    dialog.resize(1200, 760)
    app.processEvents()
    wide_widths = [dialog.tabs.tabBar().tabRect(index).width() for index in range(4)]
    assert len(set(wide_widths)) == 1
    assert wide_widths[0] > 120
    assert dialog.tabs.tabBar().width() >= dialog.tabs.width() - 2
    for current_index in range(4):
        dialog.tabs.setCurrentIndex(current_index)
        app.processEvents()
        switched_widths = [dialog.tabs.tabBar().tabRect(index).width() for index in range(4)]
        assert len(set(switched_widths)) == 1
    dialog.resize(520, 480)
    app.processEvents()
    narrow_widths = [dialog.tabs.tabBar().tabRect(index).width() for index in range(4)]
    assert len(set(narrow_widths)) == 1
    assert 0 < narrow_widths[0] < wide_widths[0]
    assert dialog.tabs.tabBar().width() >= dialog.tabs.width() - 2
    for current_index in range(4):
        dialog.tabs.setCurrentIndex(current_index)
        app.processEvents()
        switched_widths = [dialog.tabs.tabBar().tabRect(index).width() for index in range(4)]
        assert len(set(switched_widths)) == 1
    auth_tabs = [tab for tab in dialog.findChildren(QTabWidget) if tab is not dialog.tabs]
    assert any(
        [tab.tabText(index) for index in range(tab.count())] == ["登录", "注册"]
        for tab in auth_tabs
    )
    assert "登录后" in dialog.buddies.item(0).text()

    dialog.refresh()
    app.processEvents()
    assert dialog.tabs.currentIndex() == 3
    assert "请先" in dialog.status_label.text()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_private_buddy_note_is_preferred_in_buddy_card_and_list_has_context_menu() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(PrivateNoteRoomClient())
    dialog.refresh()
    app.processEvents()

    assert dialog.buddies.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    item = dialog.buddies.item(0)
    widget = dialog.buddies.itemWidget(item)
    assert widget is not None
    assert any("论文搭子家的六毛" in label.text() for label in widget.findChildren(QLabel))
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_outgoing_buddy_request_has_retract_action_and_is_not_acceptable() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())
    dialog.apply_dashboard({
        "me": {"nickname": "六毛搭子", "visibility": "friends", "show_exact_time": True},
        "buddies": [], "room_people": [], "requests": [],
        "outgoing_requests": [{"id": "request-1", "nickname": "胡老师", "owner_nickname": "胡老师"}],
        "visits": [],
    })
    dialog.inbox.setCurrentRow(0)
    assert dialog.inbox.item(0).data(Qt.ItemDataRole.UserRole)[0] == "buddy_outgoing"
    assert not dialog.inbox_cancel_button.isHidden()
    assert dialog.inbox_accept_button.isHidden()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_buddy_lookup_with_existing_pending_request_does_not_claim_to_send_again() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())

    dialog._buddy_lookup_completed(
        "5BCF1D45",
        {"state": "pending", "owner_nickname": "胡老师"},
    )
    app.processEvents()

    assert "本次没有重复发送" in dialog.status_label.text()
    assert "查找完成" in dialog.status_label.text()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_buddy_profile_dialog_requires_explicit_submit_or_return_choice() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = BuddyProfileDialog("找到：胡老师家的六毛\n昵称：胡老师")

    assert dialog.windowTitle() == "搭子资料确认"
    assert dialog.submit_button.text() == "提交搭子申请"
    assert dialog.return_button.text() == "返回"
    assert "提交搭子申请" in " ".join(label.text() for label in dialog.findChildren(QLabel))

    dialog.return_button.click()
    assert dialog.result() == 0
    dialog.deleteLater(); app.processEvents()


def test_hidden_buddy_remains_visible_as_offline_and_online_buddies_are_sorted() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())
    dialog.apply_dashboard({
        "me": {"nickname": "六毛搭子", "visibility": "friends", "show_exact_time": True},
        "buddies": [
            {"user_id": "offline", "nickname": "张三", "visibility": "hidden", "online": False, "status": "offline"},
            {"user_id": "online-low", "nickname": "乙", "online": True, "status": "rest", "today_seconds": 60, "week_seconds": 120},
            {"user_id": "online-high", "nickname": "甲", "online": True, "status": "focus", "working": True, "today_seconds": 600, "week_seconds": 1200},
        ],
        "room_people": [], "requests": [], "visits": [],
    })
    app.processEvents()

    assert dialog.buddies.count() == 3
    first = dialog.buddies.itemWidget(dialog.buddies.item(0))
    last = dialog.buddies.itemWidget(dialog.buddies.item(2))
    assert first is not None and last is not None
    assert any("甲家的六毛" in label.text() for label in first.findChildren(QLabel))
    assert any("已离线" in label.text() for label in last.findChildren(QLabel))
    assert any("本周已专注 20分钟" in label.text() for label in first.findChildren(QLabel))
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_homepage_uses_weekly_focus_leaderboard_labels() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())
    dialog.apply_dashboard({
        "me": {"nickname": "六毛搭子"}, "buddies": [], "room_people": [],
        "leaderboard": [{"nickname": "甲", "week_seconds": 3660}],
    })
    app.processEvents()
    assert "甲家的六毛" in dialog.wealth_leaderboard.item(0).text()
    assert "本周专注 1小时1分钟" in dialog.wealth_leaderboard.item(0).text()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_social_hub_is_minimizable_and_signed_in_room_refresh_works() -> None:
    """自习室是普通可最小化窗口，且登录后能渲染房间数据。"""
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient(), outfit_key="hour-01")

    flags = dialog.windowFlags()
    assert flags & Qt.WindowType.WindowMinimizeButtonHint
    assert flags & Qt.WindowType.WindowSystemMenuHint
    assert flags & Qt.WindowType.WindowCloseButtonHint
    assert not flags & Qt.WindowType.WindowStaysOnTopHint
    assert not dialog.isModal()

    dialog.refresh()
    app.processEvents()
    assert dialog.tabs.currentIndex() == 0
    assert dialog.rooms.count() == 1
    assert "安静工作间" in dialog.rooms.item(0).text()
    assert "AB12CD34" in dialog.identity.text()

    dialog.showMinimized()
    app.processEvents()
    assert dialog.isMinimized()
    dialog.showNormal()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_wealth_leaderboard_is_on_by_default_but_preserves_explicit_opt_out() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())
    dialog.apply_dashboard(dialog.client.dashboard())
    assert dialog.wealth_opt_in.isChecked()

    data = dialog.client.dashboard()
    data["me"].update(
        {"wealth_leaderboard_enabled": False, "wealth_leaderboard_preference_set": True}
    )
    dialog.apply_dashboard(data)
    assert not dialog.wealth_opt_in.isChecked()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_supply_actions_are_large_inline_buttons() -> None:
    app = QApplication.instance() or QApplication([])
    widget = BuddyCardWidget({"nickname": "搭子", "online": True, "working": False})
    buttons = {button.text() for button in widget.findChildren(QPushButton)}
    assert {"请咖啡", "请奶茶", "敬茶", "请蛋糕"} <= buttons
    assert "送补给 ▼" not in buttons
    assert all(button.minimumHeight() >= 32 for button in widget.findChildren(QPushButton) if button.text() in buttons)
    widget.close(); widget.deleteLater(); app.processEvents()


def test_incoming_visit_notice_has_direct_accept_reject_and_later_actions() -> None:
    app = QApplication.instance() or QApplication([])
    notice = IncomingVisitNotice(
        {"id": "visit-1", "nickname": "论文搭子", "kind": "food_milk_tea"}
    )
    labels = [button.text() for button in notice.findChildren(QPushButton)]
    assert "论文搭子家的六毛🧋 请你喝奶茶" in [label.text() for label in notice.findChildren(QLabel)]
    assert {"接受", "拒绝", "稍后处理"} <= set(labels)
    notice.close_without_notice(); notice.deleteLater(); app.processEvents()


def test_signup_thread_calls_client_without_gui_side_effects() -> None:
    app = QApplication.instance() or QApplication([])

    class Client:
        def sign_up(self, email, password, nickname):
            return SignupResult(
                email=email,
                user_id="user-1",
                confirmation_pending=True,
                confirmation_sent=True,
            )

    client = Client()
    thread = SocialSignupThread(client, "person@example.com", "secret", "昵称")
    completed = []
    thread.completed.connect(completed.append)
    thread.run()

    assert completed == [
        SignupResult(
            email="person@example.com",
            user_id="user-1",
            confirmation_pending=True,
            confirmation_sent=True,
        )
    ]
    assert thread._password == ""
    thread.deleteLater()
    app.processEvents()


def test_signup_thread_forwards_social_error() -> None:
    app = QApplication.instance() or QApplication([])

    class Client:
        def sign_up(self, email, password, nickname):
            raise SocialError("SMTP timeout", kind="signup_timeout", retryable=True)

    thread = SocialSignupThread(Client(), "person@example.com", "secret", "昵称")
    failed = []
    thread.failed.connect(failed.append)
    thread.run()

    assert len(failed) == 1
    assert isinstance(failed[0], SocialError)
    assert failed[0].kind == "signup_timeout"
    assert thread._password == ""
    thread.deleteLater()
    app.processEvents()


def test_incoming_visit_response_thread_calls_visit_rpc() -> None:
    app = QApplication.instance() or QApplication([])

    class Client:
        def __init__(self) -> None:
            self.calls = []

        def rpc(self, name, body):
            self.calls.append((name, body))

    client = Client()
    event = {"id": "visit-2", "nickname": "搭子", "kind": "visit"}
    thread = SocialVisitResponseThread(client, event, True)
    completed = []
    thread.completed.connect(lambda received, accepted: completed.append((received, accepted)))
    thread.run()
    assert client.calls == [("lili_respond_visit", {"event_id": "visit-2", "accept": True})]
    assert completed == [(event, True)]
    thread.deleteLater(); app.processEvents()


def test_explicit_offline_flag_wins_over_stale_focus_payload() -> None:
    app = QApplication.instance() or QApplication([])
    widget = BuddyCardWidget(
        {"nickname": "搭子", "online": False, "working": True, "status": "focus"}
    )
    labels = [label.text() for label in widget.findChildren(QLabel)]
    assert any("已离线" in text for text in labels)
    assert all("正在工作" not in text for text in labels)
    widget.close(); widget.deleteLater(); app.processEvents()


def test_buddy_visit_is_a_normal_minimizable_taskbar_window() -> None:
    app = QApplication.instance() or QApplication([])
    visit = BuddyVisitWindow()
    flags = visit.windowFlags()
    assert flags & Qt.WindowType.Window
    assert flags & Qt.WindowType.WindowMinimizeButtonHint
    assert flags & Qt.WindowType.WindowSystemMenuHint
    assert flags & Qt.WindowType.WindowCloseButtonHint
    # Qt's window-type enum shares the low four bits (so ``flags & Tool`` is
    # true even for a normal Window).  Compare the type mask instead.
    assert (int(flags) & 0x0F) == int(Qt.WindowType.Window)
    assert not flags & Qt.WindowType.FramelessWindowHint
    assert not flags & Qt.WindowType.WindowStaysOnTopHint

    visit.show_peer({"id": "visit-1", "nickname": "搭子", "today_seconds": 60})
    app.processEvents()
    visit.showMinimized()
    app.processEvents()
    assert visit.isMinimized()
    # A repeated dashboard payload for the same visit must not restore it.
    visit.show_peer({"id": "visit-1", "nickname": "搭子", "today_seconds": 61})
    app.processEvents()
    assert visit.isMinimized()
    visit.hide_visit()
    visit.close(); visit.deleteLater(); app.processEvents()


def test_focus_page_shares_snapshot_and_renders_room_activity() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(RoomClient())
    dialog.set_focus_snapshot({"status": "focus", "session_seconds": 2520, "today_seconds": 1860})
    dialog.refresh()
    app.processEvents()
    dialog.rooms.setCurrentRow(0)
    app.processEvents()

    assert dialog.focus_status.text() == "专注中"
    assert "42分钟" in dialog.focus_clock.text()
    # 房间现在同时展示本人和搭子，避免只看到一个“休息中”的远端占位。
    assert dialog.room_members.count() == 2
    assert "（我）" in dialog.room_members.itemWidget(dialog.room_members.item(0)).findChildren(QLabel)[0].text()
    assert "正在工作" in dialog.room_members.itemWidget(dialog.room_members.item(1)).findChildren(QLabel)[0].text()
    assert dialog.room_activity.count() == 2
    assert "35分钟" in dialog.room_goal.text()
    report_buttons = dialog.findChildren(QPushButton, "focusReportButton")
    assert len(report_buttons) == 1
    report_signal = QSignalSpy(dialog.work_report_requested)
    report_buttons[0].click()
    assert report_signal.count() == 1
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_focus_weekly_total_does_not_become_yesterday_difference() -> None:
    """Live weekly reconciliation must not reuse the weekly value as a day delta."""

    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())
    dialog.set_focus_snapshot({
        "status": "rest",
        "session_seconds": 9 * 3600 + 53 * 60,
        "today_seconds": 9 * 3600 + 54 * 60,
    })
    dialog.set_focus_analytics({
        "today_seconds": 0,
        "weekly_total_seconds": 39 * 3600 + 8 * 60,
        "yesterday_seconds": 0,
        # Deliberately stale/invalid: this used to leak into the UI as a
        # 49-hour “较昨天” comparison after the live weekly supplement.
        "difference_vs_yesterday_seconds": 39 * 3600 + 8 * 60,
    })
    app.processEvents()

    assert "本周 49小时2分钟" in dialog.focus_insights.text()
    assert "较昨天 多 9小时54分钟" in dialog.focus_insights.text()
    assert "较昨天 多 49小时2分钟" not in dialog.focus_insights.text()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_recent_cached_presence_is_not_rendered_as_peer_offline() -> None:
    app = QApplication.instance() or QApplication([])
    client = UncertainRoomClient()
    dialog = SocialHubDialog(client)
    data = client.dashboard()
    dialog.current_room_id = "room-1"
    dialog._room_selection_explicit = True
    dialog.apply_dashboard(data)
    app.processEvents()

    peer_labels = []
    for index in range(dialog.room_members.count()):
        widget = dialog.room_members.itemWidget(dialog.room_members.item(index))
        if widget is not None:
            peer_labels.extend(label.text() for label in widget.findChildren(QLabel))

    assert any("正在工作（同步恢复中）" in text for text in peer_labels)
    assert all("已离线" not in text for text in peer_labels)
    assert "连接暂时不稳定" in dialog.status_label.text()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_offline_dashboard_does_not_mask_local_focus_when_no_room_is_selected() -> None:
    """A room sync failure must not look like a local focus failure."""

    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(OfflineCachedClient())
    dialog.set_focus_snapshot(
        {"status": "focus", "session_seconds": 30, "today_seconds": 30}
    )
    data = dialog.client.dashboard()
    dialog.apply_dashboard(data)
    app.processEvents()

    assert "当前无法连接自习室" not in dialog.status_label.text()
    assert "本地专注已开始" in dialog.status_label.text()

    # Once a room is explicitly selected, the same offline payload should keep
    # the room-specific warning so a real room outage remains visible.
    dialog.current_room_id = "room-1"
    dialog._room_selection_explicit = True
    room_data = dict(data)
    room_data["rooms"] = [
        {"id": "room-1", "name": "安静工作间", "members": 1, "invite_code": "ROOM1234"}
    ]
    dialog.apply_dashboard(room_data)
    app.processEvents()
    assert "当前无法连接自习室" in dialog.status_label.text()

    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_local_focus_wins_and_missing_leaderboard_does_not_clear_cache() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedInClient())
    dialog.set_focus_snapshot({"status": "focus", "session_seconds": 10, "today_seconds": 16080})
    dialog.apply_dashboard({
        "me": {"nickname": "六毛搭子", "today_seconds": 0},
        "buddies": [], "room_people": [], "requests": [], "visits": [],
        "leaderboard": [{"nickname": "甲", "period_income": 12}, {"nickname": "乙", "period_income": 3}],
    })
    app.processEvents()
    assert "4小时28分钟" in dialog.study_summary.text()
    assert "甲家的六毛" in dialog.wealth_leaderboard.item(0).text()

    dialog.apply_dashboard({"me": {"nickname": "六毛搭子"}, "buddies": [], "room_people": []})
    app.processEvents()
    assert "甲家的六毛" in dialog.wealth_leaderboard.item(0).text()

    dialog.apply_dashboard({"me": {"nickname": "六毛搭子"}, "leaderboard": []})
    app.processEvents()
    assert "暂无可展示" in dialog.wealth_leaderboard.item(0).text()
    dialog.close(); dialog.deleteLater(); app.processEvents()
