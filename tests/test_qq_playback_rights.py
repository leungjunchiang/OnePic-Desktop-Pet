"""验证 QQ 完整播放位、会员/付费授权与无版权区别，随机和原生队列均不接受未知授权。"""
from dataclasses import replace
import pytest

from onepic_desktop_pet.qqmusic_eligibility import playback_rights, random_song_eligible
from onepic_desktop_pet.qqmusic_online import QQMusicCatalog
from onepic_desktop_pet.qqmusic_native_queue import QQNativeQueue
from onepic_desktop_pet.qqmusic_playlist import bundled_candidates
from onepic_desktop_pet.resources import resource_path
import json
from test_qqmusic_playlist import song
from test_qq_native_queue import metadata
from test_qq_artist_catalog import artist_response
from test_qqmusic_online import row, payload


@pytest.mark.parametrize('bit', [1, 2, 3, 17])
def test_each_official_full_playback_bit_is_eligible(bit):
    assert playback_rights({'action': {'switch': 1 | (1 << bit)}}) is True


@pytest.mark.parametrize('bits', [0, 1, 1 | (1 << 14), 134283265, 65537])
def test_metadata_without_complete_or_paid_playback_is_not_a_licensed_full_song(bits):
    assert playback_rights({'action': {'switch': bits}}) is False


@pytest.mark.parametrize('key', ['pay_play', 'payplay', 'pay_down', 'paydownload'])
def test_subscription_and_purchase_are_not_misclassified_as_no_copyright(key):
    assert playback_rights({'action': {'switch': 1}, 'pay': {key: 1}}) is True
    assert playback_rights({'pay': {key: '1'}}) is True
    assert playback_rights({'action': {'switch': 1}, 'pay': {key: 0}}) is False


@pytest.mark.parametrize('value', [None, {}, {'file': {'size_128mp3': 100}},
                                  {'action': {'switch': -1}}, {'action': {'switch': True}},
                                  {'action': {'switch': 'invalid'}}])
def test_missing_or_invalid_rights_are_unknown_instead_of_licensed(value):
    assert playback_rights(value) is None


def test_legacy_switch_strings_and_explicit_disabled_metadata():
    assert playback_rights({'switch': '3'}) is True
    assert playback_rights({'disabled': True, 'action': {'switch': 3}, 'pay': {'pay_play': 1}}) is False


def test_bad_embedded_rights_override_old_native_boolean_and_live_is_always_excluded():
    candidate = replace(song(1), native={'playback_rights': True, 'track_info': {'action': {'switch': 1}}})
    assert not random_song_eligible(candidate)
    assert not random_song_eligible(replace(song(2), title='歌曲2 (Live)'))
    assert not random_song_eligible(replace(song(2), native={}))
    assert random_song_eligible(song(3, paid=True))
    assert not random_song_eligible(replace(song(3), native={'playback_rights': True, 'album': ''}))


def test_catalog_filters_unlicensed_artist_rows_without_renumbering_and_keeps_vip():
    response = artist_response(count=3, total=3)
    rows = response['req']['data']['songList']
    rows[0]['songInfo'].update(action={'switch': 1}, pay={'pay_play': 0, 'pay_down': 0})
    rows[1]['songInfo'].update(action={'switch': 1}, pay={'pay_play': 1})
    result = QQMusicCatalog(request_json=lambda _: response).artist_candidates('陈楚生')
    assert [s.identifier for s in result] == ['2', '3']
    assert result[0].native['popularity_rank'] == 2 and result[0].native['playback_rights'] is True


def test_legacy_search_filters_explicit_no_copyright_but_keeps_subscription():
    data = payload(row(1, switch=1), row(2, switch=1, pay={'payplay': 1}), row(3, switch=3))
    songs = QQMusicCatalog(request_json=lambda _: data).search('', '陈楚生')
    assert [s.identifier for s in songs] == ['2', '3']
    assert all(s.native['playback_rights'] is True for s in songs)


@pytest.mark.parametrize('rights', [{'action': {'switch': 1}}, {}])
def test_native_metadata_validation_rejects_unlicensed_or_unknown_before_commands(rights):
    item = song(1)
    track = metadata(item)
    track.pop('action')
    track.update(rights)
    with pytest.raises(ValueError):
        QQNativeQueue._validated(item, track)


def test_native_metadata_validation_accepts_vip_without_free_play_bits():
    item = song(1, paid=True)
    track = metadata(item)
    track.update(action={'switch': 1}, pay={'pay_play': 1})
    assert QQNativeQueue._validated(item, track) is track


def test_bundled_studio_fallback_is_complete_licensed_and_retains_subscription_tracks():
    data = json.loads(resource_path('resources/music/qq-chenchusheng-studio.json').read_text(encoding='utf-8'))
    ids = {str(s['id']) for s in data['songs']}
    assert data['checked'] == '2026-10-24' and len(ids) == 60
    items = [s for s in bundled_candidates() if s.identifier in ids]
    assert len(items) == 60 and all(random_song_eligible(s) for s in items)
    assert any(s.native['requires_subscription'] for s in items)
    for item in items:
        QQNativeQueue._validated(item, item.native['track_info'])


def test_old_saved_round_without_rights_can_be_viewed_but_not_dispatched(tmp_path):
    from onepic_desktop_pet.qqmusic_playlist import QQPlaylist
    from test_qqmusic_playlist import Player
    original = QQPlaylist(Player(), store_path=tmp_path / 'list.json')
    assert original.create().success
    data = json.loads(original.store_path.read_text(encoding='utf-8'))
    for entry in data['songs']:
        entry.pop('playback_rights')
    original.store_path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    player = Player()
    restored = QQPlaylist(player, store_path=original.store_path)
    assert len(restored.entries) == 30 and not restored.auto_advance
    assert not restored.select(0).success and not player.sent
    assert restored.create().success and len(restored.entries) == 30
