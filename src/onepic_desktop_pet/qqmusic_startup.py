"""QQ 冷启动先普通打开并等待公开界面就绪；不携带播放命令、不访问或修改登录凭据。

只复用唯一已运行版本，避免设置里的旧程序再次拉起另一版本。登录未就绪时没有
播放、暂停、模式恢复或重试启动动作；用户完成 QQ 自身登录后可再次点击随机。
兼容没有搜索框的播放栏，普通“登录”文字不覆盖完整播放栏证据；读取失败与登录提示分开说明。
"""
from pathlib import Path
import subprocess
import time

from .music_background import music_client_windows, qq_background_startup_options


class QQClientNotReady(OSError):
    """播放投递之前的客户端状态阻断，不应触发失败暂停或模式修改。"""


LOGIN_LABELS = frozenset(('点击登录', '立即登录', '请先登录', '未登录', '重新登录', '登录', 'Login', 'Log in', 'Sign in',
    '登录失效', '登录过期', '扫码登录', 'QQ登录', '微信登录', '短信登录', '安全验证'))
BUSY_LABELS = frozenset(('正在登录', '正在登录…', '正在登录...', '登录中', '登录中…', '登录中...'))
AMBIGUOUS_LOGIN_LABELS = frozenset(('登录', 'Login', 'Log in', 'Sign in'))
PLAYBAR_LABELS = frozenset(('播放', '暂停', '播放音乐', '暂停播放', 'Play', 'Pause',
                          '随机播放', '列表循环', '单曲循环', '顺序播放', '单曲播放'))


def classify_startup(controls):
    """明确登录/验证标记仍阻断；不同版本可用搜索框或播放栏第三个控件确认就绪。"""
    controls = tuple(controls)
    names = {name.strip() for _role, name in controls}
    if names & BUSY_LABELS:
        return 'signing-in'
    if names & (LOGIN_LABELS - AMBIGUOUS_LOGIN_LABELS):
        return 'login-required'
    if any(name.strip() in AMBIGUOUS_LOGIN_LABELS and role != 'TextControl' for role, name in controls):
        return 'login-required'
    if (names & {'上一曲', '上一首'} and names & {'下一曲', '下一首'}
            and (any(role in {'EditControl', 'ComboBoxControl'} for role, _name in controls)
                 or names & PLAYBAR_LABELS)):
        return 'ready'
    if names & AMBIGUOUS_LOGIN_LABELS:
        return 'login-required'
    return 'loading'


def running_qq_executables():
    """只读进程映像路径；不能确定已有客户端时拒绝再启动，不读取账户资料。"""
    import ctypes
    from ctypes import wintypes
    from .music import _windows_process_ids
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    paths = {}
    for pid in _windows_process_ids('QQMusic.exe'):
        process = kernel.OpenProcess(0x1000, False, pid)
        if not process:
            if pid in _windows_process_ids('QQMusic.exe'):
                raise QQClientNotReady('无法确认已运行 QQ 的版本，未启动另一客户端。请手动打开 QQ 后重试。')
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                raise QQClientNotReady('无法读取已运行 QQ 的程序位置，未重复启动。')
            path = Path(buffer.value)
            if path.name.casefold() != 'qqmusic.exe' or not path.is_file():
                raise QQClientNotReady('已运行 QQ 的程序位置暂不可用，未启动另一版本。')
            paths[str(path.resolve()).casefold()] = path
        finally:
            kernel.CloseHandle(process)
    return tuple(paths.values())


def require_running_qq(executable):
    if not music_client_windows(Path(executable)):
        raise QQClientNotReady('QQ 已关闭或正在重启，未将歌曲命令用作启动命令。请等 QQ 登录完成后重试。')


class WindowsQQStartup:
    def __init__(self, *, cancelled=lambda: False, running_paths=None, probe=None,
                 popen=None, clock=time.monotonic, sleep=time.sleep, timeout=25):
        self.cancelled = cancelled
        self.running_paths = running_paths or running_qq_executables
        self.probe = probe or self._probe
        self.popen = popen or subprocess.Popen
        self.clock, self.sleep, self.timeout = clock, sleep, timeout

    def _probe(self, executable):
        from .qqmusic_queue_mode import WindowsQQModeBackend
        return WindowsQQModeBackend(lambda: executable, cancelled=self.cancelled)._request('startup')

    def ensure(self, executable):
        paths = self.running_paths()
        if len(paths) > 1:
            raise QQClientNotReady('检测到多个 QQ 音乐版本，未继续投递。请只保留你正在使用的一个 QQ。')
        cold = not paths
        client = Path(paths[0] if paths else executable)
        started = self.clock()
        if self.cancelled():
            raise QQClientNotReady('音乐操作已取消，未请求播放。')
        if cold:
            # The normal executable is the same as the user's shortcut: no autorun,
            # account, URL, song, queue or mode arguments during login restoration.
            self.popen([str(client)], cwd=str(client.parent), shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                **qq_background_startup_options(client))
        deadline, stable, blocked, state = started + self.timeout, 0, 0, 'loading'
        while not self.cancelled():
            try:
                state = self.probe(client)
            except OSError:
                state = 'unavailable'
            stable = stable + 1 if state == 'ready' else 0
            blocked = blocked + 1 if state == 'login-required' else 0
            if stable >= 2 and not self.cancelled() and (not cold or self.clock() - started >= 2):
                return client
            if (not cold and blocked >= 2) or self.clock() >= deadline:
                break
            self.sleep(min(.35, max(0, deadline - self.clock())))
        if self.cancelled():
            raise QQClientNotReady('音乐操作已取消，未请求播放。')
        if state == 'signing-in':
            detail = 'QQ 界面仍显示正在登录，请等待客户端登录恢复完成后重试。'
        elif state == 'login-required':
            detail = 'QQ 界面持续显示登录或验证提示，请先在 QQ 完成对应操作后重试。'
        elif state == 'unavailable':
            detail = '已找到 QQ，但界面读取失败或超时，无法确认就绪状态。请展开 QQ 主窗口，确认桌面可操作，并让 QQ 与六毛使用相同运行权限后重试。'
        else:
            detail = '已找到 QQ，但暂未识别到可用播放栏；这不表示账号未登录。请展开 QQ 主窗口后重试。'
        raise QQClientNotReady(detail + '未发送歌单、播放或模式命令，也未退出或重新登录账号。')
