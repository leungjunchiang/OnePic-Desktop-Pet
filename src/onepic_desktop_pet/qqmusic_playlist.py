"""保存六毛的三十首在线歌单，按序续播并在每轮结束后重新生成下一轮。

只存歌曲元数据，不下载音频或保存登录信息。歌单属于六毛，不写入 QQ 云端收藏。
所有修改和媒体查询复用串行音乐线程；持久化失败不会丢失内存中的队列。
连续轮播先确认 QQ 单曲模式隔离旧队列；只在完整曲终或明确单曲回绕后切换六毛下一首。
模式设置安排在客户端最小化之前；失败说明给出播放栏的设置位置。
生成完毕先保存三十首，再尝试播放；首次接管或点歌失败不会丢失本机已生成歌单。
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
import re
import time

from .music_playback import MusicPlaybackError, MusicPlaybackOutcome, SongCandidate, SongPlaybackResult
from .qqmusic_online import QQCatalogError, artist_names, canonical, candidate_matches
from .resources import resource_path


def bundled_candidates():
    """公开目录元数据备用表，音频始终由 QQ 客户端在线播放。"""
    try:
        data = json.loads(resource_path('resources/music/qq-chenchusheng.json').read_text(encoding='utf-8'))
        songs = []
        for row in data['songs']:
            song_id = str(row['id'])
            if (not re.fullmatch(r'[1-9][0-9]{0,9}', song_id) or int(song_id) > 4294967295
                    or canonical('陈楚生') not in artist_names(row['artist'])):
                continue
            songs.append(SongCandidate('qq', row['title'], row['artist'], identifier=song_id,
                native={'album': row.get('album', ''), 'requires_subscription': bool(row.get('requires_subscription')),
                        'primary_artist_only': bool(row.get('primary_artist_only'))}))
        return tuple(songs)
    except (OSError, ValueError, KeyError, TypeError):
        return ()


class QQPlaylist:
    SIZE = 30

    def __init__(self, player, *, store_path: Path | None = None, shuffle=None, fallback_loader=None,
                 clock=time.monotonic, queue_mode=None, stopper=lambda: None):
        self.player = player
        self.store_path = store_path
        self.shuffle = shuffle or random.SystemRandom().shuffle
        self.fallback_loader = fallback_loader
        self.clock = clock
        self.queue_mode, self.stopper = queue_mode, stopper
        self._catalog_retry_after = 0.0
        self.entries: tuple[SongCandidate, ...] = ()
        self.index = -1
        self.round_number = 0
        self.auto_advance = False
        self._near_end = False
        self._end_not_before = None
        self._last_end_position = None
        self._pending_end = None
        self._start_stopped = False
        self.load()

    def failure(self, message):
        return SongPlaybackResult(False, 'qq', '', '陈楚生', message,
                                  MusicPlaybackError.RESULT_NOT_FOUND)

    def create(self, artist='陈楚生'):
        if self.player.cancelled():
            return self.failure('音乐操作已取消。')
        if self.player.client_finder('qq', self.player.settings.qq_music_path) is None:
            return self.failure('未找到 QQ 音乐，请先安装并在设置中选择 QQ 音乐客户端。')
        songs = {}
        def collect(rows):
            for candidate in rows:
                key = canonical(re.split(r'[（(]', candidate.title, maxsplit=1)[0])
                if key and (key not in songs or self._paid(songs[key]) > self._paid(candidate)
                            or (self._paid(songs[key]) == self._paid(candidate)
                                and (songs[key].native or {}).get('primary_artist_only'))):
                    songs[key] = candidate
        if self.fallback_loader and canonical(artist) == canonical('陈楚生'):
            collect(self.fallback_loader())
        try:
            has_fallback = sum(not self._paid(s) for s in songs.values()) >= self.SIZE
            pages = () if has_fallback and self.clock() < self._catalog_retry_after else range(1, 6)
            for page in pages:
                if self.player.cancelled():
                    return self.failure('音乐操作已取消。')
                try:
                    rows = self.player.catalog.search('', artist, page=page)
                except QQCatalogError:
                    if self.player.cancelled():
                        return self.failure('音乐操作已取消。')
                    self.player.sleep(0.2)
                    rows = self.player.catalog.search('', artist, page=page)
                collect(rows)
                if sum(not self._paid(s) for s in songs.values()) >= self.SIZE:
                    break
                if not rows:
                    break
        except QQCatalogError as exc:
            if len(songs) < self.SIZE:
                return self.failure(str(exc))
            self._catalog_retry_after = self.clock() + 60
        available = list(songs.values())
        if len(available) < self.SIZE:
            return self.failure(f'在线目录只找到 {len(available)} 首不同歌曲，未满 30 首；原歌单已保留，请稍后重试。')
        self.shuffle(available)
        available.sort(key=self._paid)  # 优先选择无需订阅的版本，授权仍由 QQ 判断。
        chosen = available[:self.SIZE]
        self.shuffle(chosen)
        if self.entries:
            previous = self.entries[self.index]
            if (chosen[0].identifier == previous.identifier
                    or canonical(chosen[0].title) == canonical(previous.title)):
                chosen[0], chosen[1] = chosen[1], chosen[0]
        if self.player.cancelled():
            return self.failure('音乐操作已取消。')
        self.entries, self.index = tuple(chosen), 0
        self.round_number += 1
        self.auto_advance = False
        self._reset_completion()
        self.save()
        result = self._play_candidate(chosen[0], artist=artist)
        if not result.success:
            if not self._start_stopped:
                self._stop_unisolated()
            return self.decorate(result, created=True)
        if not self._acquire_mode(result):
            return self.decorate(self._mode_failure(), created=True)
        self.auto_advance = (not self.player.cancelled()
                             and result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
        self._near_end = False
        self._reset_completion()
        self.save()
        return self.decorate(result, created=True)

    @staticmethod
    def _paid(song):
        return bool((song.native or {}).get('requires_subscription'))

    def decorate(self, result, *, created=False):
        prefix = f'六毛歌单 第 {self.round_number} 轮 {self.index + 1}/{len(self.entries)} · '
        return replace(result, playlist_size=len(self.entries), playlist_index=self.index,
                       playlist_created=created, playlist_round=self.round_number,
                       message=prefix + result.message)

    def move(self, step):
        if step == 1 and self.entries and self.index == len(self.entries) - 1:
            return self.create()
        return self.select(self.index + step)

    def select(self, target):
        if self.player.cancelled():
            return self.failure('音乐操作已取消。')
        if not self.entries:
            return self.failure('还没有陈楚生歌单，请先点击“随机听陈楚生”。')
        if type(target) is not int or not 0 <= target < len(self.entries):
            return self.failure('已经到歌单边界，请重新随机或选择列表中的歌曲。')
        result = self._play_candidate(self.entries[target])
        if result.success:
            if not self._acquire_mode(result):
                return self.decorate(self._mode_failure())
            self.index = target
            self.auto_advance = (not self.player.cancelled()
                                 and result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
            self._near_end = False
            self._reset_completion()
            self.save()
            return self.decorate(result)
        if not self._start_stopped:
            self._stop_unisolated()
        return self.decorate(result)

    def matches(self, track):
        if not track or not 0 <= self.index < len(self.entries):
            return False
        return candidate_matches(self.entries[self.index], track)

    def _reset_completion(self):
        self._near_end = False
        self._end_not_before = None
        self._last_end_position = None
        self._pending_end = None

    def _acquire_mode(self, result):
        if self.queue_mode is None:
            return True
        if result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED:
            if getattr(self.queue_mode, 'isolated', False) and not self.player.cancelled():
                return True
            return self._prepare_queue_mode()
        self._stop_unisolated()
        return False

    def _play_candidate(self, selected, **kwargs):
        self._start_stopped = False
        if self.queue_mode is not None:
            kwargs['prepare_queue'] = self._prepare_queue_mode
        return self.player.play_candidate(selected, **kwargs)

    def _prepare_queue_mode(self):
        if not self.player.cancelled() and self.queue_mode.acquire():
            return True
        self._stop_unisolated()
        return False

    def _stop_unisolated(self):
        self._start_stopped = True
        self.auto_advance = False
        self._reset_completion()
        try:
            self.stopper()
        finally:
            if self.queue_mode is not None:
                self.queue_mode.release()

    def _mode_failure(self):
        from .qqmusic_queue_mode import MODE_SETUP_MESSAGE
        return SongPlaybackResult(False, 'qq', '', '陈楚生', MODE_SETUP_MESSAGE,
                                  MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE)

    def ended(self, track):
        """播放完整时长后，结束状态还需持续 1.2 秒；暂停与异常快照不会抢切。"""
        if not self.auto_advance or track is None:
            self._pending_end = None
            return False
        now = self.clock()
        state = track.playback_status.casefold()
        matches = self.matches(track)
        position = getattr(track, 'raw_position_ms', None)
        position = track.position_ms if position is None else position
        duration = track.duration_ms
        if matches and state == 'playing':
            if (getattr(self.queue_mode, 'isolated', False) and self._near_end
                    and self._end_not_before is not None and self._last_end_position is not None
                    and duration == self._last_end_position[1] and position < 2500):
                # QQ stays on the same song in verified single mode. A real
                # end-to-start wrap is stronger evidence than metadata change;
                # it cannot insert an unrelated QQ queue item between ours.
                return now >= self._end_not_before
            self._pending_end = None
            near = duration > 0 and 0 <= duration - position <= 2500
            if near:
                sample = (position, duration)
                if sample != self._last_end_position:
                    self._end_not_before = now + max(0, duration - position) / 1000
                    self._last_end_position = sample
                self._near_end = True
            else:
                self._reset_completion()
            return False
        actual_end = matches and duration > 0 and 0 <= duration - position <= 200
        if state == 'paused' and not actual_end:
            self.auto_advance = False
            self._reset_completion()
            if self.queue_mode is not None:
                self.queue_mode.release()
            return False
        terminal = (not matches and bool(track.title) and state in {'playing', 'paused', 'stopped'}) or (
            actual_end and state in {'paused', 'stopped'})
        if not terminal or not self._near_end or self._end_not_before is None:
            self._pending_end = None
            if not matches:
                self.auto_advance = False
                self._reset_completion()
                if self.queue_mode is not None:
                    self.queue_mode.release()
            return False
        # A crossfade/metadata update can precede the actual end. Never send
        # our next-song command before the previous track's full deadline.
        fingerprint = (track.title, track.artist, track.album, state)
        if self._pending_end is None or self._pending_end[0] != fingerprint:
            self._pending_end = (fingerprint, now)
            return False
        return now >= self._end_not_before and now - self._pending_end[1] >= 1.2

    def save(self):
        if self.store_path is None:
            return
        data = {'version': 1, 'index': self.index, 'round': self.round_number, 'songs': [
            {'id': s.identifier, 'title': s.title, 'artist': s.artist,
             'album': (s.native or {}).get('album', ''), 'requires_subscription': self._paid(s),
             'primary_artist_only': bool((s.native or {}).get('primary_artist_only'))}
            for s in self.entries]}
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.store_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.store_path)
        except OSError:
            pass

    def load(self):
        if self.store_path is None:
            return
        try:
            if self.store_path.stat().st_size > 100_000:
                return
            data = json.loads(self.store_path.read_text(encoding='utf-8'))
            rows, index = data['songs'], data['index']
            if data['version'] != 1 or len(rows) != self.SIZE or type(index) is not int or not 0 <= index < self.SIZE:
                return
            songs = []
            for row in rows:
                song_id = row['id']
                if (not isinstance(song_id, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', song_id)
                        or int(song_id) > 4294967295):
                    return
                if any(not isinstance(row[k], str) or not 0 < len(row[k]) <= 200 for k in ('title', 'artist')):
                    return
                if canonical('陈楚生') not in artist_names(row['artist']):
                    return
                album = row.get('album', '')
                if not isinstance(album, str) or len(album) > 500:
                    return
                songs.append(SongCandidate('qq', row['title'], row['artist'], identifier=song_id,
                    native={'album': album, 'requires_subscription': bool(row.get('requires_subscription')),
                            'primary_artist_only': bool(row.get('primary_artist_only'))}))
            if len({s.identifier for s in songs}) != self.SIZE:
                return
            self.entries, self.index = tuple(songs), index
            number = data.get('round', 1)
            self.round_number = number if type(number) is int and 1 <= number <= 1_000_000 else 1
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return
