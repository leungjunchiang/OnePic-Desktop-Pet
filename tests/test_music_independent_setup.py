"""验证换电脑的本机程序发现、Mac 应用包选择，以及随机首曲跨重启避重。"""
import plistlib
from types import SimpleNamespace

import pytest

from onepic_desktop_pet import music
from onepic_desktop_pet.qqmusic_playlist import QQPlaylist
from test_qqmusic_playlist import Player, song


def mac_app(tmp_path, *, name='QQ Music.app', bundle='com.tencent.QQMusicMac'):
    app = tmp_path / name
    contents = app / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': bundle, 'CFBundleExecutable': 'QQMusic'}))
    (contents / 'MacOS' / 'QQMusic').touch()
    return app


def test_mac_app_and_internal_executable_resolve_to_validated_bundle(tmp_path):
    app = mac_app(tmp_path)
    for path in (app, app / 'Contents' / 'MacOS' / 'QQMusic', f'"{app}"'):
        assert music.normalize_music_client_path('qq', path, platform_name='darwin') == app
    (app / 'Contents' / 'MacOS' / 'QQMusic').unlink()
    assert music.normalize_music_client_path('qq', app, platform_name='darwin') is None


def test_wrong_or_incomplete_app_and_parent_directory_are_refused(tmp_path):
    app = mac_app(tmp_path, bundle='com.example.OtherMusic')
    assert music.normalize_music_client_path('qq', app, platform_name='darwin') is None
    assert music.normalize_music_client_path('qq', tmp_path, platform_name='darwin') is None
    (app / 'Contents' / 'Info.plist').write_bytes(b'broken')
    assert music.normalize_music_client_path('qq', app, platform_name='darwin') is None


def test_new_mac_discards_other_computers_path_and_discovers_renamed_local_qq(tmp_path, monkeypatch):
    app = mac_app(tmp_path, name='Renamed music.app')
    monkeypatch.setattr(music.sys, 'platform', 'darwin')
    monkeypatch.setattr(music, '_registered_music_client', lambda *_: None)
    monkeypatch.setattr(music, '_macos_registered_music_client', lambda *_: app)
    assert music.find_music_client('qq', r'C:\OldComputer\QQMusic.exe') == app


def test_mac_picker_uses_file_panel_for_app_packages(monkeypatch):
    from onepic_desktop_pet import chat
    calls = []
    monkeypatch.setattr(chat.sys, 'platform', 'darwin')
    monkeypatch.setattr(chat.QFileDialog, 'getExistingDirectory', lambda *_: pytest.fail('Packages are files in the native panel'))
    monkeypatch.setattr(chat.QFileDialog, 'getOpenFileName', lambda *args: calls.append(args) or ('/Applications/QQMusic.app', ''))
    assert chat.AISettingsDialog._choose_music_program(None, 'QQ', '') == '/Applications/QQMusic.app'
    assert calls[0][2:] == ('/Applications', '应用程序 (*.app);;所有文件 (*)')


def test_recent_ten_confirmed_starts_survive_restarts_and_do_not_repeat(tmp_path):
    path = tmp_path / 'local-profile' / 'playlist.json'
    starts = []
    for _ in range(15):
        queue = QQPlaylist(Player(), store_path=path, shuffle=lambda _: None)
        result = queue.create()
        assert result.success
        starts.append(queue.entries[0].title)
        assert starts[-1] not in starts[max(0, len(starts) - 11):-1]
        assert len(queue.recent_starts) <= 10
    # Every new machine starts with a clean local playlist; no test-selected song is shipped.
    fresh = QQPlaylist(Player(), store_path=tmp_path / 'second-computer' / 'playlist.json', shuffle=lambda _: None)
    assert not fresh.entries and not fresh.recent_starts and not fresh.auto_advance
    assert fresh.create().success and fresh.round_number == 1


def test_actual_client_song_is_avoided_even_without_a_saved_playlist():
    player = Player()
    player.track_reader = lambda: SimpleNamespace(title='歌曲1')
    queue = QQPlaylist(player, shuffle=lambda _: None)
    assert queue.create().success and queue.entries[0].title != '歌曲1'
    player.track_reader = lambda: SimpleNamespace(title='歌曲1 (Live)')
    fresh = QQPlaylist(player, shuffle=lambda _: None)
    assert fresh.create().success and fresh.entries[0].title != '歌曲1'


def test_automatic_native_round_draws_from_complete_cached_pool_without_catalog_io():
    player = Player()
    player.catalog.artist_candidates = lambda *_a, **_k: tuple(song(i) for i in range(1, 101))
    shifts = [0]
    def shuffle(rows):
        step = shifts[0] % len(rows)
        rows[:] = rows[step:] + rows[:step]
        shifts[0] += 31
    queue = QQPlaylist(player, shuffle=shuffle, native_queue=SimpleNamespace(dispatch=lambda *_: None))
    assert queue.create().success
    first = {s.identifier for s in queue.entries}
    assert len(queue._catalog) == 100 and not player.searched
    player.catalog.artist_candidates = lambda *_a, **_k: pytest.fail('Round end must not request artist catalog')
    player.catalog.search = lambda *_a, **_k: pytest.fail('Round end must not search')
    assert queue.create(reuse_catalog=True).success
    assert {s.identifier for s in queue.entries} != first and len(queue.entries) == 30
