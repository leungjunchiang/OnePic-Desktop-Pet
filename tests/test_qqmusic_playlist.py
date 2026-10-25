"""验证非现场三十首混合、曲尾暂停优先、真实曲终换轮与界面控制。"""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from onepic_desktop_pet.music_control import MusicProviderManager, TrackInfo, WindowsSessionSnapshot
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_playback import MusicPlaybackOutcome, SongCandidate, SongPlaybackResult
from onepic_desktop_pet.qqmusic_online import QQCatalogError
from onepic_desktop_pet.qqmusic_playlist import QQPlaylist, bundled_candidates


def song(i, *, paid=False, title=None):
    return SongCandidate('qq', title or f'歌曲{i}', '陈楚生', identifier=str(i),
                         native={'album': '专辑', 'requires_subscription': paid, 'popularity_rank': i,
                                 'playback_rights': True})


class Player:
    def __init__(self, pages=None):
        self.settings = SimpleNamespace(qq_music_path='')
        self.client_finder = lambda *_: Path('QQMusic.exe')
        self.cancelled = lambda: False
        self.sleep = lambda _: None
        self.pages = pages or {1: tuple(song(i) for i in range(1, 41))}
        self.searched, self.sent = [], []
        self.catalog = SimpleNamespace(search=self.search)
        self.fail = False

    def search(self, title, artist, *, page):
        self.searched.append(page)
        return self.pages.get(page, ())

    def play_candidate(self, selected, **_):
        self.sent.append(selected.identifier)
        return SongPlaybackResult(not self.fail, 'qq', '', '陈楚生', 'test', selected=selected,
            outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED if not self.fail else None)


def queue(tmp_path=None, player=None):
    player = player or Player()
    value = QQPlaylist(player, store_path=tmp_path / 'list.json' if tmp_path else None,
                       shuffle=lambda entries: entries.reverse())
    value.test_time = [1000.0]
    value.clock = lambda: value.test_time[0]
    assert value.create().success
    return value, player


def track(queue, state='playing', position=1000, duration=200000):
    current = queue.entries[queue.index]
    return TrackInfo(current.title, current.artist, '专辑', state, position, duration)


def test_create_exactly_thirty_unique_songs_and_next_previous_follow_saved_order(tmp_path):
    value, player = queue(tmp_path)
    expected = [s.identifier for s in value.entries]
    assert len(set(expected)) == len(expected) == 30
    assert player.sent == [expected[0]]
    assert value.move(1).playlist_index == 1
    assert value.move(1).playlist_index == 2
    assert value.move(-1).playlist_index == 1
    assert player.sent == [expected[0], expected[1], expected[2], expected[1]]
    assert player.searched == [1]  # 切歌不重新搜索、随机或使用旧 QQ 队列。
    restored = QQPlaylist(player, store_path=tmp_path / 'list.json')
    assert [s.identifier for s in restored.entries] == expected
    assert restored.index == 1 and not restored.auto_advance
    restored.move(1)
    assert player.sent[-1] == expected[2]


def test_collects_pages_deduplicates_versions_and_keeps_subscription_songs():
    player = Player({1: tuple(song(i, paid=True) for i in range(1, 21)),
                     2: tuple(song(i, paid=True) for i in range(11, 41))})
    value, _ = queue(player=player)
    assert player.searched == [1, 2]
    assert all(value._paid(s) for s in value.entries)
    assert len({s.identifier for s in value.entries}) == 30


def test_partial_catalog_and_network_failure_keep_previous_playlist():
    value, player = queue()
    before = value.entries
    player.pages = {1: (song(99),)}
    assert not value.create().success
    assert value.entries == before and len(player.sent) == 1
    def fail(*_, **__):
        raise QQCatalogError('network test')
    player.catalog.search = fail
    assert not value.create().success
    assert value.entries == before


def test_cancelled_or_missing_client_does_not_dispatch():
    value, player = queue()
    player.cancelled = lambda: True
    assert not value.create().success
    player.client_finder = lambda *_: None
    assert not value.create().success
    assert len(player.sent) == 1


