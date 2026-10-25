"""验证 QQ 身份 ID 队列、曲尾手动暂停优先、稳定换轮及资料门禁。"""
import json
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import pytest

from onepic_desktop_pet.music_control import TrackInfo
from onepic_desktop_pet.music_playback import MusicPlaybackOutcome, SongCandidate, SongPlaybackResult
from onepic_desktop_pet.qqmusic_native_queue import QQNativeQueue
from onepic_desktop_pet.qqmusic_playlist import QQPlaylist
from onepic_desktop_pet.qqmusic_queue_mode import QQQueueMode
from onepic_desktop_pet.qqmusic_online import QQMusicOnlinePlayer
from onepic_desktop_pet.music_control import MusicProviderManager
from onepic_desktop_pet.config import PetSettings
from test_qq_queue_mode import Backend
from test_qqmusic_playlist import Player, song


def metadata(item):
    return {'type': 0, 'id': int(item.identifier), 'mid': 'mid'+item.identifier,
        'name': item.title, 'singer': [{'name': item.artist}], 'album': {'name': '专辑'},
        'file': {'size_128mp3': 1024}, 'interval': 200, 'action': {'switch': 3}}


def native(entries, mutate=None):
    requests, commands = [], []
    def fetch(url):
        payload=json.loads(parse_qs(urlparse(url).query)['data'][0])
        requests.append(payload)
        result={'code': 0}
        for name, value in payload.items():
            if name=='comm':continue
            item=next(s for s in entries if int(s.identifier)==value['param']['song_id'])
            row=metadata(item)
            if mutate:mutate(row)
            result[name]={'code':0,'data':{'track_info':row}}
        return result
    return QQNativeQueue(request_json=fetch,popen=lambda *a,**k: commands.append((a,k)),running_check=lambda _:None),requests,commands


def test_thirty_keeps_remaining_order_without_sending_guest_metadata_or_targeting_cloud(tmp_path):
    entries=tuple(song(i) for i in range(1,31))
    backend,requests,commands=native(entries)
    executable=tmp_path/'QQMusic.exe';executable.touch()
    backend.dispatch(executable,entries,12)
    assert len(requests)==3 and len(commands)==1
    argv=commands[0][0][0]
    assert argv[:2]==[str(executable),'/playbysongid']
    fields=dict(pair.split('==',1) for pair in argv[2].split('&&'))
    assert fields=={'cmd_count':'1','cmd_0':'playsong',
        'id_0':'|'.join(str(i) for i in range(13,31)),
        'songtype_0':'|'.join('0' for _ in range(18)),'info_0':'','quality_0':'quality'}
    assert not any(token in argv[2] for token in ('pay','action','switch','try','listId','listName'))
    assert commands[0][1]['shell'] is False
    backend.dispatch(executable,tuple(reversed(entries)),0)
    assert len(requests)==3
    assert 'id_0=='+'|'.join(str(i) for i in range(30,0,-1))+'&&' in commands[1][0][0][2]


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(id=999),lambda r:r.update(type=111),
    lambda r:r.update(name='不同版本'),lambda r:r.update(file=None),lambda r:r.update(file={}),
    lambda r:r.update(singer=[{'name':'其他歌手'}]),lambda r:r.update(interval=0),
])
def test_bad_or_wrong_metadata_sends_no_command(tmp_path,mutation):
    entries=(song(1),)
    backend,_,commands=native(entries,mutation)
    executable=tmp_path/'QQMusic.exe';executable.touch()
    with pytest.raises(OSError):backend.dispatch(executable,entries,0)
    assert commands==[]


def test_cancel_during_metadata_lookup_does_not_play(tmp_path):
    entries=tuple(song(i) for i in range(1,31))
    backend,requests,commands=native(entries)
    backend.cancelled=lambda:len(requests)>=1
    executable=tmp_path/'QQMusic.exe';executable.touch()
    with pytest.raises(OSError):backend.dispatch(executable,entries,0)
    assert len(requests)==1 and commands==[]


