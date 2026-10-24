"""验证 Mac 搜索子角色、按钮回退、广度读取、只读诊断、冷启动归属和设置线程安全关闭。"""
import plistlib
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from onepic_desktop_pet.qqmusic_macos import AXNode, BUNDLE_ID, NativeAX, QQMusicMacBridge, parse_track
from onepic_desktop_pet.qqmusic_queue_mode import MacQQModeBackend
from onepic_desktop_pet.music_playback import SongCandidate
from test_qqmusic_macos import Backend, region, static


def app_bundle(tmp_path):
    path = tmp_path / 'QQMusic.app'
    (path / 'Contents' / 'MacOS').mkdir(parents=True)
    (path / 'Contents' / 'MacOS' / 'QQMusic').touch()
    (path / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': BUNDLE_ID, 'CFBundleExecutable': 'QQMusic', 'CFBundleShortVersionString': '11.test'}))
    return path


def test_search_subrole_and_real_search_button_work_without_confirm_or_global_keys(tmp_path):
    path = app_bundle(tmp_path)
    root = AXNode('AXWindow', children=[AXNode('AXTextField', subrole='AXSearchField'),
        AXNode('AXButton', '搜索'), AXNode('AXRow', children=[AXNode('AXLink', '招摇 (Live)'),
        static('陈楚生 / 胡莎莎'), AXNode('AXButton', '播放歌曲')])])
    backend = Backend(root)
    def action(node, action='AXPress'):
        backend.actions.append((node.text, action))
        return action != 'AXConfirm'
    backend.action = action
    bridge = QQMusicMacBridge(backend=backend)
    bridge.play_candidate(path, SongCandidate('qq', '招摇 (Live)', '陈楚生 / 胡莎莎', identifier='1'))
    assert backend.actions == [('search', '陈楚生 招摇'), ('', 'AXConfirm'), ('搜索', 'AXPress'), ('播放歌曲', 'AXPress')]
    assert bridge.session() is None  # A search result is never playback proof.


def test_ambiguous_search_confirmation_does_not_press_unrelated_buttons(tmp_path):
    root = AXNode('AXWindow', children=[AXNode('AXSearchField', '搜索'), AXNode('AXButton', '搜索'), AXNode('AXButton', 'Search')])
    backend = Backend(root)
    backend.action = lambda *_: False
    with pytest.raises(OSError, match='搜索确认'):
        QQMusicMacBridge(backend=backend).play_candidate(app_bundle(tmp_path), SongCandidate('qq', '光', '陈楚生', identifier='1'))


def test_native_tree_keeps_search_placeholder_and_shallow_player_despite_deep_web_branch():
    deep = {'AXRole': 'AXGroup', 'AXChildren': []}
    for _ in range(32):
        deep = {'AXRole': 'AXGroup', 'AXChildren': [deep]}
    toolbar = {'AXRole': 'AXToolbar', 'AXChildren': [
        *({'AXRole': 'AXStaticText', 'AXValue': text} for text in ('光', '陈楚生', '00:15', '03:39')),
        {'AXRole': 'AXButton', 'AXDescription': '暂停(P)'},
        {'AXRole': 'AXPopUpButton', 'AXTitle': '播放模式', 'AXValue': '随机播放'}]}
    root = {'AXRole': 'AXApplication', 'AXWindows': [{'AXRole': 'AXWindow', 'AXChildren': [deep, toolbar,
        {'AXRole': 'AXTextField', 'AXSubrole': 'AXSearchField', 'AXPlaceholderValue': '搜索音乐'}]}]}
    backend = NativeAX.__new__(NativeAX)
    backend.cancelled = lambda: False
    backend.ax = SimpleNamespace(
        AXUIElementCopyMultipleAttributeValues=lambda element, names, *_: (0, [element.get(name) for name in names]),
        AXUIElementCopyAttributeValue=lambda element, name, *_: (0, element.get(name)))
    tree = backend.tree(root)
    assert parse_track(tree).title == '光' and parse_track(tree).playback_status == 'playing'
    assert backend.truncated
    assert any(n.text == '搜索音乐' and n.subrole == 'AXSearchField' for n in tree.descendants())
    assert any(n.role == 'AXPopUpButton' and n.text == '随机播放' for n in tree.descendants())


def test_mac_mode_targets_player_popup_and_uses_supported_menu_action():
    player = region()
    mode_button = AXNode('AXPopUpButton', '随机播放')
    player.children.append(mode_button)
    root = AXNode('AXWindow', children=[player, AXNode('AXButton', '单曲循环')])
    backend = Backend(root)
    def action(node, action='AXPress'):
        backend.actions.append((node.text, action))
        if node is mode_button:
            if action != 'AXShowMenu':
                return False
            root.children.append(AXNode('AXMenuItem', '单曲循环'))
        else:
            assert node.role == 'AXMenuItem'
            mode_button.text = '单曲循环'
        return True
    backend.action = action
    bridge = QQMusicMacBridge(backend=backend, sleep=lambda _: None)
    mode = MacQQModeBackend(bridge)
    assert mode.read() == '随机播放' and mode.set('单曲循环') and mode.read() == '单曲循环'
    assert backend.actions == [('随机播放', 'AXPress'), ('随机播放', 'AXShowMenu'), ('单曲循环', 'AXPress')]


def test_diagnostics_never_launch_search_play_hide_or_export_account_text(tmp_path):
    player = region()
    player.children.append(AXNode('AXPopUpButton', '顺序播放'))
    root = AXNode('AXWindow', children=[static('PRIVATE-ACCOUNT-NAME'), player, AXNode('AXTextField', '搜索')])
    backend = Backend(root)
    def forbidden(*_):
        pytest.fail('Read-only diagnostics must not change the client')
    backend.action = backend.search = backend.ensure_running = forbidden
    result = QQMusicMacBridge(backend=backend).diagnostics(app_bundle(tmp_path))
    assert '11.test' in result and '搜索框：1' in result and '顺序播放' in result
    assert 'PRIVATE-ACCOUNT-NAME' not in result and not backend.actions


def test_existing_mac_application_is_never_hidden_or_relaunched():
    backend = NativeAX.__new__(NativeAX)
    backend.ax = SimpleNamespace(AXIsProcessTrusted=lambda: True)
    backend.apps = SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda _: [object()])
    assert backend.ensure_running(Path('/Applications/QQMusic.app')) is False


