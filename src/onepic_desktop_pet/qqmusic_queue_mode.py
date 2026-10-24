"""QQ 模式读回兼容首页与歌曲详情页，优先遍历浅层播放栏并确认相邻切歌控件。"""
from __future__ import annotations

from pathlib import Path
from collections import deque
import json
import subprocess
import sys
import tempfile
import time

MODES = frozenset(('随机播放', '列表循环', '单曲循环', '顺序播放', '单曲播放'))
SINGLE = '单曲循环'
NATIVE_MODES = {SINGLE: 1, '顺序播放': 2, '列表循环': 3, '随机播放': 4}
MODE_SETUP_MESSAGE = ('已找到 QQ 音乐，但没能确认“单曲循环”模式。'
    '请展开 QQ 音乐，在底部播放栏选择“单曲循环”，再点一次随机听陈楚生。'
    '已请求暂停，避免原队列插入其他歌曲。')


def dispatch_native_mode(executable, target, *, popen=None):
    """只向已运行且完整路径匹配的 QQ 投递已实测的模式值；投递不等于确认。"""
    from .music_background import music_client_windows
    if target not in NATIVE_MODES or executable is None:
        return False
    executable = Path(executable)
    if (executable.name.casefold() != 'qqmusic.exe' or not executable.is_file()
            or not music_client_windows(executable)):
        return False
    # Do not wait for or terminate the client: an existing process can exit
    # between discovery and dispatch, turning this invocation into a new client.
    (popen or subprocess.Popen)([str(executable), '/playmode', str(NATIVE_MODES[target])],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        shell=False, creationflags=0x08000000 if sys.platform == 'win32' else 0)
    return True


class QQQueueMode:
    """以明确读回的单曲循环隔离客户端队列，不把点击成功当作模式成功。"""

    def __init__(self, backend, cancelled=lambda: False):
        self.backend, self.cancelled = backend, cancelled
        self.original = None
        self.isolated = False
        self.last_error = ''

    def acquire(self):
        try:
            if self.cancelled():
                raise OSError('音乐操作已取消。')
            current = self.backend.read()
            if current not in MODES:
                raise OSError('QQ 未公开明确的播放模式。')
            if self.original is None:
                self.original = current
            if current != SINGLE and not self.backend.set(SINGLE):
                raise OSError('QQ 未接受单曲模式。')
            self.isolated = not self.cancelled() and self.backend.read() == SINGLE
            if not self.isolated:
                raise OSError('未确认 QQ 单曲模式。')
            self.last_error = ''
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.release(force=True)
            return False

    def release(self, *, force=False):
        self.isolated = False
        if self.original is None:
            return True
        original = self.original
        try:
            # The user can change QQ while listening. Do not overwrite a mode
            # they selected after we acquired the client.
            current = self.backend.read()
            if (current == SINGLE or force) and original != current:
                if not self.backend.set(original) or self.backend.read() != original:
                    return False
            self.original = None
            return True
        except Exception:
            return False


class WindowsQQModeBackend:
    """每次请求用独立限时进程访问 UIA，避免 COM 指针跨 Qt 线程和调用残留。"""

    def __init__(self, executable, cancelled=lambda: False):
        self.executable, self.cancelled = executable, cancelled

    def _request(self, action, target=''):
        with tempfile.TemporaryDirectory(prefix='lili-qq-mode-') as directory:
            result = Path(directory)/'reply.json'
            executable = self.executable()
            args = [action, str(executable or ''), target, str(result)]
            command = ([sys.executable, '--lili-qq-mode-worker'] if getattr(sys, 'frozen', False)
                       else [sys.executable, '-m', 'onepic_desktop_pet.qqmusic_mode_worker']) + args
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == 'win32' else 0)
            deadline = time.monotonic() + (10 if action == 'set' else 6)
            try:
                while process.poll() is None:
                    if self.cancelled() or time.monotonic() >= deadline:
                        raise OSError('QQ 播放模式访问已取消或超时。')
                    time.sleep(0.04)
                if not result.is_file():
                    raise OSError('QQ 模式辅助未返回结果。')
                reply = json.loads(result.read_text(encoding='utf-8'))
                if not reply.get('ok'):
                    raise OSError(reply.get('error') or 'QQ 播放模式暂不可用。')
                return reply.get('value')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)

    def read(self):
        return self._request('read')

    def set(self, target):
        return self._request('set', target) is True


