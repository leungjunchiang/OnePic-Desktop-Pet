"""验证 Mac QQ 的真实 AX 播放栏判定、权限拒绝和只操作目标客户端的后台路径。"""
from pathlib import Path
import plistlib
from types import SimpleNamespace
import pytest

from onepic_desktop_pet.qqmusic_macos import (
    AXNode, BUNDLE_ID, QQMusicMacBridge, clock_ms, parse_track, player_region,
)
from onepic_desktop_pet.qqmusic_online import candidate_matches
from onepic_desktop_pet.music_control import MusicProviderManager, TrackInfo
from onepic_desktop_pet.music_playback import SongCandidate
from onepic_desktop_pet.config import PetSettings


def static(text, identifier=''):
    return AXNode('AXStaticText', text, identifier)


def region(*, state='暂停', position='00:15', duration='03:39', title='山高水长'):
    return AXNode('AXGroup', children=[static(title),static('陈楚生'),static(position),static(duration),AXNode('AXButton',state)])


@pytest.mark.parametrize('text,expected', [('01:02',62000),('1:02:03',3723000),('-03:14',-194000),('播放',None)])
def test_time_labels_are_parsed_without_guessing(text, expected):
    assert clock_ms(text) == expected


def test_only_a_clear_player_bar_proves_playback():
    track = parse_track(region())
    assert (track.title,track.artist,track.playback_status,track.position_ms,track.duration_ms) == (
        '山高水长','陈楚生','playing',15000,219000)
    assert track.metadata_scope == 'mac-accessibility'
    assert parse_track(region(state='播放')).playback_status == 'paused'
    for invalid in (AXNode('AXWindow',children=[static('山高水长'),static('陈楚生'),AXNode('AXButton','播放')]),
                    AXNode('AXGroup',children=[*region().children,AXNode('AXButton','播放')])):
        assert player_region(invalid) is None and parse_track(invalid) is None


def test_remaining_time_and_zero_at_natural_end():
    track = parse_track(region(position='00:15', duration='-03:24'))
    assert (track.position_ms,track.duration_ms) == (15000,219000)
    track = parse_track(region(state='播放',position='03:39', duration='-00:00'))
    assert (track.position_ms,track.duration_ms) == (219000,219000)


def test_search_results_do_not_become_playback_proof():
    row = AXNode('AXRow',children=[static('山高水长'),static('陈楚生'),static('03:39'),AXNode('AXButton','播放')])
    assert parse_track(AXNode('AXWindow',children=[row])) is None
    ambiguous = region()
    ambiguous.children.append(static('其他文本'))
    assert parse_track(ambiguous) is None


def test_missing_album_is_relaxed_only_for_explicit_mac_ax_scope():
    candidate = SongCandidate('qq','山高水长','陈楚生',identifier='739823',native={'album':'央视亚运主题曲'})
    assert not candidate_matches(candidate,TrackInfo('山高水长','陈楚生','','playing'))
    assert candidate_matches(candidate,parse_track(region()))
    assert not candidate_matches(candidate,parse_track(region(title='山高水长 (Live)')))


class Backend:
    def __init__(self, tree=None):
        self.root = tree or region()
        self.app = SimpleNamespace(hide=lambda: self.actions.append('hide'))
        self.actions = []
    def application(self): return self.app, self.root
    def tree(self, root): return root
    def action(self, node, action='AXPress'):
        self.actions.append((node.text,action))
        return True
    def search(self, node, text):
        self.actions.append(('search',text))
        return True


def test_permission_or_unreadable_ui_never_confirms_or_controls():
    backend = Backend()
    def denied(): raise PermissionError('需要辅助功能权限')
    backend.application = denied
    bridge = QQMusicMacBridge(backend=backend)
    assert bridge.session() is None and '权限' in bridge.last_error
    assert not bridge.control(BUNDLE_ID,'pause')
    assert not backend.actions


def test_control_uses_only_the_qq_player_bar_and_idempotent_state():
    backend = Backend()
    bridge = QQMusicMacBridge(backend=backend)
    assert bridge.session().source_id == BUNDLE_ID
    assert not bridge.control('com.apple.Music','pause')
    assert bridge.control(BUNDLE_ID,'play') and backend.actions == []
    assert bridge.control(BUNDLE_ID,'pause')
    assert backend.actions == [('暂停','AXPress')]


def test_background_dispatch_does_not_activate_a_window_or_send_keys(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a,**k:pytest.fail('No shell, launcher or keyboard process'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a,**k:pytest.fail('No shell, launcher or keyboard process'))
    app = tmp_path/'QQMusic.app'
    (app/'Contents').mkdir(parents=True)
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':BUNDLE_ID}))
    tree = AXNode('AXWindow',children=[AXNode('AXSearchField','搜索'),
        AXNode('AXRow',children=[static('山高水长'),static('陈楚生'),AXNode('AXButton','播放歌曲')])])
    backend = Backend(tree)
    bridge = QQMusicMacBridge(backend=backend)
    bridge.play_candidate(app,SongCandidate('qq','山高水长','陈楚生',identifier='739823'))
    assert backend.actions == [('search','陈楚生 山高水长'),('搜索','AXConfirm'),('播放歌曲','AXPress')]
    assert bridge.session() is None  # 发出 AXPress 也不能伪造 Playing。


def test_cancelled_selection_does_not_launch_or_change_any_controls(tmp_path):
    app = tmp_path/'QQMusic.app'
    (app/'Contents').mkdir(parents=True)
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':BUNDLE_ID}))
    backend = Backend()
    bridge = QQMusicMacBridge(backend=backend,cancelled=lambda:True)
    with pytest.raises(OSError,match='取消'):
        bridge.play_candidate(app,SongCandidate('qq','山高水长','陈楚生',identifier='739823'))
    assert not backend.actions


def test_manager_on_mac_reads_qq_ax_without_touching_windows_sessions():
    backend = Backend()
    bridge = QQMusicMacBridge(backend=backend)
    manager = MusicProviderManager(PetSettings(),platform_name='darwin',mac_qq_bridge=bridge,
        windows_bridge=SimpleNamespace(sessions=lambda:pytest.fail('Windows bridge must not be used')),
        client_finder=lambda *_:Path('/Applications/QQMusic.app'))
    assert manager._qq_online_track().title == '山高水长'
    manager._online_qq_selected = True
    manager.active_provider = 'qq'
    backend.root = region(state='播放')
    assert manager.control('qq','pause').success
    assert not backend.actions


@pytest.mark.skipif(__import__('sys').platform != 'darwin',reason='native ApplicationServices exists on macOS')
def test_native_accessibility_binding_imports_without_requesting_permission():
    import ApplicationServices as ax
    assert callable(ax.AXIsProcessTrusted)
    assert callable(ax.AXUIElementCopyAttributeValue)
    assert callable(ax.AXUIElementPerformAction)
