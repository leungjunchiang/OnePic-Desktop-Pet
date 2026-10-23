"""验证随机与歌单双入口共用工作报告的悬停/移出规则，查看歌单不触发播放。"""

import pytest
import time
from PySide6.QtCore import QPoint
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from onepic_desktop_pet.controls import QuickControlPanel


def wait_until(condition):
    """轮询真实状态并持续处理 Qt 事件，不依赖 C++ 模板的 Python 绑定。"""
    deadline = time.monotonic() + 1.5
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(10)
    return condition()


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    value = QuickControlPanel()
    target = [None]
    value._button_at_global_pos = lambda _: target[0]
    value.show()
    app.processEvents()
    yield value, target, app
    value.hide()
    value.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('primary,secondary,mode', [
    ('work_button', 'report_button', 'work_report'),
    ('music_button', 'random_music_button', 'music_random'),
])
def test_same_dwell_bridge_and_leave_hide_for_both_shortcuts(panel, primary, secondary, mode):
    value, target, app = panel
    primary, secondary = getattr(value, primary), getattr(value, secondary)
    target[0] = primary
    value._poll_hover_button()
    assert not secondary.isVisible()
    assert wait_until(lambda: secondary.isVisible() and value._secondary_mode == mode)
    assert primary.mapToGlobal(QPoint(primary.width() // 2, 0)).x() == secondary.mapToGlobal(
        QPoint(secondary.width() // 2, 0)).x()
    target[0] = None
    value._poll_hover_button()
    assert value._report_hide_timer.isActive()
    target[0] = secondary
    value._poll_hover_button()
    QTest.qWait(180)
    assert secondary.isVisible()
    target[0] = None
    value._poll_hover_button()
    # 60ms polling must not perpetually restart the 160ms hide timer. Await
    # the actual transition rather than assuming the runner schedules it at
    # one exact wall-clock millisecond (macOS may synthesize later Enter events).
    assert wait_until(lambda: not secondary.isVisible())


def test_crossing_music_fast_never_shows_or_plays_and_switching_modes_keeps_primary_row(panel):
    value, target, app = panel
    requests = QSignalSpy(value.music_requested)
    original = value.music_button.mapToGlobal(QPoint(0, 0))
    target[0] = value.music_button
    value._poll_hover_button()
    target[0] = value.social_button
    value._poll_hover_button()
    QTest.qWait(180)
    assert not value.random_music_button.isVisible() and requests.count() == 0
    for button, visible, hidden in ((value.work_button, value.report_button, value.random_music_button),
                                    (value.music_button, value.random_music_button, value.report_button)):
        target[0] = button
        value._set_hover_button(button)
        app.processEvents()
        assert visible.isVisible() and not hidden.isVisible()
        if value._stable_windows_dock:
            assert value.music_button.mapToGlobal(QPoint(0, 0)) == original
    value.random_music_button.click()
    assert requests.count() == 1
    assert not value.isVisible() and not value.random_music_button.isVisible()
    value.show()
    target[0] = None
    value._poll_hover_button()
    assert not value.random_music_button.isVisible()


def test_playlist_is_discoverable_beside_random_and_stays_until_leaving_both(panel):
    value, target, app = panel
    viewed, played = QSignalSpy(value.music_playlist_requested), QSignalSpy(value.music_requested)
    original=value.music_button.mapToGlobal(QPoint(0,0))
    target[0]=value.music_button
    value._poll_hover_button()
    assert wait_until(lambda:value.music_playlist_button.isVisible() and value.random_music_button.isVisible())
    assert value.music_playlist_button.text()=='歌单'
    assert value.rect().contains(value.music_playlist_button.mapTo(value,value.music_playlist_button.rect().bottomRight()))
    if value._stable_windows_dock:
        assert value.music_button.mapToGlobal(QPoint(0,0))==original
    target[0]=value.music_playlist_button
    value._poll_hover_button()
    QTest.qWait(180)
    assert value.music_playlist_button.isVisible() and value.random_music_button.isVisible()
    target[0]=None
    value._poll_hover_button()
    assert wait_until(lambda:not value.music_playlist_button.isVisible() and not value.random_music_button.isVisible())
    target[0]=value.music_button
    value._set_hover_button(value.music_button)
    value.music_playlist_button.click()
    assert viewed.count()==1 and played.count()==0 and not value.isVisible()


def test_music_menu_puts_saved_playlist_first_and_does_not_play_on_view(panel, monkeypatch):
    from PySide6.QtWidgets import QMenu
    value, _, app = panel
    viewed, played = QSignalSpy(value.music_playlist_requested), QSignalSpy(value.music_requested)
    titles=[]
    def execute(menu,*_):
        titles.extend(action.text() for action in menu.actions())
        menu.actions()[0].trigger()
    def menu_factory(parent):
        menu=QMenu(parent)
        # The application decorates each QMenu instance for native safety;
        # replace exec after construction rather than patching its base type.
        menu.exec=lambda *args:execute(menu,*args)
        return menu
    monkeypatch.setattr('onepic_desktop_pet.controls.QMenu',menu_factory)
    value._show_music_menu()
    assert titles[:2]==['查看陈楚生歌单…','随机听陈楚生（QQ 在线）']
    assert viewed.count()==1 and played.count()==0