def test_failed_selection_keeps_position_but_new_random_saves_its_prepared_queue(tmp_path):
    value, player = queue(tmp_path)
    before = (tmp_path / 'list.json').read_bytes()
    player.fail = True
    assert not value.move(1).success
    assert (tmp_path / 'list.json').read_bytes() == before
    result=value.create()
    assert not result.success and result.playlist_created and result.playlist_size == 30
    assert value.index == 0
    restored=QQPlaylist(player,store_path=tmp_path/'list.json')
    assert restored.entries == value.entries and restored.round_number == 2
    assert not restored.auto_advance


def test_fresh_computer_keeps_thirty_on_disk_even_when_first_play_fails(tmp_path):
    player=Player()
    player.fail=True
    path=tmp_path/'new-computer'/'music'/'qq-playlist.json'
    value=QQPlaylist(player,store_path=path,shuffle=lambda _:None)
    assert not path.exists() and not value.entries
    normal_play=player.play_candidate
    def play(selected,**kwargs):
        saved=json.loads(path.read_text(encoding='utf-8'))
        assert len(saved['songs'])==30 and saved['songs'][0]['id']==selected.identifier
        return normal_play(selected,**kwargs)
    player.play_candidate=play
    result=value.create()
    assert not result.success and result.playlist_created and result.playlist_size==30
    restored=QQPlaylist(player,store_path=path)
    assert len(restored.entries)==30 and restored.entries==value.entries and not restored.auto_advance
    player.play_candidate=normal_play
    player.fail=False
    searches=player.searched[:]
    assert restored.select(0).success and player.searched==searches
    assert player.sent[-1]==restored.entries[0].identifier


def test_previous_at_start_stops_but_next_at_end_creates_fresh_round():
    value, player = queue()
    assert not value.move(-1).success
    value.index = 29
    result = value.move(1)
    assert result.success and result.playlist_round == 2 and result.playlist_created
    assert value.index == 0 and len(value.entries) == 30
    assert len(player.sent) == 2


def test_manual_pause_and_early_external_change_never_auto_resume():
    value, _ = queue()
    assert not value.ended(track(value, position=199000))
    assert not value.ended(track(value, 'paused', position=199000))
    assert not value.auto_advance
    value.auto_advance = True
    assert not value.ended(track(value, position=10000))
    assert not value.ended(TrackInfo('外部歌曲', '其他人', '', 'playing'))
    assert not value.auto_advance


def test_natural_completion_after_near_end_advances_but_unknown_duration_does_not():
    value, _ = queue()
    assert not value.ended(track(value, duration=0))
    assert not value.ended(track(value, 'stopped'))
    assert not value.ended(track(value, position=199000))
    assert not value.ended(track(value, 'stopped', position=200000))
    value.test_time[0] += 1.3
    assert value.ended(track(value, 'stopped', position=200000))


def test_client_paused_at_actual_end_advances_but_explicit_pause_never_does():
    value, _ = queue()
    assert not value.ended(track(value, position=199000))
    assert not value.ended(track(value, 'paused', position=200000))
    value.test_time[0] += 1.3
    assert value.ended(track(value, 'paused', position=200000))
    value.auto_advance = False  # 六毛的优先暂停在系统状态返回前已经关闭续播。
    assert not value.ended(track(value, 'paused', position=200000))


@pytest.mark.parametrize('remaining', [1, 100, 200, 2000])
def test_mac_style_pause_before_exact_end_never_advances_after_wall_clock_deadline(remaining):
    value, player = queue()
    assert not value.ended(track(value, position=200000-remaining))
    assert not value.ended(track(value, 'paused', position=200000-remaining))
    value.test_time[0] += 60
    assert not value.ended(track(value, 'paused', position=200000-remaining))
    assert not value.auto_advance and len(player.sent) == 1


def test_manager_next_never_sends_native_next_or_global_media_key():
    value, player = queue()
    forbidden = lambda *_: (_ for _ in ()).throw(AssertionError('must use playlist IDs'))
    manager = MusicProviderManager(PetSettings(), platform_name='win32',
        windows_bridge=SimpleNamespace(control=forbidden, sessions=forbidden),
        media_key_sender=forbidden, client_finder=lambda *_: None)
    manager.qq_playlist = value
    result = manager.control('auto', 'next')
    assert result.playlist_index == 1
    assert player.sent[-1] == value.entries[1].identifier


