"""验证公开歌手目录分页、完整版本元数据缓存与原生队列直接复用。"""
from dataclasses import replace
from urllib.parse import parse_qs, urlparse
import json

import pytest

from onepic_desktop_pet.qqmusic_online import QQMusicCatalog, QQCatalogError
from onepic_desktop_pet.qqmusic_native_queue import QQNativeQueue
from test_qq_native_queue import metadata
from test_qqmusic_playlist import song


def artist_response(begin=0, count=80, total=81):
    rows = []
    for i in range(begin + 1, begin + count + 1):
        row = metadata(song(i, title=f'曲目{i} (Live)'))
        row['name'] = f'曲目{i}'
        row['title'] = f'曲目{i} (Live)'
        row['pay'] = {'pay_play': i % 2}
        rows.append({'songInfo': row})
    return {'code': 0, 'req': {'code': 0, 'data': {
        'singerMid': '002PZBgg1S9xPX', 'totalNum': total, 'songList': rows}}}


def test_complete_artist_pages_and_cache_keep_live_versions_and_ids():
    requested = []
    def fetch(url):
        query = json.loads(parse_qs(urlparse(url).query)['data'][0])['req']
        assert query['method'] == 'GetSingerSongList'
        begin = query['param']['begin']
        requested.append(begin)
        return artist_response(begin, 80 if begin == 0 else 1)
    catalog = QQMusicCatalog(request_json=fetch)
    songs = catalog.artist_candidates('陈楚生')
    assert len(songs) == 81 and requested == [0, 80]
    assert songs[0].title == '曲目1 (Live)' and songs[0].native['requires_subscription']
    assert songs[-1].identifier == '81' and songs[-1].native['track_info']['id'] == 81
    assert catalog.artist_candidates('陈楚生') is songs and requested == [0, 80]
    with pytest.raises(QQCatalogError, match='取消'):
        catalog.artist_candidates('陈楚生', cancelled=lambda: True)


def test_wrong_artist_or_malformed_page_never_becomes_catalog_cache():
    response = artist_response(count=2, total=2)
    response['req']['data']['songList'][0]['songInfo']['singer'] = [{'name': '翻唱者'}]
    catalog = QQMusicCatalog(request_json=lambda _: response)
    assert [s.identifier for s in catalog.artist_candidates('陈楚生')] == ['2']
    response['req']['data']['singerMid'] = 'other-artist'
    with pytest.raises(QQCatalogError, match='格式'):
        QQMusicCatalog(request_json=lambda _: response).artist_candidates('陈楚生')


def test_accompaniment_does_not_replace_vocal_recordings_with_free_backing_tracks():
    response = artist_response(count=3, total=3)
    rows = response['req']['data']['songList']
    rows[0]['songInfo']['title'] = '经过 (伴奏)'
    rows[1]['songInfo']['title'] = '经过 (Instrumental)'
    rows[2]['songInfo']['title'] = '经过 (Live)'
    songs = QQMusicCatalog(request_json=lambda _: response).artist_candidates('陈楚生')
    assert [s.title for s in songs] == ['经过 (Live)']


def test_native_queue_reuses_validated_artist_metadata_and_refuses_wrong_embedded_row(tmp_path):
    entries = QQMusicCatalog(request_json=lambda _: artist_response(count=30, total=30)).artist_candidates('陈楚生')
    commands = []
    native = QQNativeQueue(request_json=lambda _: pytest.fail('Artist list already contains complete metadata'),
                           popen=lambda *a, **k: commands.append(a))
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    native.dispatch(executable, entries, 7)
    assert len(commands) == 1 and len(native._rows) == 30
    bad = replace(entries[0], title='不匹配的版本')
    with pytest.raises(OSError, match='不匹配'):
        native.dispatch(executable, (bad,), 0)
    assert len(commands) == 1
