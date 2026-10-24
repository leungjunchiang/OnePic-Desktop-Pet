"""验证随机完全排除现场、无版权和授权未知，保留会员曲及旧歌单可查看但不投递。"""

from dataclasses import replace

import pytest

from onepic_desktop_pet.qqmusic_playlist import QQPlaylist, is_live_song, song_title_key
from onepic_desktop_pet.qqmusic_eligibility import random_song_eligible
from test_qqmusic_playlist import Player, song


@pytest.mark.parametrize('title,album,track,expected', [
    ('歌曲 (Live)', '专辑', {}, True),
    ('歌曲（lIvE版）', '专辑', {}, True),
    ('歌曲（ＬＩＶＥ）', '专辑', {}, True),
    ('歌曲 (现场版)', '专辑', {}, True),
    ('歌曲', '陈楚生巡回演唱會', {}, True),
    ('歌曲', '巡演记录', {}, True),
    ('歌曲', 'Concert 2024', {}, True),
    ('歌曲', 'Livehouse', {}, True),
    ('歌曲 (新年首唱版)', '', {}, True),
    ('歌曲 (慈善晚會)', '', {}, True),
    ('歌曲', '十二乐章 第2期', {}, True),
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


def test_hot_and_general_halves_and_cached_rounds_exclude_every_live_recording():
    player = Player()
    hot = [live(song(i)) if i <= 15 else song(i, paid=True) for i in range(1, 51)]
    general = [live(song(i)) if i < 71 else song(i) for i in range(51, 101)]
    set_catalog(player, hot + general)
    value = QQPlaylist(player, shuffle=lambda _: None, native_queue=object())
    assert value.create().success
    assert not any(is_live_song(s) for s in value.entries)
    assert all(random_song_eligible(s) for s in value.entries)
    assert any(value._paid(s) for s in value.entries)
    assert len({song_title_key(s.title) for s in value.entries}) == 30
    assert len({s.identifier for s in value.entries}) == 30
    assert sum(song_title_key(s.title) in value._popular_keys for s in value.entries) >= 15
    # The same rule applies when the last native queue item completes a round.
    player.catalog.artist_candidates = lambda *_a, **_k: pytest.fail('Cached round must not query QQ')
    player.catalog.search = player.catalog.artist_candidates
    assert value.create(reuse_catalog=True).success
    assert not any(is_live_song(s) for s in value.entries)


def test_exactly_thirty_studio_songs_fill_the_round_without_using_live_extras():
    player = Player()
    hot = [live(song(i)) if i <= 12 else song(i) for i in range(1, 28)]
    rest = [song(i) for i in range(51, 66)]
    set_catalog(player, hot + rest)
    value = QQPlaylist(player, shuffle=lambda _: None)
    assert value.create().success
    assert len(value.entries) == 30 and not any(is_live_song(s) for s in value.entries)


@pytest.mark.parametrize('insufficient_hot', [False, True])
def test_insufficient_studio_material_preserves_existing_playlist_on_disk_without_dispatch(tmp_path, insufficient_hot):
    player = Player()
    value = QQPlaylist(player, store_path=tmp_path / 'list.json', shuffle=lambda _: None)
    assert value.create().success
    old, stored, sent = value.entries, value.store_path.read_bytes(), len(player.sent)
    hot = [live(song(i)) if insufficient_hot and i == 1 else song(i) for i in range(1, 16)]
    rest = [song(i) if i < (81 if insufficient_hot else 65) else live(song(i)) for i in range(51, 101)]
    set_catalog(player, hot + rest)
    player.pages = {1: ()}
    result = value.create()
    assert not result.success and ('不足 15' in result.message if insufficient_hot else '未满 30' in result.message)
    assert value.entries == old and value.store_path.read_bytes() == stored
    assert len(player.sent) == sent


def test_live_cannot_hide_a_paid_studio_version_of_the_same_song():
    player = Player()
    set_catalog(player, [live(song(1)), live(song(2)), *(song(i) for i in range(3, 31)),
                        song(101, title='歌曲1'), song(102, title='歌曲2', paid=True)])
    value = QQPlaylist(player, shuffle=lambda _: None)
    assert value.create().success
    selected = {song_title_key(s.title): s for s in value.entries}
    assert selected['歌曲1'].identifier == '101' and not is_live_song(selected['歌曲1'])
    assert selected['歌曲2'].identifier == '102' and value._paid(selected['歌曲2'])


def test_old_live_round_can_be_viewed_without_autoplay_but_cannot_be_dispatched(tmp_path):
    value = QQPlaylist(Player(), store_path=tmp_path / 'list.json', shuffle=lambda _: None)
    assert value.create().success
    value.entries = tuple(live(candidate) for candidate in value.entries)
    value.save()
    restored = QQPlaylist(Player(), store_path=value.store_path, shuffle=lambda _: None)
    assert restored.entries == value.entries and not restored.auto_advance
    assert not restored.select(0).success and not restored.player.sent
    assert restored.create().success
    assert not any(is_live_song(s) for s in restored.entries)

