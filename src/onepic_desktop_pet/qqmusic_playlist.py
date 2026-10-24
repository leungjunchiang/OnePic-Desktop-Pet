"""保存六毛的三十首在线歌单，按序续播并在每轮结束后重新生成下一轮。

只存歌曲元数据，不下载音频或保存登录信息。歌单属于六毛，不写入 QQ 云端收藏。
所有修改和媒体查询复用串行音乐线程；持久化失败不会丢失内存中的队列。
Windows 歌曲 ID 临时队列由 QQ 解析当前账号权益并顺序衔接，投递前先确认顺序模式。
六毛同步实际曲目，不再曲终抢切；手动选曲仅投递该位置至轮末的剩余队列。
其他适配保留单曲隔离和保守曲终判断。
每轮热门前五十对应歌曲随机十五首、完整曲库随机十五首，按歌名和 ID 去重并混合打乱。
随机完全排除现场版，仅收公开元数据明确有播放/付费授权的歌曲，会员曲同等随机。
现场、无版权或无法确认授权的候选在同名归并前排除，避免覆盖可用的录音室版本。
完整曲库部分排除已选曲目，仍可抽到其他热门歌；跨轮及首曲可重复。
原生队列首次预备整个候选池的元数据，后续换轮从该池重新抽取三十首且不曲终联网。
模式设置不改变 QQ 窗口状态；Mac 失败保留对应阶段原因及自检入口提示。
未确认实际歌曲的结果保留原始原因，不能误报成播放模式失败。
登录未就绪的请求尚未投递，仅禁用本机续播，不触发失败暂停或恢复 QQ 模式。
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
from .qqmusic_eligibility import is_live_song, playback_rights, random_song_eligible


def song_title_key(title):
    """同一首歌的现场/录音版本共享轮内去重键。"""
    return canonical(re.split(r'[（(]', title, maxsplit=1)[0])


def bundled_candidates():
    """随包公开元数据和明确日期的热歌快照；不包含音频、账户或凭据。"""
    songs = {}
    for name in ('qq-chenchusheng.json', 'qq-chenchusheng-hot.json', 'qq-chenchusheng-studio.json'):
        try:
            data = json.loads(resource_path('resources/music/' + name).read_text(encoding='utf-8'))
            for row in data['songs']:
                song_id = str(row['id'])
                if (not re.fullmatch(r'[1-9][0-9]{0,9}', song_id) or int(song_id) > 4294967295
                        or canonical('陈楚生') not in artist_names(row['artist'])):
                    continue
                native = {'album': row.get('album', ''), 'requires_subscription': bool(row.get('requires_subscription')),
                          'primary_artist_only': bool(row.get('primary_artist_only'))}
                rank = row.get('popularity_rank')
                if type(rank) is int and 1 <= rank <= 50:
                    native['popularity_rank'] = rank
                if isinstance(row.get('track_info'), dict):
                    native['track_info'] = row['track_info']
                    native['playback_rights'] = playback_rights(row['track_info'])
                songs[song_id] = SongCandidate('qq', row['title'], row['artist'], identifier=song_id, native=native)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return tuple(songs.values())


class QQPlaylist:
    SIZE = 30
    HOT_SIZE = 15

    def __init__(self, player, *, store_path: Path | None = None, shuffle=None, fallback_loader=None,
                 clock=time.monotonic, queue_mode=None, stopper=lambda: None, native_queue=None):
        self.player = player
        self.store_path = store_path
        self.shuffle = shuffle or random.SystemRandom().shuffle
        self.fallback_loader = fallback_loader
        self.clock = clock
        self.queue_mode, self.stopper = queue_mode, stopper
        self.native_queue = native_queue
        self.native_loaded = False
        self._native_pending = None
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
        self._catalog = ()
        self._popular_keys = set()
        self._popular_snapshot = False
        self.load()
        self._reset_completion()

    def failure(self, message):
        return SongPlaybackResult(False, 'qq', '', '陈楚生', message,
                                  MusicPlaybackError.RESULT_NOT_FOUND)

    def create(self, artist='陈楚生', *, reuse_catalog=False):
        if self.player.cancelled():
            return self.failure('音乐操作已取消。')
        if self.player.client_finder('qq', self.player.settings.qq_music_path) is None:
            return self.failure('未找到 QQ 音乐，请先安装并在设置中选择 QQ 音乐客户端。')
        songs = {}
        popular_keys = set(self._popular_keys) if reuse_catalog and self._catalog else set()
        popular_snapshot = self._popular_snapshot if reuse_catalog and self._catalog else False
        def collect(rows):
            for candidate in rows:
                if not random_song_eligible(candidate):
                    continue
                key = song_title_key(candidate.title)
                same_id_metadata = (key in songs and songs[key].identifier == candidate.identifier
                                    and (candidate.native or {}).get('track_info') is not None)
                if key and (key not in songs or same_id_metadata
                            or (songs[key].native or {}).get('primary_artist_only')):
                    songs[key] = candidate
        if reuse_catalog:
            collect(self._catalog or self.entries)
        if self.fallback_loader and not (reuse_catalog and self.native_queue is not None and self._catalog) and canonical(artist) == canonical('陈楚生'):
            fallback = self.fallback_loader()
            collect(fallback)
            popular_keys.update(self._top_fifty_keys(fallback))
            popular_snapshot = len(popular_keys) >= self.HOT_SIZE
        try:
            loaded_artist = False
            artist_loader = getattr(self.player.catalog, 'artist_candidates', None)
            if not reuse_catalog and callable(artist_loader) and self.clock() >= self._catalog_retry_after:
                try:
                    artist_rows = artist_loader(artist, cancelled=self.player.cancelled)
                    collect(artist_rows)
                    ranked = self._top_fifty_keys(artist_rows)
                    if len(ranked) >= self.HOT_SIZE:
                        popular_keys = ranked  # Fresh official ordering replaces the bundled snapshot.
                        popular_snapshot = False
                    loaded_artist = len(songs) >= self.SIZE
                except QQCatalogError:
                    if self.player.cancelled():
                        return self.failure('音乐操作已取消。')
            has_fallback = len(songs) >= self.SIZE
            cached_native = reuse_catalog and self.native_queue is not None and len(songs) >= self.SIZE
            pages = () if loaded_artist or cached_native or has_fallback and (reuse_catalog or self.clock() < self._catalog_retry_after) else range(1, 6)
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
                popular_keys.update(self._top_fifty_keys(rows))
                if len(songs) >= self.SIZE:
                    break
                if not rows:
                    break
        except QQCatalogError as exc:
            if len(songs) < self.SIZE:
                return self.failure(str(exc))
            self._catalog_retry_after = self.clock() + 60
        by_id = {}
        for candidate in songs.values():
            previous = by_id.get(candidate.identifier)
            if previous is None or (candidate.native or {}).get('track_info') is not None:
                by_id[candidate.identifier] = candidate
        available = list(by_id.values())
        if len(available) < self.SIZE:
            return self.failure(f'在线目录只找到 {len(available)} 首已获播放/付费授权的非现场歌曲，未满 30 首；原歌单已保留，请稍后重试。')
        popular_keys.update(self._top_fifty_keys(available) if not popular_keys else ())
        hot = [s for s in available if song_title_key(s.title) in popular_keys]
        if len(hot) < self.HOT_SIZE:
            return self.failure('热门前 50 的可用歌曲不足 15 首，原歌单已保留，请稍后重试。')
        chosen = self._sample(hot, self.HOT_SIZE)
        selected_ids = {s.identifier for s in chosen}
        remaining = [s for s in available if s.identifier not in selected_ids]
        rest = self._sample(remaining, self.SIZE - self.HOT_SIZE)
        chosen += rest
        self.shuffle(chosen)
        if self.player.cancelled():
            return self.failure('音乐操作已取消。')
        self._catalog = tuple(available)
        self._popular_keys = popular_keys
        self._popular_snapshot = popular_snapshot
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
            return self.decorate(self._unready_result(result), created=True)
        self.auto_advance = (not self.player.cancelled()
                             and result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
        self._near_end = False
        self._reset_completion()
        self.save()
        return self.decorate(result, created=True)

    @staticmethod
    def _top_fifty_keys(songs):
        return {song_title_key(s.title) for s in songs
                if type((s.native or {}).get('popularity_rank')) is int
                and 1 <= s.native['popularity_rank'] <= 50}

    def _sample(self, pool, count):
        """所有已获授权的候选同等洗牌；不再按会员标记排序。"""
        self.shuffle(pool)
        return pool[:count]

    @staticmethod
    def _paid(song):
        return bool((song.native or {}).get('requires_subscription'))

    def decorate(self, result, *, created=False):
        prefix = f'六毛歌单 第 {self.round_number} 轮 {self.index + 1}/{len(self.entries)} · '
        if self._popular_snapshot:
            prefix += '使用随包热歌快照 · '
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
                return self.decorate(self._unready_result(result))
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
        self._tail_position = None
        self._tail_samples = 0
        self._handoff_deadline = None
        self.completion_reason = None

    def _fresh_tail_complete(self, track, position, duration, now):
        """至少两次真实曲末推进且新鲜时间轴到完整时长，才允许不等回绕直接衔接。"""
        age = getattr(track, 'timeline_age_ms', None)
        if (getattr(track, 'raw_position_ms', None) is None or age is None
                or not 0 <= age <= 2000 or not 0 <= duration - position <= 2500
                or duration <= 0 or not getattr(self.queue_mode, 'isolated', False)):
            self._tail_position = None
            self._tail_samples = 0
            self._handoff_deadline = None
            return False
        previous = self._tail_position
        if previous is None or previous[1] != duration or position < previous[0]:
            self._tail_samples = 1
            self._handoff_deadline = None
        elif position > previous[0]:
            self._tail_samples += 1
        self._tail_position = (position, duration)
        deadline = now + (duration - position - age) / 1000
        self._handoff_deadline = max(deadline, self._handoff_deadline or deadline)
        complete = self._tail_samples >= 2 and now >= self._handoff_deadline and track.position_ms >= duration
        if complete:
            self.completion_reason = 'fresh-timeline-end'
        return complete

    @property
    def poll_interval_ms(self):
        """仅在预计曲终附近短时加密采样，冻结的时间轴恢复普通频率。"""
        return (75 if self._near_end and self._end_not_before is not None
                and self.clock() <= self._end_not_before + 2 else 1000)

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
        if any(not random_song_eligible(song) for song in self.entries):
            self.block_unready_client()
            return self.failure('旧歌单包含现场版、无版权或尚未确认授权的歌曲；请重新点击“随机听陈楚生”生成新歌单。')
        if self.queue_mode is not None:
            kwargs['prepare_queue'] = self._prepare_queue_mode
            kwargs['queue_failure_message'] = lambda: self._mode_failure().message
        if self.native_queue is not None:
            self.native_loaded = False
            def dispatch(client, candidate):
                prepare = getattr(self.native_queue, 'prepare', None)
                if callable(prepare):
                    prepare(self._catalog or self.entries)
                if self.queue_mode is not None and not self._prepare_queue_mode():
                    raise OSError(self._mode_failure().message)
                self.native_queue.dispatch(client, self.entries, self.entries.index(candidate))
            kwargs['dispatch_candidate'] = dispatch
        result = self.player.play_candidate(selected, **kwargs)
        if result.error_code is MusicPlaybackError.CLIENT_NOT_READY:
            self.block_unready_client()
        if self.native_queue is not None:
            self.native_loaded = (result.success and result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
                                  and not self.player.cancelled())
            self._native_pending = None
        return result

    def block_unready_client(self):
        """尚未投递时只停止本机轮播，不发送失败清理命令。"""
        self._start_stopped = True
        self.auto_advance = self.native_loaded = False
        self._reset_completion()
        abandon = getattr(self.queue_mode, 'abandon', None)
        if callable(abandon):
            abandon()

    def observe_native(self, track):
        """只跟随 QQ 的实际曲目；普通曲终不发送暂停或下一首。"""
        if not self.auto_advance or not self.native_loaded or track is None:
            return None
        now = self.clock()
        state = track.playback_status.casefold()
        if state == 'playing' and track.title:
            matched = next((i for i, s in enumerate(self.entries) if candidate_matches(s, track)), None)
            if matched is not None:
                self._native_pending = None
                if matched != self.index:
                    self.sync_native_position(track)
                    return self.decorate(SongPlaybackResult(True, 'qq', '', '陈楚生',
                        f'QQ 音乐：{track.artist}《{track.title}》', selected=self.entries[matched],
                        current_title=track.title, current_artist=track.artist,
                        outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED))
                raw = getattr(track, 'raw_position_ms', None)
                raw = track.position_ms if raw is None else raw
                if track.duration_ms > 0 and 0 <= track.duration_ms - raw <= 2500:
                    self._near_end = True
                    age = getattr(track, 'timeline_age_ms', None) or 0
                    deadline = now + max(0, track.duration_ms - raw - min(2000, max(0, age))) / 1000
                    self._end_not_before = max(self._end_not_before or deadline, deadline)
                else:
                    self._near_end = False
                    self._end_not_before = None
                return None
            # QQ can publish the new title before its album/artist update.
            if self._native_pending is None or self._native_pending[0] != 'foreign':
                self._native_pending = ('foreign', now)
            if now - self._native_pending[1] >= 2:
                return 'foreign'
            return None
        if state == 'paused' and not self._near_end:
            self.auto_advance = False
            self._native_pending = None
            if self.queue_mode is not None:
                self.queue_mode.release()
            return None
        if (state in {'paused', 'stopped'} and self.matches(track) and self._near_end
                and self.index == len(self.entries) - 1 and self._end_not_before is not None):
            if self._native_pending is None or self._native_pending[0] != 'end':
                self._native_pending = ('end', now)
            if now >= self._end_not_before and now - self._native_pending[1] >= 1.2:
                return 'round'
        return None

    def sync_native_position(self, track):
        """手动控制前同步真实位置，避免曲终刚换歌而轮询未到时重播同一首。"""
        if self.native_queue is None or not self.native_loaded or track is None:
            return False
        matched = next((i for i, song in enumerate(self.entries) if candidate_matches(song, track)), None)
        if matched is None:
            return False
        if matched != self.index:
            self.index = matched
            self._native_pending = None
            self._reset_completion()
            self.save()
        return True

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
        from .qqmusic_queue_mode import mode_setup_message
        mode = self.queue_mode
        if getattr(getattr(mode, 'backend', None), 'platform', '') == 'darwin':
            detail = getattr(mode, 'last_error', '')
            return SongPlaybackResult(False, 'qq', '', '陈楚生',
                '[Mac QQ / 模式] 尚未确认连续播放所需模式，已请求暂停并保留歌单。'
                '请在设置中点“检查 QQ 适配”。' + (f' 原因：{detail}' if detail else ''),
                MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE)
        return SongPlaybackResult(False, 'qq', '', '陈楚生',
                                  mode_setup_message(getattr(self.queue_mode, 'target', '单曲循环')),
                                  MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE)

    def _unready_result(self, result):
        if result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED:
            return self._mode_failure()
        return replace(result, success=False, error_code=MusicPlaybackError.MEDIA_SESSION_TIMEOUT,
                       message=result.message + ' 未启用连续播放，已请求暂停，六毛歌单已保留。')

    def ended(self, track):
        """新鲜曲末到期及时衔接；无可靠时间轴时保留真实回绕/稳定结束的保守判断。"""
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
            if self._fresh_tail_complete(track, position, duration, now):
                return True
            if (getattr(self.queue_mode, 'isolated', False) and self._near_end
                    and self._end_not_before is not None and self._last_end_position is not None
                    and duration == self._last_end_position[1] and position < 2500):
                # QQ stays on the same song in verified single mode. A real
                # end-to-start wrap is stronger evidence than metadata change;
                # it cannot insert an unrelated QQ queue item between ours.
                complete = now >= self._end_not_before
                if complete:
                    self.completion_reason = 'single-wrap'
                return complete
            self._pending_end = None
            near = duration > 0 and 0 <= duration - position <= 2500
            if near:
                sample = (position, duration)
                if sample != self._last_end_position:
                    age = getattr(track, 'timeline_age_ms', None)
                    age = age if age is not None and 0 <= age <= 2000 else 0
                    self._end_not_before = now + max(0, duration - position - age) / 1000
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
             'playback_rights': random_song_eligible(s),
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
                            'playback_rights': row.get('playback_rights') is True,
                            'primary_artist_only': bool(row.get('primary_artist_only'))}))
            if len({s.identifier for s in songs}) != self.SIZE:
                return
            self.entries, self.index = tuple(songs), index
            number = data.get('round', 1)
            self.round_number = number if type(number) is int and 1 <= number <= 1_000_000 else 1
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return