def test_vip_guest_status_is_validated_locally_but_never_forwarded_or_changed(tmp_path):
    item=song(1,paid=True)
    row=metadata(item)
    row.update(action={'switch':1,'msgid':13},pay={'pay_play':1,'pay_status':0})
    row['file'].update(size_try=960000,try_begin=30000,try_end=60000)
    original=deepcopy(row)
    item=SongCandidate('qq',item.title,item.artist,identifier=item.identifier,
                       native={'track_info':row,'album':'专辑','playback_rights':True})
    commands=[]
    backend=QQNativeQueue(request_json=lambda _:pytest.fail('Embedded metadata should suffice'),
        popen=lambda *a,**k:commands.append((a,k)),running_check=lambda _:None)
    executable=tmp_path/'带 空格\'QQMusic'/'QQMusic.exe'
    executable.parent.mkdir();executable.touch()
    backend.dispatch(executable,(item,),0)
    assert row==original and backend._rows['1']==original
    argv=commands[0][0][0]
    assert argv==[str(executable),'/playbysongid',
        'cmd_count==1&&cmd_0==playsong&&id_0==1&&songtype_0==0&&info_0==&&quality_0==quality']
    assert commands[0][1]['shell'] is False


def playlist():
    player=Player()
    mode=QQQueueMode(Backend(),target='顺序播放')
    value=QQPlaylist(player,shuffle=lambda rows:None,queue_mode=mode,native_queue=SimpleNamespace())
    now=[100.0];value.clock=lambda:now[0]
    assert value.create().success
    value.native_loaded=True
    assert mode.acquire()
    return value,player,now


def track(value,index=None,state='playing',raw=1000,duration=200000):
    item=value.entries[value.index if index is None else index]
    return TrackInfo(item.title,item.artist,'专辑',state,raw,duration,raw_position_ms=raw)


def test_native_next_syncs_position_without_dispatching_or_pausing():
    value,player,now=playlist()
    assert value.observe_native(track(value,raw=199000)) is None
    now[0]+=1
    result=value.observe_native(track(value,1))
    assert result.success and result.playlist_index==1 and value.auto_advance
    assert len(player.sent)==1 and value.queue_mode.backend.changes==['顺序播放']
    # A late tail snapshot cannot trigger a second native dispatch.
    assert value.observe_native(track(value)) is None and len(player.sent)==1


def test_native_pause_and_reload_do_not_claim_an_active_queue():
    value,_,_=playlist()
    assert value.observe_native(track(value,state='paused')) is None
    assert not value.auto_advance and value.queue_mode.backend.value=='随机播放'
    fresh=QQPlaylist(Player(),native_queue=SimpleNamespace())
    assert not fresh.native_loaded and not fresh.auto_advance


@pytest.mark.parametrize('raw', [197600, 199000, 199700, 200000])
def test_last_song_client_pause_never_starts_a_new_round_after_deadline(raw):
    value, player, now = playlist()
    value.index = 29
    assert value.observe_native(track(value, raw=raw)) is None
    assert value.observe_native(track(value, state='paused', raw=raw)) is None
    for seconds in (1.3, 5, 60):
        now[0] += seconds
        assert value.observe_native(track(value, state='paused', raw=raw)) is None
    assert not value.auto_advance and value.round_number == 1
    assert len(player.sent) == 1


def test_stopped_before_actual_tail_is_not_a_finished_round():
    value, _, now = playlist()
    value.index = 29
    assert value.observe_native(track(value, raw=197600)) is None
    assert value.observe_native(track(value, state='stopped', raw=197600)) is None
    now[0] += 3
    assert value.observe_native(track(value, state='stopped', raw=197600)) is None


def test_continuous_stopped_state_warns_once_without_dispatching_a_recovery_song():
    value, player, now = playlist()
    assert value.observe_native(track(value, state='stopped', raw=90000)) is None
    now[0] += 29
    assert value.observe_native(track(value, state='stopped', raw=90000)) is None
    now[0] += 1
    result = value.observe_native(track(value, state='stopped', raw=90000))
    assert not result.success and '30 秒' in result.message
    assert not value.auto_advance and len(player.sent) == 1
    assert value.observe_native(track(value, state='stopped', raw=90000)) is None