def test_manager_natural_end_advances_once_and_failure_disarms():
    value, player = queue()
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_: None)
    manager.qq_playlist = value
    manager._control_qq_online = lambda action: None
    manager._qq_online_track = lambda: track(value, position=199000)
    assert manager.control('auto', 'playlist_tick') is None
    manager._qq_online_track = lambda: track(value, 'stopped', 200000)
    player.fail = True
    assert manager.control('auto', 'playlist_tick') is None
    value.test_time[0] += 1.3
    assert not manager.control('auto', 'playlist_tick').success
    assert not value.auto_advance
    assert manager.control('auto', 'playlist_tick') is None
    assert len(player.sent) == 2


def test_every_random_click_rebuilds_thirty_and_can_repeat_the_first_song(tmp_path):
    value, player = queue(tmp_path)
    first = player.sent[-1]
    result = value.create()
    assert result.playlist_created and result.playlist_size == 30
    assert result.playlist_round == value.round_number == 2
    assert value.index == 0 and player.sent[-1] == first
    assert len({s.identifier for s in value.entries}) == 30
    restored = QQPlaylist(player, store_path=tmp_path / 'list.json')
    assert restored.round_number == 2 and not restored.auto_advance


def test_thirty_natural_ends_generate_another_round_and_pause_stops_all_advancement():
    value, player = queue()
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_: None)
    manager.qq_playlist = value
    for expected in range(1, 31):
        manager._qq_online_track = lambda: track(value, position=199000)
        assert manager.control('auto', 'playlist_tick') is None
        manager._qq_online_track = lambda: track(value, 'stopped', 200000)
        assert manager.control('auto', 'playlist_tick') is None
        value.test_time[0] += 1.3
        result = manager.control('auto', 'playlist_tick')
        assert result.success
        assert value.index == expected % 30
        assert value.round_number == (2 if expected == 30 else 1)
    assert len(value.entries) == 30 and len(player.sent) == 31
    assert value.auto_advance and not result.playlist_created
    assert player.sent[-1] != player.sent[-2]
    manager._qq_online_track = lambda: track(value, 'paused')
    assert manager.control('auto', 'playlist_tick') is None
    count = len(player.sent)
    for _ in range(3):
        assert manager.control('auto', 'playlist_tick') is None
    assert len(player.sent) == count and not value.auto_advance


def test_failed_new_round_stops_and_keeps_the_new_thirty_ready_for_retry():
    value, player = queue()
    value.index = 29
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_: None)
    manager.qq_playlist = value
    pauses = []
    manager._control_qq_online = pauses.append
    manager._qq_online_track = lambda: track(value, position=199000)
    manager.control('auto', 'playlist_tick')
    manager._qq_online_track = lambda: track(value, 'stopped', 200000)
    assert manager.control('auto', 'playlist_tick') is None
    value.test_time[0] += 1.3
    player.fail = True
    assert not manager.control('auto', 'playlist_tick').success
    assert len(value.entries) == 30 and value.round_number == 2 and value.index == 0
    assert not value.auto_advance and pauses == ['pause']


def test_extrapolated_end_cannot_arm_or_cut_a_song_with_raw_position_in_middle():
    value, player = queue()
    optimistic = replace(track(value, position=200000), raw_position_ms=70000)
    assert not value.ended(optimistic)
    assert not value.ended(TrackInfo('另一首', '陈楚生', '', 'playing'))
    value.test_time[0] += 10
    assert not value.ended(TrackInfo('另一首', '陈楚生', '', 'playing'))
    assert len(player.sent) == 1


def test_transient_metadata_and_stopped_status_do_not_trigger_early_next():
    value, _ = queue()
    assert not value.ended(track(value, position=197600))
    changed = TrackInfo('短暂新标题', '陈楚生', '', 'playing')
    assert not value.ended(changed)
    value.test_time[0] += 1.3
    assert not value.ended(changed)  # Stable metadata still cannot shorten the last 2.4s.
    assert not value.ended(track(value, position=198900))  # Original song returns.
    value.test_time[0] += 1.3
    assert not value.ended(track(value, 'stopped', position=198900))  # A buffer/stop in the tail.
    assert not value.ended(track(value, position=200000))  # Playing alone is never completion.
    assert not value.ended(track(value, 'stopped', position=200000))
    value.test_time[0] += 1.3
    assert value.ended(track(value, 'stopped', position=200000))


