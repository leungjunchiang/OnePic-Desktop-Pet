"""验证两池各十五首、整轮去重、会员同等随机及非现场授权快照降级。"""
from dataclasses import replace
import json

from onepic_desktop_pet.qqmusic_playlist import QQPlaylist, bundled_candidates, is_live_song, song_title_key
from onepic_desktop_pet.qqmusic_online import QQMusicCatalog
from onepic_desktop_pet.qqmusic_native_queue import QQNativeQueue
from onepic_desktop_pet.resources import resource_path
from test_qqmusic_playlist import Player, song
from test_qq_artist_catalog import artist_response


def mixed_player():
    player = Player()
    player.catalog.artist_candidates = lambda *_a, **_k: tuple(song(i, paid=i==100) for i in range(1,101))
    return player


def test_exactly_fifteen_hot_plus_fifteen_remaining_does_not_penalize_subscription_songs():
    calls = []
    def shuffle(rows):
        calls.append(len(rows))
        if len(rows) == 85:
            rows.reverse()
    value = QQPlaylist(mixed_player(), shuffle=shuffle)
    assert value.create().success
    ids = {s.identifier for s in value.entries}
    assert calls == [50,85,30]
    assert ids == {str(i) for i in range(1,16)} | {str(i) for i in range(86,101)}
    assert len(ids) == len({song_title_key(s.title) for s in value.entries}) == 30
    assert '100' in ids and any(value._paid(s) for s in value.entries)


def test_full_catalog_half_can_include_other_hot_songs_and_rounds_may_repeat():
    value = QQPlaylist(mixed_player(), shuffle=lambda _:None)
    assert value.create().success
    first = value.entries
    assert {s.identifier for s in first} == {str(i) for i in range(1,31)}
    assert value.create(reuse_catalog=True).success and value.entries==first


def test_duplicate_versions_and_ids_cannot_appear_in_both_halves():
    player = mixed_player()
    original = player.catalog.artist_candidates
    player.catalog.artist_candidates = lambda *_a, **_k: original() + (song(150,title='歌曲1 (Live)'), song(1,title='别名'))
    value = QQPlaylist(player, shuffle=lambda _:None)
    assert value.create().success
    assert len({s.identifier for s in value.entries}) == 30
    assert len({song_title_key(s.title) for s in value.entries}) == 30


def test_cannot_promote_rank_fifty_one_to_fill_an_insufficient_hot_quota():
    player = mixed_player()
    value = QQPlaylist(player, shuffle=lambda _:None)
    assert value.create().success
    old, sent = value.entries, len(player.sent)
    player.catalog.artist_candidates = lambda *_a, **_k: tuple(song(i) for i in (*range(1,15),*range(51,100)))
    player.pages = {1:()}
    result=value.create()
    assert not result.success and '不足 15' in result.message
    assert value.entries==old and len(player.sent)==sent


def test_live_ranking_replaces_old_snapshot_and_automatic_round_does_not_request_again():
    player=mixed_player()
    stale=replace(song(100),native={'album':'专辑','popularity_rank':1})
    value=QQPlaylist(player,shuffle=lambda _:None,fallback_loader=lambda:(stale,),native_queue=object())
    assert value.create().success
    assert value._popular_keys == {song_title_key(f'歌曲{i}') for i in range(1,51)}
    assert not value._popular_snapshot
    player.catalog.artist_candidates=lambda *_a,**_k: (_ for _ in ()).throw(AssertionError('No round-end HTTP'))
    player.catalog.search=player.catalog.artist_candidates
    assert value.create(reuse_catalog=True).success and not value._popular_snapshot


def test_original_ranks_survive_filtered_rows_instead_of_renumbering():
    response=artist_response(count=60,total=60)
    response['req']['data']['songList'][0]['songInfo']['title']='片段试听'
    catalog=QQMusicCatalog(request_json=lambda _:response)
    songs=catalog.artist_candidates('陈楚生')
    assert songs[0].native['popularity_rank']==2 and songs[49].native['popularity_rank']==51
    assert len(QQPlaylist._top_fifty_keys(songs))==49


def test_bundled_hot_snapshot_contains_only_checked_top_fifty_complete_metadata():
    raw=json.loads(resource_path('resources/music/qq-chenchusheng-hot.json').read_text(encoding='utf-8'))
    assert raw['checked']=='2026-10-24' and len(raw['songs'])==50
    pool=bundled_candidates()
    hot=[s for s in pool if type((s.native or {}).get('popularity_rank')) is int]
    assert len(hot)==50 and all(1<=s.native['popularity_rank']<=50 for s in hot)
    for candidate in hot:
        QQNativeQueue._validated(candidate,candidate.native['track_info'])
    player=Player({1:()})
    value=QQPlaylist(player,fallback_loader=bundled_candidates,shuffle=lambda _:None)
    assert value.create().success and value._popular_snapshot
    assert '随包热歌快照' in value.create().message
    assert len({song_title_key(s.title) for s in value.entries})==30
    assert not any(is_live_song(s) for s in value.entries)
