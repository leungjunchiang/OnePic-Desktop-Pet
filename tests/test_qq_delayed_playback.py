"""验证慢加载等待超过十秒仍只投递一次，并保留取消优先，不重启或操作账号。"""
from pathlib import Path
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.qqmusic_online import QQMusicOnlinePlayer, QQ_VERIFY_TIMEOUT
from onepic_desktop_pet.music_control import MusicProviderManager, TrackInfo
from onepic_desktop_pet.music_playback import MusicPlaybackOutcome
from test_qqmusic_playlist import song


def test_slow_licensed_song_can_confirm_after_twelve_seconds_without_redispatch():
    now, sent = [0.0], []
    item = song(1, paid=True)
    player = QQMusicOnlinePlayer(PetSettings(),
        lambda: TrackInfo(item.title, item.artist, '专辑', 'playing') if now[0] >= 12 else None,
        client_finder=lambda *_: Path('QQMusic.exe'), dispatcher=lambda *_: sent.append('once'),
        clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0]+seconds))
    assert player.verify_timeout == QQ_VERIFY_TIMEOUT == 25
    result = player.play_candidate(item)
    assert result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED and 12 <= now[0] < 13
    assert sent == ['once']


def test_cancel_during_extended_wait_cannot_late_confirm_or_redispatch():
    now, sent = [0.0], []
    player = QQMusicOnlinePlayer(PetSettings(), lambda: None, cancelled=lambda: now[0] >= 1,
        client_finder=lambda *_: Path('QQMusic.exe'), dispatcher=lambda *_: sent.append('once'),
        clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0]+seconds))
    assert not player.play_candidate(song(1)).success
    assert now[0] < 2 and sent == ['once']


def test_production_manager_uses_same_default_wait():
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_: None)
    assert manager.qq_online_player.verify_timeout == 25
