"""验证公开目录分页、跨重启十分钟缓存、失效回源和完整元数据复用。"""
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


def test_explicit_thirty_second_preview_and_excerpt_are_not_random_full_songs():
    response = artist_response(count=3, total=3)
    rows = response['req']['data']['songList']
    rows[0]['songInfo']['title'] = '空 (30秒试听版)'
    rows[1]['songInfo']['title'] = '一夜 (Preview)'
    rows[2]['songInfo']['title'] = '一夜 (Live)'
    assert [s.title for s in QQMusicCatalog(request_json=lambda _: response).artist_candidates('陈楚生')] == ['一夜 (Live)']


def test_short_greetings_and_unlabelled_clips_are_not_random_full_songs():
    response = artist_response(count=3, total=3)
    rows = response['req']['data']['songList']
    rows[0]['songInfo'].update(title='和陈楚生一起拜年啦', interval=20)
    rows[1]['songInfo'].update(title='即兴片段', interval=70)
    rows[2]['songInfo'].update(title='山高水长', interval=219)
    assert [s.title for s in QQMusicCatalog(request_json=lambda _: response).artist_candidates('陈楚生')] == ['山高水长']


def test_native_queue_reuses_validated_artist_metadata_and_refuses_wrong_embedded_row(tmp_path):
    entries = QQMusicCatalog(request_json=lambda _: artist_response(count=30, total=30)).artist_candidates('陈楚生')
    commands = []
    native = QQNativeQueue(request_json=lambda _: pytest.fail('Artist list already contains complete metadata'),
                           popen=lambda *a, **k: commands.append(a), running_check=lambda _: None)
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    native.dispatch(executable, entries, 7)
    assert len(commands) == 1 and len(native._rows) == 30
    bad = replace(entries[0], title='不匹配的版本')
    with pytest.raises(OSError, match='不匹配'):
        native.dispatch(executable, (bad,), 0)
    assert len(commands) == 1


def test_persistent_catalog_reuses_identical_pages_and_ranks_without_http_after_restart(tmp_path):
    path = tmp_path/'music'/'qq-catalog.json'
    requested = []
    def fetch(url):
        begin = json.loads(parse_qs(urlparse(url).query)['data'][0])['req']['param']['begin']
        requested.append(begin)
        return artist_response(begin, 80 if begin == 0 else 1)
    catalog = QQMusicCatalog(fetch, cache_path=path, wall_clock=lambda:1000)
    songs = catalog.artist_candidates('陈楚生')
    assert requested == [0, 80] and path.is_file()
    restored = QQMusicCatalog(lambda _:pytest.fail('Fresh disk catalog should not query'),
                              cache_path=path, wall_clock=lambda:1100)
    assert restored.artist_candidates('陈楚生') == songs
    assert restored.artist_candidates('陈楚生')[-1].native['popularity_rank'] == 81
    assert not list(path.parent.glob('.qq-catalog-*'))
    expired = QQMusicCatalog(fetch, cache_path=path, wall_clock=lambda:1600)
    assert expired.artist_candidates('陈楚生') == songs and requested == [0, 80, 0, 80]


@pytest.mark.parametrize('damage', ['json', 'partial', 'foreign', 'future', 'row'])
def test_corrupt_incomplete_foreign_or_future_disk_cache_refetches(tmp_path, damage):
    path = tmp_path/'catalog.json'
    QQMusicCatalog(lambda _:artist_response(count=30, total=30),
                   cache_path=path, wall_clock=lambda:1000).artist_candidates('陈楚生')
    data = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'json':
        path.write_text('{broken', encoding='utf-8')
    else:
        if damage == 'partial':
            data['pages'][0]['response']['req']['data']['totalNum'] = 81
        elif damage == 'foreign':
            data['pages'][0]['response']['req']['data']['singerMid'] = 'another-singer'
        elif damage == 'row':
            del data['pages'][0]['response']['req']['data']['songList'][0]['songInfo']['album']
        else:
            data['saved_at'] = 99999
        path.write_text(json.dumps(data), encoding='utf-8')
    queried = []
    restored = QQMusicCatalog(lambda _:queried.append(True) or artist_response(count=30,total=30),
                             cache_path=path, wall_clock=lambda:1100)
    assert len(restored.artist_candidates('陈楚生')) == 30 and queried == [True]


def test_unwritable_cache_is_optional_and_cancelled_disk_lookup_never_queries(tmp_path):
    invalid = tmp_path/'directory'
    invalid.mkdir()
    catalog = QQMusicCatalog(lambda _:artist_response(count=30,total=30), cache_path=invalid)
    assert len(catalog.artist_candidates('陈楚生')) == 30
    assert not list(tmp_path.glob('.qq-catalog-*'))
    cancelled = QQMusicCatalog(lambda _:pytest.fail('Cancelled request'), cache_path=invalid)
    with pytest.raises(QQCatalogError, match='取消'):
        cancelled.artist_candidates('陈楚生', cancelled=lambda:True)
