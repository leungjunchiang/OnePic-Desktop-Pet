"""验证搭子自习室四标签布局和未登录交互反馈。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabWidget

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


def test_social_hub_has_four_function_pages_and_compact_auth_tabs() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(SignedOutClient())

    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "首页", "聊天", "专注", "我的"
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
