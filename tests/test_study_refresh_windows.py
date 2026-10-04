"""自习室刷新不能把卡片子控件短暂显示为独立窗口；覆盖重建、隐藏和焦点。"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget, QListWidget, QLineEdit, QLabel
from onepic_desktop_pet.social_ui import BuddyCardWidget, SocialHubDialog


class WindowShows(QObject):
    def __init__(self):
        super().__init__()
        self.shown = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow():
            self.shown.append((type(watched).__name__, watched.objectName(), watched.windowTitle()))
        return False


@pytest.fixture
def surface_watch():
    qt = QApplication.instance() or QApplication([])
    watch = WindowShows()
    qt.installEventFilter(watch)
    try:
        yield qt, watch
    finally:
        qt.removeEventFilter(watch)


def buddy(**changes):
    return dict(user_id="peer", nickname="公开昵称", private_note_name="论文搭子",
                online=False, working=False, status="offline", last_seen_age_seconds=900,
                on_focus_start=True, on_focus_end=True, **changes)


@pytest.mark.parametrize("visible", [False, True])
def test_card_construction_never_shows_orphan_labels(surface_watch, visible):
    qt, watch = surface_watch
    host = QListWidget()
    if visible:
        host.show()
        qt.processEvents()
    watch.shown.clear()
    try:
        card = BuddyCardWidget(buddy(), host)
        assert watch.shown == []  # 即便瞬间收回，Show 事件仍能捕获，不能只检查最终窗口数。
        assert all(not label.isWindow() for label in card.findChildren(QLabel))
        assert card.reminder_summary.parentWidget() is card
        assert not card.reminder_summary.isHidden()
        assert not card._confirmation_label.isHidden()
    finally:
        host.close()
        host.deleteLater()
        qt.processEvents()


def test_restored_coaching_badge_is_a_page_child_before_layout_adoption(surface_watch, monkeypatch):
    from onepic_desktop_pet import coaching_ui
    qt, watch = surface_watch
    host = QWidget()
    host.show()
    qt.processEvents()
    engine = SimpleNamespace(store=SimpleNamespace(account_id="a", coaching_cases=[]))
    monkeypatch.setattr(coaching_ui, "projection", lambda *args: {
        "card": None, "badge": {"id": "case-1", "badge_text": "说明待处理"},
        "card_count": 0, "badge_count": 1})
    watch.shown.clear()
    try:
        panel = coaching_ui.CoachingPanel(lambda: engine, lambda: (0, 0), lambda *args: None,
                                         lambda _: "搭子", lambda: None, host)
        assert watch.shown == []
        assert panel.badge.parentWidget() is host and not panel.badge.isWindow()
        assert panel.badge.text() == "说明待处理"
    finally:
        host.close()
        host.deleteLater()
        qt.processEvents()


class Client:
    signed_in = False
    session = SimpleNamespace(user_id="account-a")
    backend_name = "local"
    backend_endpoint = ""


@pytest.mark.parametrize("visible", [False, True])
def test_dashboard_rebuild_keeps_refresh_inside_hub(surface_watch, visible):
    qt, watch = surface_watch
    hub = SocialHubDialog(Client())
    other = QLineEdit()
    other.show()
    if visible:
        hub.show()
    qt.processEvents()
    other.activateWindow()
    other.setFocus()
    qt.processEvents()
    focus_before = qt.focusWidget()
    watch.shown.clear()
    try:
        for index in range(3):
            row = buddy()
            row["outfit_key"] = f"hour-{index:02}"
            hub.apply_dashboard({"me": {"user_id": "account-a"}, "buddies": [row]})
            qt.processEvents()
        assert watch.shown == []
        assert qt.focusWidget() is focus_before
        assert hub.isVisible() is visible
    finally:
        other.close()
        hub.close()
        other.deleteLater()
        hub.deleteLater()
        qt.processEvents()


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal"},
                    reason="真实 HWND 焦点检查需要 Windows 原生插件；无头运行用 Show 事件验证")
def test_native_refresh_does_not_change_foreground_window(surface_watch):
    import ctypes
    qt, watch = surface_watch
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    hub = SocialHubDialog(Client())
    hub.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    # 测试窗口本身也不激活用户当前程序；只比较刷新前后的前台 HWND。
    hub.show()
    qt.processEvents()
    foreground = user32.GetForegroundWindow()
    watch.shown.clear()
    try:
        for index in range(4):
            row = buddy()
            row["outfit_key"] = str(index)
            hub.apply_dashboard({"me": {"user_id": "account-a"}, "buddies": [row]})
            qt.processEvents()
            assert user32.GetForegroundWindow() == foreground
        assert watch.shown == []
    finally:
        hub.close()
        hub.deleteLater()
        qt.processEvents()