def test_real_client_transition_waits_for_full_duration_and_stable_successor():
    value, _ = queue()
    assert not value.ended(track(value, position=197600))
    changed = TrackInfo('客户端队列下一首', '陈楚生', '', 'playing')
    assert not value.ended(changed)
    value.test_time[0] += 1.3
    assert not value.ended(changed)
    value.test_time[0] += 1.2
    assert value.ended(changed)


def test_seek_back_clears_old_end_proof_and_pause_during_confirmation_disarms():
    value, _ = queue()
    assert not value.ended(track(value, position=199000))
    assert not value.ended(track(value, position=30000))
    value.test_time[0] += 200
    assert not value.ended(track(value, 'stopped', position=200000))
    assert not value.ended(track(value, position=199000))
    assert not value.ended(track(value, 'stopped', position=200000))
    value.auto_advance = False
    value.test_time[0] += 2
    assert not value.ended(track(value, 'stopped', position=200000))


def test_select_last_song_preserves_order_and_rejects_invalid_rows():
    value, player = queue()
    before = value.entries
    assert value.select(29).playlist_index == 29
    assert value.entries == before and player.sent[-1] == before[29].identifier
    for index in (-1, 30, True, '1'):
        assert not value.select(index).success


def test_resume_restored_queue_reselects_saved_song_if_qq_has_moved_elsewhere():
    value, player = queue()
    value.index = 12
    value.auto_advance = False
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_: None)
    manager.qq_playlist = value
    manager._qq_online_track = lambda: TrackInfo('外部歌曲', '其他歌手', '', 'paused')
    result = manager.control('auto', 'play')
    assert result.success and result.playlist_index == 12
    assert player.sent[-1] == value.entries[12].identifier
    assert value.round_number == 1 and value.auto_advance


def test_invalid_persisted_song_ids_or_wrong_artist_never_load(tmp_path):
    value, player = queue(tmp_path)
    data = json.loads((tmp_path / 'list.json').read_text(encoding='utf-8'))
    for changes in ({'id': '123&&bad'}, {'artist': '翻唱者'}):
        bad = json.loads(json.dumps(data))
        bad['songs'][0].update(changes)
        (tmp_path / 'bad.json').write_text(json.dumps(bad), encoding='utf-8')
        restored = QQPlaylist(player, store_path=tmp_path / 'bad.json')
        assert restored.entries == () and restored.index == -1


def test_public_fallback_has_thirty_distinct_titles_and_keeps_online_dispatch():
    candidates = bundled_candidates()
    assert len(candidates) >= 30
    player = Player()
    def offline(*_, **__):
        raise QQCatalogError('offline metadata service')
    player.catalog.search = offline
    value = QQPlaylist(player, fallback_loader=lambda: candidates)
    result = value.create()
    assert result.playlist_created and result.playlist_size == 30
    assert len({s.identifier for s in value.entries}) == 30
    assert len(player.sent) == 1


def test_primary_artist_fallback_accepts_guests_but_never_other_singers():
    from onepic_desktop_pet.qqmusic_online import candidate_matches
    candidate = replace(song(1), native={'primary_artist_only': True})
    assert candidate_matches(candidate, TrackInfo(candidate.title, '陈楚生 / 合作歌手'))
    assert not candidate_matches(candidate, TrackInfo(candidate.title, '其他歌手'))
    assert not candidate_matches(candidate, TrackInfo(candidate.title + ' (Live)', '陈楚生'))
    assert not candidate_matches(song(1), TrackInfo(candidate.title, '陈楚生 / 合作歌手', '专辑'))


def test_catalog_retry_is_bounded_and_stops_on_cancellation():
    player = Player()
    calls = []
    def search(*_, **__):
        calls.append(1)
        raise QQCatalogError('network')
    player.catalog.search = search
    value = QQPlaylist(player)
    assert not value.create().success and len(calls) == 2
    player.cancelled = lambda: True
    assert not value.create().success and len(calls) == 2


def test_recent_catalog_failure_uses_metadata_fallback_without_delaying_every_round():
    player = Player()
    calls = []
    def offline(*_, **__):
        calls.append(1)
        raise QQCatalogError('offline')
    player.catalog.search = offline
    now = [100.0]
    value = QQPlaylist(player, fallback_loader=bundled_candidates, clock=lambda: now[0])
    assert value.create().success and len(calls) == 2
    assert value.create().success and len(calls) == 2 and value.round_number == 2
    now[0] += 61
    assert value.create().success and len(calls) == 4
    player.cancelled = lambda: True
    assert not value.create().success and len(calls) == 4


