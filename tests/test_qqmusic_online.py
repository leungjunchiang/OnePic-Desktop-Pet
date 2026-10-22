"""验证在线目录筛选、单次原生投递、真实播放确认及失败时不误控其他应用。"""

from pathlib import Path
from threading import Event

import pytest

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_commands import parse_music_request
from onepic_desktop_pet.music_control import (
    MusicProviderManager, TrackInfo, WindowsSessionSnapshot,
)
from onepic_desktop_pet.music_playback import MusicPlaybackOutcome, SongCandidate
from onepic_desktop_pet.qqmusic_online import (
    QQCatalogError, QQMusicCatalog, QQMusicOnlinePlayer, dispatch_song,
)


def row(song_id=123, title='山楂花', singer='陈楚生', **changes):
    value = dict(songid=song_id, songname=title, singer=[{'name': singer}], type=0,
                 albumname='冬去春来')
    value.update(changes)
    return value


def payload(*rows):
    return {'code': 0, 'data': {'song': {'list': list(rows)}}}


def test_catalog_filters_covers_live_versions_types_and_duplicate_ids():
    seen = []
    data = payload(row(), row(), row(456, singer='翻唱者'),
                   row(789, title='山楂花 (Live)'), row(987, type=1), row('songmid'))
    catalog = QQMusicCatalog(lambda url: seen.append(url) or data)
    songs = catalog.search('山楂花', '陈楚生')
    assert [s.identifier for s in songs] == ['123']
    assert 'n=40' in seen[0] and 'w=' in seen[0]


@pytest.mark.parametrize('data', [{'code': 1}, {'code': 0}, payload(None)])
def test_catalog_business_and_schema_errors_are_not_empty_searches(data):
    with pytest.raises(QQCatalogError):
        QQMusicCatalog(lambda _: data).search('', '陈楚生')


def player(track=None, *, data=None, **options):
    dispatched = []
    settings = PetSettings()
    service = QQMusicOnlinePlayer(
        settings, lambda: track,
        catalog=QQMusicCatalog(lambda _: data or payload(row())),
        client_finder=lambda *_: Path('QQMusic.exe'),
        dispatcher=lambda exe, song_id: dispatched.append((exe, song_id)),
        verify_timeout=0, **options)
    return service, settings, dispatched


def test_matched_playing_title_artist_and_album_is_confirmed():
    service, _, sent = player(TrackInfo('山楂花', '陈楚生', '冬去春来', 'playing'))
    result = service.play()
    assert result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
    assert result.current_title == '山楂花'
    assert sent == [(Path('QQMusic.exe'), '123')]


@pytest.mark.parametrize('track', [None,
    TrackInfo('山楂花', '陈楚生', '冬去春来', 'paused'),
    TrackInfo('山楂花 (Live)', '陈楚生', '冬去春来', 'playing'),
    TrackInfo('山楂花', '翻唱者', '冬去春来', 'playing'),
    TrackInfo('山楂花', '陈楚生', '其他版本', 'playing')])
def test_dispatch_alone_or_wrong_metadata_never_confirms_and_never_retries(track):
    service, _, sent = player(track)
    result = service.play()
    assert result.success
    assert result.outcome is MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED
    assert '尚未' in result.message
    assert not result.current_title
    assert len(sent) == result.play_attempts == 1


def test_random_online_bag_does_not_repeat_in_a_round_and_persists_ids():
    service, settings, sent = player(data=payload(row(1), row(2, title='经过'), row(3, title='一夜')))
    for _ in range(3):
        service.play()
    assert {item[1] for item in sent} == {'1', '2', '3'}
    assert all(item.startswith('qq:') for item in settings.music_recent_history)


def test_random_prefers_catalog_tracks_marked_without_subscription():
    service, _, sent = player(data=payload(
        row(1, pay={'payplay': 1}), row(2, title='耍赖', pay={'payplay': 0})))
    service.play()
    assert sent == [(Path('QQMusic.exe'), '2')]


def test_qq_versioned_install_directory_is_detected(tmp_path, monkeypatch):
    import os
    from onepic_desktop_pet import music
    if os.name != 'nt':
        pytest.skip('Windows installation paths')
    for name in ('ProgramFiles', 'ProgramFiles(x86)', 'ONEPIC_MUSIC_LOCALAPPDATA'):
        monkeypatch.setenv(name, str(tmp_path))
    newest = tmp_path / 'Tencent/QQMusic/QQMusic2276.10.06.08/QQMusic.exe'
    older = tmp_path / 'Tencent/QQMusic/QQMusic2275.1/QQMusic.exe'
    for path in (newest, older):
        path.parent.mkdir(parents=True)
        path.touch()
    monkeypatch.setattr(music, '_registered_music_client', lambda _: None)
    assert music.find_music_client('qq') == newest


def test_cancel_after_search_does_not_dispatch():
    cancelled = [False]
    service, _, sent = player(cancelled=lambda: cancelled[0])
    def search(*_):
        cancelled[0] = True
        return (SongCandidate('qq', '山楂花', '陈楚生', identifier='123'),)
    service.catalog.search = search
    assert not service.play().success
    assert sent == []


def test_missing_client_does_not_query_the_network():
    service, _, sent = player()
    service.client_finder = lambda *_: None
    service.catalog.search = lambda *_: pytest.fail('must not search without a client')
    assert not service.play().success
    assert sent == []


