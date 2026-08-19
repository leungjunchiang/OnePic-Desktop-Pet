"""验证搭子自习室四标签布局和未登录交互反馈。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

from onepic_desktop_pet.social_ui import BuddyVisitWindow, SocialHubDialog


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

