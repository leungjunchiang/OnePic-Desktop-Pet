"""验证每轮现场版最多六首，涵盖两池共享额度、版本选择、缓存换轮与失败保留。"""

from dataclasses import replace

import pytest

from onepic_desktop_pet.qqmusic_playlist import QQPlaylist, is_live_song, song_title_key
from test_qqmusic_playlist import Player, song


@pytest.mark.parametrize('title,album,track,expected', [
    ('歌曲 (Live)', '专辑', {}, True),
    ('歌曲（lIvE版）', '专辑', {}, True),
    ('歌曲 (现场版)', '专辑', {}, True),
    ('歌曲', '陈楚生巡回演唱會', {}, True),
    ('歌曲', '巡演记录', {}, True),
    ('歌曲', 'Concert 2024', {}, True),
    ('歌曲', 'Livehouse', {}, True),
    ('歌曲', '', {'subtitle': '音樂會現場'}, True),
    ('歌曲', '', {'album': {'title': 'Live in Beijing'}}, True),
    ('Alive', 'Delivered', {}, False),
    ('歌曲', '录音室专辑', {}, False),
    ('歌曲', None, {'album': None, 'subtitle': None}, False),
])
def test_explicit_live_markers_in_titles_albums_and_original_metadata(title, album, track, expected):
    candidate = replace(song(1, title=title), native={'album': album, 'track_info': track})
    assert is_live_song(candidate) is expected


def live(candidate):
    return replace(candidate, title=candidate.title + ' (Live)')


def set_catalog(player, rows):
    player.catalog.artist_candidates = lambda *_a, **_k: tuple(rows)


@pytest.mark.parametrize('hot_live', range(7))
def test_hot_and_general_halves_share_six_slots_including_the_boundary(hot_live):
    player = Player()
    hot = [live(song(i)) if i <= hot_live else song(i) for i in range(1, 16)]
    general = [live(song(i)) if i < 71 else song(i) for i in range(51, 101)]
    set_catalog(player, hot + general)
    value = QQPlaylist(player, shuffle=lambda _: None, native_queue=object())
    assert value.create().success
    assert sum(is_live_song(s) for s in value.entries) == 6
    assert len({song_title_key(s.title) for s in value.entries}) == 30
    assert len({s.identifier for s in value.entries}) == 30
    assert sum(song_title_key(s.title) in value._popular_keys for s in value.entries) == 15
    # The same rule applies when the last native queue item completes a round.
    player.catalog.artist_candidates = lambda *_a, **_k: pytest.fail('Cached round must not query QQ')
    player.catalog.search = player.catalog.artist_candidates
    assert value.create(reuse_catalog=True).success
    assert sum(is_live_song(s) for s in value.entries) == 6


def test_exactly_twenty_four_studio_and_six_live_can_fill_the_round():
    player = Player()
    hot = [live(song(i)) if i <= 12 else song(i) for i in range(1, 22)]
    rest = [song(i) for i in range(51, 66)]
    set_catalog(player, hot + rest)
    value = QQPlaylist(player, shuffle=lambda _: None)
    assert value.create().success
    assert len(value.entries) == 30 and sum(is_live_song(s) for s in value.entries) == 6


@pytest.mark.parametrize('insufficient_hot', [False, True])
def test_infeasible_cap_preserves_existing_playlist_on_disk_without_dispatch(tmp_path, insufficient_hot):
    player = Player()
    value = QQPlaylist(player, store_path=tmp_path / 'list.json', shuffle=lambda _: None)
    assert value.create().success
    old, stored, sent = value.entries, value.store_path.read_bytes(), len(player.sent)
    # Either there are only 23 studio songs, or popular songs alone require 7 Live.
    hot = [live(song(i)) if insufficient_hot and i <= 7 else song(i) for i in range(1, 16)]
    rest = [song(i) if i < (81 if insufficient_hot else 59) else live(song(i)) for i in range(51, 101)]
    set_catalog(player, hot + rest)
    result = value.create()
    assert not result.success and '现场版最多 6 首' in result.message
    assert value.entries == old and value.store_path.read_bytes() == stored
    assert len(player.sent) == sent


def test_equal_subscription_versions_prefer_studio_without_promoting_a_paid_copy():
    player = Player()
    set_catalog(player, [live(song(1)), live(song(2)), *(song(i) for i in range(3, 41)),
                        song(101, title='歌曲1'), song(102, title='歌曲2', paid=True)])
    value = QQPlaylist(player, shuffle=lambda _: None)
    assert value.create().success
    selected = {song_title_key(s.title): s for s in value.entries}
    assert selected['歌曲1'].identifier == '101' and not is_live_song(selected['歌曲1'])
    assert selected['歌曲2'].identifier == '2' and not value._paid(selected['歌曲2'])


def test_old_saved_round_can_be_viewed_without_autoplay_then_regenerated_with_the_cap(tmp_path):
    value = QQPlaylist(Player(), store_path=tmp_path / 'list.json', shuffle=lambda _: None)
    assert value.create().success
    value.entries = tuple(live(candidate) for candidate in value.entries)
    value.save()
    restored = QQPlaylist(Player(), store_path=value.store_path, shuffle=lambda _: None)
    assert restored.entries == value.entries and not restored.auto_advance
    assert restored.create().success
    assert sum(is_live_song(s) for s in restored.entries) <= 6

