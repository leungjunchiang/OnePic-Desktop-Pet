"""核对 QQ 模式并保留窗口；入口提示统一为QQ音乐渠道，兼容无搜索框与隐藏登录节点。"""
from __future__ import annotations

from .music_labels import QQ_RANDOM_LABEL

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
    f'请展开 QQ 音乐，在底部播放栏选择“单曲循环”，再点一次“{QQ_RANDOM_LABEL}”。'
    '已请求暂停，避免原队列插入其他歌曲。')


def mode_setup_message(target=SINGLE):
    if target == SINGLE:
        return MODE_SETUP_MESSAGE
    return ('已找到 QQ 音乐，但没能确认临时歌单的“顺序播放”模式。'
            f'请展开 QQ 音乐，在底部播放栏选择“顺序播放”，再点一次“{QQ_RANDOM_LABEL}”。'
            '已请求暂停，六毛歌单已保留。')


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
        shell=False, creationflags=0x08000000 if sys.platform == 'win32' else 0, cwd=str(executable.parent))
    return True


class QQQueueMode:
    """读回确认临时队列所需模式，暂停后恢复原模式，不把投递当作成功。"""

    def __init__(self, backend, cancelled=lambda: False, *, target=SINGLE):
        self.backend, self.cancelled = backend, cancelled
        self.target = target
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
            if current != self.target and not self.backend.set(self.target):
                raise OSError('QQ 未接受' + self.target + '模式。')
            self.isolated = not self.cancelled() and self.backend.read() == self.target
            if not self.isolated:
                raise OSError('未确认 QQ ' + self.target + '模式。')
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
            if (current == self.target or force) and original != current:
                if not self.backend.set(original) or self.backend.read() != original:
                    return False
            self.original = None
            return True
        except Exception:
            return False

    def abandon(self):
        """登录/进程失去就绪状态时仅放弃本机归属，绝不为恢复模式再拉起 QQ。"""
        self.original, self.isolated = None, False


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
        from .music_background import music_client_mode_windows
        executable = self.executable()
        if executable is None:
            raise OSError('未找到已配置的 QQ 音乐。')
        roots = []
        for hwnd in music_client_mode_windows(Path(executable)):
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
        roots = self._roots(auto)
        if auto is not None:
            try:
                controls = self._cached_controls(auto, roots)
            except Exception:
                controls = None
            if controls is not None:
                yield from controls
                return
        pending = deque((root, 0) for root in roots)
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

    def startup_state(self):
        """查询固定就绪标记；展开窗口排除隐藏节点，后台主窗口仍可只读检测。"""
        if self.executable() is None:
            return 'loading'
        import uiautomation as auto
        from uiautomation.uiautomation import _AutomationClient
        from .music_background import music_window_is_background
        from .qqmusic_startup import LOGIN_LABELS, BUSY_LABELS, PLAYBAR_LABELS, classify_startup
        with auto.UIAutomationInitializerInThread():
            api = _AutomationClient.instance().IUIAutomation
            condition = api.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, auto.ControlType.EditControl)
            condition = api.CreateOrCondition(condition, api.CreatePropertyCondition(
                auto.PropertyId.ControlTypeProperty, auto.ControlType.ComboBoxControl))
            names = LOGIN_LABELS | BUSY_LABELS | PLAYBAR_LABELS | {'上一曲', '上一首', '下一曲', '下一首'}
            for name in sorted(names):
                condition = api.CreateOrCondition(condition, api.CreatePropertyCondition(auto.PropertyId.NameProperty, name))
            cache = api.CreateCacheRequest()
            cache.AddProperty(auto.PropertyId.NameProperty)
            cache.AddProperty(auto.PropertyId.ControlTypeProperty)
            cache.AddProperty(auto.PropertyId.IsOffscreenProperty)
            controls = []
            for root in self._roots(auto):
                if self.cancelled():
                    return 'loading'
                background = root.IsOffscreen or music_window_is_background(root.NativeWindowHandle)
                root_name = root.Name.strip()
                if root_name in LOGIN_LABELS | BUSY_LABELS:
                    controls.append(('WindowControl', root_name))
                elements = root.Element.FindAllBuildCache(4, condition, cache)
                if elements is None:
                    continue
                if elements.Length > 150:
                    return 'loading'
                for index in range(elements.Length):
                    element = elements.GetElement(index)
                    if not background and element.CachedIsOffscreen:
                        continue
                    role = {auto.ControlType.EditControl: 'EditControl',
                            auto.ControlType.ComboBoxControl: 'ComboBoxControl',
                            auto.ControlType.TextControl: 'TextControl'}.get(element.CachedControlType, 'Other')
                    # Do not return or persist the current search-field value.
                    controls.append((role, '' if role in {'EditControl', 'ComboBoxControl'} else element.CachedName.strip()))
            return classify_startup(controls)

    def _cached_controls(self, auto, roots):
        """只在已核对路径的 QQ 窗口中原生查询五种模式名，不逐个跨进程枚举网页节点。"""
        from uiautomation.uiautomation import _AutomationClient
        api = _AutomationClient.instance().IUIAutomation
        names = sorted(MODES)
        condition = api.CreatePropertyCondition(auto.PropertyId.NameProperty, names[0])
        for name in names[1:]:
            condition = api.CreateOrCondition(condition, api.CreatePropertyCondition(auto.PropertyId.NameProperty, name))
        cache = api.CreateCacheRequest()
        cache.AddProperty(auto.PropertyId.NameProperty)
        controls = []
        for root in roots:
            if self.cancelled():
                return []
            elements = root.Element.FindAllBuildCache(4, condition, cache)  # TreeScope_Descendants
            if elements is None:
                continue
            if elements.Length > 30:
                raise OSError('QQ 暴露过多同名模式控件，不能确认播放栏。')
            for index in range(elements.Length):
                element = elements.GetElement(index)
                control = auto.Control.CreateControlFromElement(element)
                if control is not None:
                    controls.append((element.CachedName.strip(), control))
        return controls

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
        self.platform = 'darwin'

    def _controls(self):
        from .qqmusic_macos import MODE_ROLES, player_region
        _app, tree = self.bridge._read()
        region = player_region(tree)
        if region is None:
            raise OSError('[Mac QQ / 模式] 未识别播放栏，未修改其他位置的同名按钮。')
        # A mode menu can be a sibling popover, but the current button must belong to the player bar.
        buttons = [n for n in region[0].descendants() if n.role in MODE_ROLES and n.text in MODES]
        menus = [n for n in tree.descendants() if n.role == 'AXMenuItem' and n.text in MODES]
        return buttons + menus

    def read(self):
        buttons = [n for n in self._controls() if n.role != 'AXMenuItem']
        if len(buttons) != 1:
            raise OSError('[Mac QQ / 模式] QQ 播放栏未公开唯一的模式按钮，请运行“检查 QQ 适配”。')
        return buttons[0].text

    def set(self, target):
        for _ in range(5):
            if self.bridge.cancelled():
                return False
            controls = self._controls()
            buttons = [n for n in controls if n.role != 'AXMenuItem']
            if len(buttons) == 1 and buttons[0].text == target:
                return True
            choices = [n for n in controls if n.text == target]
            node = choices[0] if len(choices) == 1 else buttons[0] if len(buttons) == 1 else None
            if node is None:
                return False
            if not self.bridge.backend.action(node):
                if node.role not in {'AXPopUpButton', 'AXMenuButton'} or not self.bridge.backend.action(node, 'AXShowMenu'):
                    return False
            self.bridge.sleep(0.15)
        return False