@pytest.mark.parametrize('interruption', ['playing', 'buffering', 'missing', 'progress'])
def test_buffering_or_new_evidence_resets_stop_watchdog(interruption):
    value, player, now = playlist()
    assert value.observe_native(track(value, state='stopped', raw=90000)) is None
    now[0] += 20
    sample = (None if interruption == 'missing' else
              track(value, state='stopped' if interruption == 'progress' else interruption,
                    raw=91000 if interruption == 'progress' else 90000))
    assert value.observe_native(sample) is None
    now[0] += 20
    assert value.observe_native(track(value, state='stopped', raw=90000)) is None
    assert value.auto_advance and len(player.sent) == 1


def test_last_song_waits_for_stable_end_and_reuses_cached_thirty():
    value,player,now=playlist()
    value.index=29
    assert value.observe_native(track(value,raw=199500)) is None
    assert value.observe_native(track(value,state='stopped',raw=199700)) is None
    now[0]+=.6
    assert value.observe_native(track(value,state='stopped',raw=199700)) is None
    now[0]+=.7
    assert value.observe_native(track(value,state='stopped',raw=199700))=='round'
    old=value.entries
    player.catalog.search=lambda *_a,**_k:pytest.fail('Automatic round must not query catalog')
    assert value.create(reuse_catalog=True).success
    assert value.round_number==2 and set(value.entries)==set(old)


def test_partial_metadata_transition_is_tolerated_and_foreign_track_is_refused():
    value,_,now=playlist()
    foreign=TrackInfo('其他歌曲','其他歌手','', 'playing',1000,200000)
    assert value.observe_native(foreign) is None
    now[0]+=1
    assert value.observe_native(track(value,1)).success
    assert value.observe_native(foreign) is None
    now[0]+=2.1
    assert value.observe_native(foreign)=='foreign'


def test_live_title_uses_version_field_and_keeps_real_metadata(tmp_path):
    entry=song(1,title='姑娘 (Live)')
    def version(row):
        row['name']='姑娘'
        row['title']='姑娘 (Live)'
    backend,_,commands=native((entry,),version)
    executable=tmp_path/'QQMusic.exe';executable.touch()
    backend.dispatch(executable,(entry,),0)
    assert len(commands)==1 and backend._rows['1']['name']=='姑娘'


def test_real_player_dispatches_full_queue_and_manager_follows_without_reissuing(tmp_path):
    entries=tuple(song(i) for i in range(1,41))
    backend,_,commands=native(entries)
    current=[None]
    def accept(command,**kwargs):
        assert mode.backend.value=='顺序播放'  # ID batches obey QQ's current mode even for their first track.
        commands.append(command)
        fields=dict(pair.split('==',1) for pair in command[2].split('&&'))
        row=backend._rows[fields['id_0'].split('|')[0]]
        current[0]=TrackInfo(row['name'],'陈楚生','专辑','playing',1000,200000)
    backend.popen=accept
    executable=tmp_path/'QQMusic.exe';executable.touch()
    player=QQMusicOnlinePlayer(PetSettings(),lambda:current[0],
        catalog=SimpleNamespace(search=lambda *_a,**_k:entries),client_finder=lambda *_:executable,
        backgrounder=lambda *_:None)
    mode=QQQueueMode(Backend(),target='顺序播放')
    value=QQPlaylist(player,shuffle=lambda rows:None,native_queue=backend,queue_mode=mode)
    assert value.create().success and value.native_loaded and value.auto_advance
    assert mode.backend.value=='顺序播放'
    manager=MusicProviderManager(PetSettings(),platform_name='win32',windows_bridge=SimpleNamespace())
    manager.qq_playlist=value
    current[0]=track(value,1)
    manager._qq_online_track=lambda:current[0]
    manager._control_qq_online=lambda *_:pytest.fail('Ordinary native handoff must not pause or skip')
    result=manager._playlist_tick()
    assert result.success and value.index==1 and len(commands)==1
    value.select(12)
    assert current[0].title==value.entries[12].title and value.index==12
    assert len(commands)==2
    # QQ has just advanced again but the one-second pet poll has not run.
    current[0]=track(value,13)
    result=manager.control('auto','next')
    assert result.success and value.index==14 and current[0].title==value.entries[14].title