def test_dialog_shows_all_thirty_and_buttons_use_shared_controller():
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from onepic_desktop_pet.music_playlist_ui import MusicPlaylistDialog
    app = QApplication.instance() or QApplication([])
    value, _ = queue()
    class Controller(QObject):
        result_ready = Signal(object)
        busy_changed = Signal(bool)
        busy = False
        manager = SimpleNamespace(qq_playlist=value)
        calls = []
        def perform(self, action):
            self.calls.append(action)
        def play_qq_song(self):
            self.calls.append('random')
        def play_qq_playlist_index(self, index):
            self.calls.append(index)
    controller = Controller()
    dialog = MusicPlaylistDialog(controller)
    try:
        assert dialog.songs.count() == 30
        dialog.buttons[2].click()
        assert controller.calls == [1]
        value.move(1)
        controller.result_ready.emit(value.decorate(player_result := SongPlaybackResult(True, 'qq', '', '', 'next')))
        assert dialog.songs.currentRow() == 1
        dialog.songs.itemDoubleClicked.emit(dialog.songs.item(29))
        assert controller.calls[-1] == 29
        controller.busy_changed.emit(True)
        assert not dialog.buttons[2].isEnabled()
        assert dialog.buttons[1].isEnabled()
    finally:
        dialog.close()


def test_playlist_window_is_independent_and_can_minimize_without_pausing():
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QApplication, QWidget
    from onepic_desktop_pet.music_playlist_ui import MusicPlaylistDialog
    app = QApplication.instance() or QApplication([])
    value, _ = queue()
    class Controller(QObject):
        result_ready = Signal(object)
        busy_changed = Signal(bool)
        busy = False
        manager = SimpleNamespace(qq_playlist=value)
        calls = []
        def perform(self, action): self.calls.append(action)
        def play_qq_song(self): self.calls.append('random')
        def play_qq_playlist_index(self, index): self.calls.append(index)
    controller = Controller()
    parent = QWidget()
    parent.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog = MusicPlaylistDialog(controller, parent)
    try:
        assert not dialog.isVisible()
        assert dialog.parentWidget() is None
        assert not dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert dialog.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint
        dialog.show()
        dialog.showMinimized()
        app.processEvents()
        assert dialog.isMinimized() and value.auto_advance
        dialog.showNormal()
        dialog.close()
        assert not dialog.isVisible() and value.auto_advance
        assert controller.calls == []
    finally:
        dialog.deleteLater()
        parent.close()


def test_empty_and_failed_playlist_have_a_visible_recovery_action_without_native_next():
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from onepic_desktop_pet.music_playlist_ui import MusicPlaylistDialog
    app=QApplication.instance() or QApplication([])
    player=Player()
    value=QQPlaylist(player,shuffle=lambda _:None)
    class Controller(QObject):
        result_ready=Signal(object)
        busy_changed=Signal(bool)
        busy=False
        manager=SimpleNamespace(qq_playlist=value)
        calls=[]
        def perform(self,action):self.calls.append(action)
        def play_qq_song(self):self.calls.append('random')
        def play_qq_playlist_index(self,index):self.calls.append(index)
    controller=Controller()
    dialog=MusicPlaylistDialog(controller)
    dialog.show();app.processEvents()
    try:
        assert dialog.empty.isVisible() and dialog.new_round_button.text()=='随机30首'
        assert not dialog.buttons[0].isEnabled() and not dialog.buttons[2].isEnabled()
        dialog.new_round_button.click()
        assert controller.calls==['random']
        player.fail=True
        result=value.create()
        controller.result_ready.emit(result);app.processEvents()
        assert dialog.songs.count()==30 and not dialog.empty.isVisible()
        assert dialog.state.text()=='待播放' and dialog.buttons[1].text()=='播放'
        dialog.songs.setCurrentRow(6)
        dialog.buttons[1].click()
        assert controller.calls[-1]==6
        dialog.buttons[2].click()
        assert controller.calls[-1]==1
        value.index=29;dialog.refresh()
        dialog.buttons[2].click()
        assert controller.calls[-1]=='random'
        assert 'next' not in controller.calls and 'toggle' not in controller.calls
    finally:
        dialog.close();dialog.deleteLater();app.processEvents()
