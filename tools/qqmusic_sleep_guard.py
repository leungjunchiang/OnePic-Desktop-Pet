"""真实播放验收的独立暂停守护；认可已停止，默认不关闭 QQ 或干扰账号会话。"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time

from onepic_desktop_pet.music_control import WindowsGSMTCBridge


def qq_pids(executable: str) -> list[int]:
    """逐个核对进程完整路径，绝不按模糊名称终止其他程序。"""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    result = []
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == 'qqmusic.exe':
                handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(path))
                        if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                            if path.value.casefold() == executable.casefold():
                                result.append(int(entry.th32ProcessID))
                    finally:
                        kernel.CloseHandle(handle)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qq-exe', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--close-on-pause-failure', action='store_true',
                        help='Only explicitly opted-in silence checks may close the verified QQ process')
    args = parser.parse_args()
    bridge = WindowsGSMTCBridge(timeout_seconds=2)
    started = time.monotonic()
    first_playing = target_started = None
    evidence = {'status': 'ARMED', 'seconds': args.seconds, 'observations': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def write():
        args.report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')

    def sessions():
        return tuple(s for s in bridge.sessions() if 'qqmusic' in s.source_id.casefold())

    write()
    print('SLEEP_GUARD_ARMED', flush=True)
    while True:
        now = time.monotonic()
        try:
            current = sessions()
        except Exception as exc:
            current = ()
            evidence['last_error'] = str(exc)
        playing = [s for s in current if s.track.playback_status.casefold() == 'playing']
        target = next((s for s in playing if '陈楚生' in s.track.artist), None)
        if playing and first_playing is None:
            first_playing = now
        if target and target_started is None:
            target_started = now
            evidence.update(status='PLAYING_CHEN_CHUSHENG', title=target.track.title,
                            artist=target.track.artist, album=target.track.album)
            write()
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
        target_due = target_started is not None and now - target_started >= args.seconds
        hard_due = now - started >= args.timeout or (
            first_playing is not None and target_started is None and now - first_playing >= 40)
        if target_due or hard_due:
            evidence['elapsed_playback_seconds'] = round(now - target_started, 3) if target_started else None
            for _ in range(3):
                for session in current:
                    try:
                        bridge.control(session.source_id, 'pause')
                    except Exception as exc:
                        evidence['pause_error'] = str(exc)
                time.sleep(0.5)
                try:
                    current = sessions()
                    states = [s.track.playback_status.casefold() for s in current]
                    if states and all(state in ('paused', 'stopped', 'closed') for state in states):
                        evidence.update(status='PAUSED_CONFIRMED' if all(state == 'paused' for state in states)
                                        else 'STOPPED_CONFIRMED', final_tracks=[
                            {'title': s.track.title, 'artist': s.track.artist, 'state': s.track.playback_status}
                            for s in current])
                        write()
                        print(json.dumps(evidence, ensure_ascii=False), flush=True)
                        return 0
                except Exception:
                    pass
            if not args.close_on_pause_failure:
                evidence.update(status='PAUSE_NOT_CONFIRMED', client_close_attempted=False)
                write()
                print(json.dumps(evidence, ensure_ascii=False), flush=True)
                return 1
            # 强制关闭必须由调用者明确选择，默认保留客户端及登录会话。
            pids = qq_pids(args.qq_exe)
            for pid in pids:
                subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True, timeout=5)
            evidence.update(status='CLIENT_CLOSED_FOR_SILENCE' if pids else 'NO_QQ_PROCESS', closed_pids=pids)
            write()
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
            return 2
        time.sleep(0.3)


if __name__ == '__main__':
    raise SystemExit(main())
