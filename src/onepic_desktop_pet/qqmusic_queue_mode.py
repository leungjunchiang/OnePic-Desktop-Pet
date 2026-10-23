"""QQ 模式使用限时独立进程核验以隔离旧队列；暂停尝试恢复原模式，不在 Qt 内执行 UIA。"""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time

MODES = frozenset(('随机播放', '列表循环', '单曲循环', '顺序播放', '单曲播放'))
SINGLE = '单曲循环'


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
    """对完整路径匹配的 QQ 播放栏调用标准可访问性动作，不移动鼠标或置前。"""

    def __init__(self, executable, cancelled=lambda: False):
        self.executable, self.cancelled = executable, cancelled

    def _roots(self, auto):
        from .music_background import music_client_windows
        executable = self.executable()
        if executable is None:
            raise OSError('未找到已配置的 QQ 音乐。')
        roots = [auto.ControlFromHandle(hwnd) for hwnd in music_client_windows(Path(executable))]
        return [root for root in roots if root is not None]

    def _controls(self, auto):
        stack = [(root, 0) for root in self._roots(auto)]
        deadline, visited = time.monotonic() + 2.5, 0
        while stack and visited < 2500 and time.monotonic() < deadline and not self.cancelled():
            control, depth = stack.pop()
            visited += 1
            if control.ControlTypeName in {'ButtonControl', 'MenuItemControl'}:
                name = control.Name.strip()
                if name in MODES:
                    yield name, control
            if depth < 45:
                stack.extend((child, depth + 1) for child in control.GetChildren())

    def read(self):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            for name, control in self._controls(auto):
                if control.ControlTypeName != 'ButtonControl':
                    continue
                parent = control.GetParentControl()
                region = parent.GetParentControl() if parent is not None else None
                labels = {child.Name for child in region.GetChildren()} if region is not None else set()
                if {'下一曲', '上一曲'} <= labels:
                    return name
            raise OSError('QQ 播放栏模式按钮不可访问。')

    def _invoke(self, auto, control):
        pattern = control.GetPattern(auto.PatternId.InvokePattern)
        try:
            if pattern is None:
                raise OSError('QQ 播放模式未提供后台 Invoke。')
            pattern.Invoke()
        except Exception as exc:
            legacy = control.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
            try:
                if legacy is None:
                    raise OSError('QQ 模式没有默认可访问性动作。')
                legacy.DoDefaultAction()
            except Exception:
                raise OSError('QQ 拒绝后台切换播放模式，请先在 QQ 中选择单曲循环。') from exc

    def set(self, target):
        if target not in MODES:
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
