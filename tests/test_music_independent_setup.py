"""验证换电脑的程序发现、Mac 应用包选择、随机三十首及兼容旧歌单并允许跨轮重复。"""
import json
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


def test_legacy_start_history_loads_without_forcing_another_first_song(tmp_path):
    path = tmp_path / 'local-profile' / 'playlist.json'
    queue = QQPlaylist(Player(), store_path=path, shuffle=lambda _: None)
    assert queue.create().success
    first = queue.entries
    legacy = json.loads(path.read_text(encoding='utf-8'))
    legacy['recent_starts'] = [s.title for s in first[:10]]
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
    restored = QQPlaylist(Player(), store_path=path, shuffle=lambda _: None)
    assert restored.entries == first and not restored.auto_advance
    assert restored.create().success and restored.round_number == 2
    assert restored.entries == first and len({s.identifier for s in restored.entries}) == 30
    assert 'recent_starts' not in json.loads(path.read_text(encoding='utf-8'))
    # Every new machine starts with a clean local playlist; no test-selected song is shipped.
    fresh = QQPlaylist(Player(), store_path=tmp_path / 'second-computer' / 'playlist.json', shuffle=lambda _: None)
    assert not fresh.entries and not fresh.auto_advance
    assert fresh.create().success and fresh.round_number == 1


@pytest.mark.parametrize('current_title', ['歌曲1', '歌曲1 (Live)'])
def test_random_selection_allows_current_song_without_extra_media_lookup(current_title):
    player = Player()
    reads = []
    player.track_reader = lambda: reads.append(True) or SimpleNamespace(title=current_title)
    queue = QQPlaylist(player, shuffle=lambda _: None)
    assert queue.create().success and queue.entries[0].title == '歌曲1' and not reads


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
