"""验证真实 Qt 反馈、阻塞网络期间的事件循环、重复提交保护及投喂成功后记账。"""

import os
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt, QEvent, QTimer, QObject, Signal, QPoint
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton
from onepic_desktop_pet.ui_feedback import ACTION_BUTTON_STYLE, decorate_buttons
from onepic_desktop_pet.social_ui import SocialHubDialog
from onepic_desktop_pet.social import SocialError
from onepic_desktop_pet.window import PetWindow
from onepic_desktop_pet.ui_feedback import readable_milk_tea_label


@pytest.fixture(scope="module", autouse=True)
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def cleanup(application):
    yield
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_rendered_hover_and_press_are_distinct(application):
    button = QPushButton("开始专注")
    button.resize(160, 50); button.setStyleSheet(ACTION_BUTTON_STYLE)
    decorate_buttons(button)
    button.show(); application.processEvents()
    def color(state):
        option = QStyleOptionButton(); button.initStyleOption(option)
        option.state &= ~(QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Sunken)
        option.state |= state
        image = QImage(button.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
        painter.end()
        return image.pixelColor(12, 12)
    normal = color(QStyle.StateFlag.State_None)
    hover = color(QStyle.StateFlag.State_MouseOver)
    QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=QPoint(30, 20))
    assert button.isDown()
    pressed = color(QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Sunken)
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=QPoint(30, 20))
    assert normal != hover != pressed
    assert button.cursor().shape() == Qt.CursorShape.PointingHandCursor
    button.close(); button.deleteLater()


@pytest.mark.parametrize("supported", [False, True])
def test_missing_cup_glyph_keeps_readable_milk_tea_text(monkeypatch, supported):
    monkeypatch.setattr("onepic_desktop_pet.ui_feedback.QFontMetrics", lambda font: SimpleNamespace(inFontUcs4=lambda code: supported))
    assert readable_milk_tea_label("🥤 奶茶", None) == ("🥤 奶茶" if supported else "奶茶")


class Client:
    signed_in = False
    session = SimpleNamespace(user_id="a")


def test_focus_click_has_immediate_feedback_and_emits_once(application):
    hub = SocialHubDialog(Client())
    hub.set_focus_snapshot({"status": "idle", "session_seconds": 0, "today_seconds": 0})
    spy = QSignalSpy(hub.focus_start_requested)
    hub.focus_start_requested.connect(lambda: hub.set_focus_snapshot({"status": "focus", "session_seconds": 0, "today_seconds": 0}))
    hub.focus_start.click(); hub.focus_start.click()
    assert hub.focus_start.text() == "正在开始…" and not hub.focus_start.isEnabled()
    assert spy.count() == 0 and hub.tabs.isEnabled()
    application.processEvents()
    assert spy.count() == 1 and not hub.focus_start.property("actionBusy")
    assert hub.focus_pause.isEnabled() and not hub.focus_start.isEnabled()
    hub.close(); hub.deleteLater()


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("kind", ["visit", "cheer"])
def test_blocked_interaction_does_not_block_gui_and_releases_guard(application, failure, kind):
    class SlowClient(Client):
        def __init__(self):
            self.entered = threading.Event(); self.release = threading.Event(); self.calls = 0
        def rpc(self, name, body):
            self.calls += 1; self.entered.set()
            assert self.release.wait(10)
            if failure:
                raise SocialError("network failed", kind="network")
            return {}
    client = SlowClient(); hub = SocialHubDialog(client)
    client.signed_in = True
    hub.refresh = lambda: None
    hub._refresh_selected_room = lambda: None
    buddy = {"user_id": "b", "nickname": "搭子", "online": True, "working": True, "status": "focus"}
    beats = []
    timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: beats.append(1)); timer.start()
    try:
        hub._send_interaction(buddy, kind)
        assert client.entered.wait(1)
        hub._send_interaction(buddy, kind)
        # AppKit/offscreen 在负载下会延迟短计时器派发；等待实际心跳，
        # 网络仍由 release 保持阻塞，不能把一次 50 ms 等待当作 GUI 卡死。
        for _ in range(100):
            if beats:
                break
            QTest.qWait(20)
        assert beats and client.calls == 1 and hub.tabs.isEnabled()
        assert "正在" in hub.status_label.text()
    finally:
        client.release.set()
        for _ in range(100):
            QTest.qWait(10)
            if not hub._buddy_rpc_threads:
                break
        timer.stop()
    assert not hub._pending_interactions and not hub._buddy_rpc_threads
    assert ("network" in hub.status_label.text() or "没有" in hub.status_label.text()) if failure else "正在" not in hub.status_label.text()
    hub.close(); hub.deleteLater()


def test_rest_day_failure_restores_only_working_button(application):
    client = Client(); hub = SocialHubDialog(client); client.signed_in = True
    calls = []
    hub._discipline_rpc_executor = lambda name, body, done, fail: calls.append((done, fail))
    hub._set_rest_day(lambda: None, hub.rest_day_button)
    assert not hub.rest_day_button.isEnabled() and hub.tabs.isEnabled()
    assert hub.rest_day_button.text() == "正在设置…"
    hub._refresh_focus_goals()
    assert not hub.rest_day_button.isEnabled()
    calls[-1][1]("timeout")
    assert not hub.rest_day_button.property("actionBusy") and not hub._rest_day_pending
    calls[-1][0]({})  # A late success cannot revive a failed request.
    assert not hub._rest_day_pending
    hub.close(); hub.deleteLater()


@pytest.mark.parametrize("failure", [False, True])
def test_food_request_waits_for_ack_deduplicates_and_releases_reservation(failure):
    class Worker(QObject):
        finished = Signal()
    class Economy:
        balance = 100
        def __init__(self):
            self.debits = []
        def catalog(self):
            return {"milk_tea": {"price": 5}}
        def record_food_gift_sent(self, *args, **kwargs):
            self.debits.append(kwargs["operation_key"])
            return SimpleNamespace(as_dict=lambda: {})
    calls = []; notices = []; worker = Worker()
    window = SimpleNamespace(social_client=SimpleNamespace(signed_in=True), economy=Economy(),
        _current_social_user_id=lambda: "a", show_speech=lambda text, ms: notices.append(text),
        _discipline_rpc=lambda name, body, done, fail, **kwargs: calls.append((done, fail)) or worker,
        _sync_economy_events=lambda events: None, _set_social_food_activity=lambda *args: None)
    buddy = {"user_id": "b", "private_note_name": "论文搭子"}
    PetWindow._send_food_interaction(window, buddy, "food_milk_tea")
    PetWindow._send_food_interaction(window, buddy, "food_milk_tea")
    assert len(calls) == 1 and not window.economy.debits
    if failure:
        calls[0][1]("timeout")
        assert not window.economy.debits
    else:
        calls[0][0]({})
        assert len(window.economy.debits) == 1 and "🥤" in notices[-1]
    assert not window._pending_food_gifts