def test_cold_mac_launch_only_sets_hidden_nonactivating_configuration(monkeypatch):
    events = []
    config = SimpleNamespace(setActivates_=lambda value: events.append(('activates', value)),
                             setHides_=lambda value: events.append(('hides', value)))
    workspace = SimpleNamespace(openApplicationAtURL_configuration_completionHandler_=lambda *args: events.append(('launch', args)))
    monkeypatch.setitem(sys.modules, 'AppKit', SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
        NSWorkspaceOpenConfiguration=SimpleNamespace(configuration=lambda: config)))
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(NSURL=SimpleNamespace(fileURLWithPath_=lambda value: value)))
    backend = NativeAX.__new__(NativeAX)
    backend.ax = SimpleNamespace(AXIsProcessTrusted=lambda: True)
    backend.apps = SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda _: [])
    assert backend.ensure_running(Path('/Applications/QQMusic.app'))
    assert events[:2] == [('activates', False), ('hides', True)] and events[2][0] == 'launch'


def test_settings_check_uses_unsaved_app_path_and_defers_close_until_native_thread_ends(tmp_path, monkeypatch):
    from threading import Event
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from onepic_desktop_pet import chat
    from onepic_desktop_pet.config import PetSettings
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(chat, 'sys', SimpleNamespace(platform='darwin'))
    entered = Event()
    seen, ticks = [], []
    def diagnose(self, path):
        seen.append(path)
        entered.set()
        while not self.cancelled():
            time.sleep(.002)
        return 'cancelled'
    monkeypatch.setattr(QQMusicMacBridge, 'diagnostics', diagnose)
    settings = PetSettings()
    dialog = chat.AISettingsDialog(settings, SimpleNamespace(get=lambda *_: '', has=lambda *_: False))
    path = str(app_bundle(tmp_path))
    dialog.qq_music_path.setText(path)
    dialog.show()
    dialog._check_qq_music()
    assert entered.wait(1)
    QTimer.singleShot(0, lambda: ticks.append(True))
    dialog.reject()
    deadline = time.monotonic() + 3
    while dialog._qq_check_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert dialog._qq_check_thread is None and ticks and seen == [path]
    assert not settings.qq_music_path and not dialog.isVisible()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.skipif(sys.platform != 'darwin', reason='Cocoa and native AX binding require macOS')
def test_native_mac_launch_configuration_and_bulk_attribute_bindings_exist_without_launching():
    import ApplicationServices as ax
    from AppKit import NSWorkspace, NSWorkspaceOpenConfiguration
    config = NSWorkspaceOpenConfiguration.configuration()
    config.setActivates_(False)
    config.setHides_(True)
    assert not config.activates() and config.hides()
    assert callable(NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_)
    assert callable(ax.AXUIElementCopyMultipleAttributeValues)