class _WindowsQQModeNativeBackend:
    """优先投递原生命令并读回模式，兼容回退到标准可访问性动作，不移动鼠标。"""

    def __init__(self, executable, cancelled=lambda: False):
        self.executable, self.cancelled = executable, cancelled

    def _roots(self, auto):
        from .music_background import music_client_windows
        executable = self.executable()
        if executable is None:
            raise OSError('未找到已配置的 QQ 音乐。')
        roots = []
        for hwnd in music_client_windows(Path(executable)):
            try:
                root = auto.ControlFromHandle(hwnd)
                if root is not None:
                    roots.append(root)
            except Exception:
                # A native command briefly creates helper windows which can
                # close between EnumWindows and ElementFromHandle.
                continue
        return roots

    def _controls(self, auto):
        pending = deque((root, 0) for root in self._roots(auto))
        deadline, visited = time.monotonic() + 2.5, 0
        while pending and visited < 2500 and time.monotonic() < deadline and not self.cancelled():
            control, depth = pending.popleft()
            visited += 1
            try:
                if control.ControlTypeName in {'ButtonControl', 'MenuItemControl'}:
                    name = control.Name.strip()
                    if name in MODES:
                        yield name, control
                if depth < 45:
                    pending.extend((child, depth + 1) for child in control.GetChildren())
            except Exception:
                continue  # QQ can rebuild the playback bar while changing modes.

    def read(self):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            modes = set()
            for name, control in self._controls(auto):
                try:
                    if control.ControlTypeName != 'ButtonControl':
                        continue
                    parent = control.GetParentControl()
                    region = parent.GetParentControl() if parent is not None else None
                    labels = {child.Name for child in region.GetChildren()} if region is not None else set()
                    if (labels & {'下一曲', '下一首'} and labels & {'上一曲', '上一首'}):
                        modes.add(name)
                except Exception:
                    continue
            if len(modes) == 1:
                return modes.pop()
            raise OSError('QQ 播放栏模式按钮不可访问。')

    def _invoke(self, auto, control):
        last_error = None
        for pattern_id, method in ((auto.PatternId.InvokePattern, 'Invoke'),
                                   (auto.PatternId.LegacyIAccessiblePattern, 'DoDefaultAction')):
            try:
                pattern = control.GetPattern(pattern_id)
                if pattern is not None and getattr(pattern, method)() is True:
                    return
            except Exception as exc:
                last_error = exc
        raise OSError('QQ 拒绝后台切换播放模式，请先在 QQ 中选择单曲循环。') from last_error

    def set(self, target):
        if target not in MODES:
            return False
        if self.cancelled():
            return False
        try:
            accepted = dispatch_native_mode(self.executable(), target)
        except OSError:
            accepted = False
        if accepted:
            deadline = time.monotonic() + 1.5
            while not self.cancelled():
                try:
                    if self.read() == target:
                        return True
                except Exception:
                    pass  # A transient detached UIA node is not a mode readback.
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
        if self.cancelled():
            return False
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            for _ in range(5):
                if self.cancelled():
                    return False
                controls = list(self._controls(auto))
                buttons = [(name, control) for name, control in controls
                           if control.ControlTypeName == 'ButtonControl']
                if len(buttons) == 1 and buttons[0][0] == target:
                    return True
                choices = [control for name, control in controls if name == target]
                if len(choices) == 1:
                    self._invoke(auto, choices[0])
                elif len(buttons) == 1:
                    self._invoke(auto, buttons[0][1])
                else:
                    return False
                time.sleep(0.15)
        return False


class MacQQModeBackend:
    """试验性 AX 模式适配；未公开唯一模式按钮时明确失败。"""

    def __init__(self, bridge):
        self.bridge = bridge

    def _controls(self):
        _app, tree = self.bridge._read()
        return [n for n in tree.descendants() if n.role in {'AXButton', 'AXMenuItem'} and n.text in MODES]

    def read(self):
        buttons = [n for n in self._controls() if n.role == 'AXButton']
        if len(buttons) != 1:
            raise OSError('当前 Mac QQ 未公开唯一的播放模式按钮。')
        return buttons[0].text

    def set(self, target):
        for _ in range(5):
            if self.bridge.cancelled():
                return False
            controls = self._controls()
            buttons = [n for n in controls if n.role == 'AXButton']
            if len(buttons) == 1 and buttons[0].text == target:
                return True
            choices = [n for n in controls if n.text == target]
            node = choices[0] if len(choices) == 1 else buttons[0] if len(buttons) == 1 else None
            if node is None or not self.bridge.backend.action(node):
                return False
            self.bridge.sleep(0.15)
        return False