def test_native_dispatch_uses_numeric_id_without_shell_or_wait(tmp_path):
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    commands = []
    dispatch_song(executable, '123', popen=lambda args, **kw: commands.append((args, kw)))
    assert commands[0][0] == [str(executable), '/playbysongid', 'cmd_count==1&&id_0==123&&songtype_0==0']
    assert commands[0][1]['shell'] is False
    for bad in ('001abc', '123&&evil', '-1', '0', '9999999999', '１２３'):
        with pytest.raises(ValueError):
            dispatch_song(executable, bad, popen=lambda *_a, **_k: pytest.fail('invalid ID'))
    assert len(commands) == 1


@pytest.mark.parametrize('message,action,title', [
    ('六毛，帮我随机听陈楚生', 'random', ''), ('换一首', 'next', ''),
    ('播放陈楚生的《山楂花》', 'song', '山楂花'), ('暂停音乐', 'pause', ''),
    ('继续播放', 'play', ''), ('下一首', 'next', ''), ('查看音乐状态', 'status', '')])
def test_chat_music_commands(message, action, title):
    request = parse_music_request(message)
    assert (request.action, request.title) == (action, title)


@pytest.mark.parametrize('message', ['暂停', '继续工作', '我在听陈楚生', '陈楚生的山楂花好听吗', '不要播放《山楂花》'])
def test_casual_chat_and_work_controls_do_not_trigger_music(message):
    assert parse_music_request(message) is None


class Bridge:
    def __init__(self, snapshots=()):
        self.snapshots = snapshots
        self.commands = []

    def sessions(self):
        return self.snapshots

    def control(self, source, action):
        self.commands.append((source, action))
        session = self.snapshots[0]
        self.snapshots = (WindowsSessionSnapshot(source,
            TrackInfo(session.track.title, session.track.artist, session.track.album, 'paused'),
            session.controls),)
        return True


def manager(bridge, fallback):
    result = MusicProviderManager(PetSettings(), platform_name='win32', windows_bridge=bridge,
                                 client_finder=lambda *_: None,
                                 media_key_sender=lambda a: fallback.append(a) or True,
                                 transport_wait_timeout=0)
    result.active_provider = 'qq'
    result._online_qq_selected = True
    return result


def test_qq_controls_confirm_pause_in_the_target_session():
    session = WindowsSessionSnapshot('QQMusic.exe', TrackInfo('山楂花', '陈楚生', '', 'playing'), frozenset({'pause'}))
    bridge, fallback = Bridge((session,)), []
    result = manager(bridge, fallback).control('auto', 'pause')
    assert result.success and not result.status.is_playing
    assert bridge.commands == [('QQMusic.exe', 'pause')]
    assert fallback == []


def test_already_paused_is_success_even_when_pause_capability_is_disabled():
    bridge = Bridge((WindowsSessionSnapshot(
        'QQMusic.exe', TrackInfo('鹿回头', '陈楚生', '', 'paused'), frozenset()),))
    result = manager(bridge, []).control('auto', 'pause')
    assert result.success and '已经暂停' in result.message
    assert bridge.commands == []


@pytest.mark.parametrize('sources', [('Spotify.exe',), ('browser-qqmusic.exe',), ('QQMusic.exe', 'QQMusic.exe'), ()])
def test_missing_or_ambiguous_qq_session_never_controls_other_apps(sources):
    bridge = Bridge(tuple(WindowsSessionSnapshot(s, TrackInfo(), frozenset({'pause'})) for s in sources))
    fallback = []
    result = manager(bridge, fallback).control('auto', 'pause')
    assert not result.success
    assert bridge.commands == fallback == []


def test_accepted_command_without_state_change_is_not_reported_as_success():
    session = WindowsSessionSnapshot('QQMusic.exe', TrackInfo('山楂花', '陈楚生', '', 'playing'), frozenset({'pause'}))
    bridge = Bridge((session,))
    bridge.control = lambda *_: True
    result = manager(bridge, []).control('auto', 'pause')
    assert not result.success and '尚未确认' in result.message


def test_http_deadline_bounds_stalled_dns_and_prevents_duplicate_requests(monkeypatch):
    from onepic_desktop_pet import qqmusic_online as module
    release, entered = Event(), Event()
    def stalled(_):
        entered.set()
        assert release.wait(2)
        return payload()
    monkeypatch.setattr(module, '_fetch_json', stalled)
    try:
        with pytest.raises(QQCatalogError, match='超时'):
            module._request_json('https://y.qq.com/', wall_timeout=0.05)
        assert entered.is_set()
        with pytest.raises(QQCatalogError, match='上一项'):
            module._request_json('https://y.qq.com/', wall_timeout=0.05)
    finally:
        release.set()
        # 等候同一把锁释放，确保本测试不污染后续网络场景。
        assert module._HTTP_LOCK.acquire(timeout=1)
        module._HTTP_LOCK.release()


def test_http_worker_propagates_catalog_failures(monkeypatch):
    from onepic_desktop_pet import qqmusic_online as module
    def failed(_):
        raise QQCatalogError('目录测试错误')
    monkeypatch.setattr(module, '_fetch_json', failed)
    with pytest.raises(QQCatalogError, match='目录测试错误'):
        module._request_json('https://y.qq.com/')
