"""实验性 Mac QQ 适配：保留已有窗口，只为未运行客户端请求隐藏启动，不发送全局按键。

官方 Mac 客户端没有播放脚本字典。只在唯一 QQ 进程、明确播放栏和真实时间轴均可
读取时确认播放；缺少权限或控件不匹配时明确失败，不猜测播放状态或改控其他应用。
广度优先读取公开 AX 属性，兼容搜索子角色、占位文字与明确搜索按钮；只读自检不操作播放器。
搜索前等待明确登录标记消失和搜索框就绪，不在登录恢复或验证界面输入歌曲。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
import platform
import plistlib
import re
import time

from .qqmusic_online import artist_names, canonical
from .qqmusic_startup import QQClientNotReady, LOGIN_LABELS, BUSY_LABELS

BUNDLE_ID = 'com.tencent.QQMusicMac'
PLAY = frozenset(('播放', '播放音乐', 'Play', 'Play music'))
PAUSE = frozenset(('暂停', '暂停播放', 'Pause', 'Pause playback'))
MODE_ROLES = frozenset(('AXButton', 'AXPopUpButton', 'AXMenuButton'))
MODE_LABELS = frozenset(('顺序播放', '列表循环', '单曲循环', '随机播放', '单曲播放'))


@dataclass
class AXNode:
    role: str
    text: str = ''
    identifier: str = ''
    children: list = field(default_factory=list)
    native: object = None
    subrole: str = ''

    def descendants(self):
        yield self
        for child in self.children:
            yield from child.descendants()


def clock_ms(text):
    value = str(text).strip()
    if not re.fullmatch(r'-?\d{1,3}:\d{2}(?::\d{2})?', value):
        return None
    sign = -1 if value.startswith('-') else 1
    seconds = 0
    for part in value.lstrip('-').split(':'):
        seconds = seconds * 60 + int(part)
    return sign * seconds * 1000


def player_region(root):
    """只接受小范围播放栏，排除搜索结果、登录窗和包含多套播放按钮的整页。"""
    candidates = []
    for node in root.descendants():
        if node.role not in {'AXGroup', 'AXToolbar', 'AXWindow'}:
            continue
        children = list(node.descendants())
        if len(children) > 100:
            continue
        buttons = [n for n in children if n.role == 'AXButton' and playback_label(n.text) in PLAY | PAUSE]
        times = [clock_ms(n.text) for n in children if n.role == 'AXStaticText' and clock_ms(n.text) is not None]
        if len(buttons) == 1 and len(times) == 2:
            candidates.append((len(children), node, buttons[0], times))
    if not candidates:
        return None
    candidates.sort(key=lambda value: value[0])
    return candidates[0][1:]


def playback_label(text):
    """只放宽明确的快捷键/辅助描述后缀，不把“播放全部”当成播放器状态。"""
    value = str(text).strip()
    if value in PLAY | PAUSE:
        return value
    match = re.fullmatch(r'(播放|暂停|Play|Pause)\s*[（(:,，：].+', value)
    return match[1] if match else value


def text_fields(node):
    seen, result = set(), []
    for child in node.descendants():
        if child.role not in {'AXStaticText', 'AXLink'} or not child.text or child.text in seen:
            continue
        seen.add(child.text)
        result.append(child)
    return result


def search_fields(tree):
    return [n for n in tree.descendants() if n.role in {'AXSearchField', 'AXTextField', 'AXComboBox'}
            and (n.role == 'AXSearchField' or n.subrole == 'AXSearchField'
                 or re.search(r'搜索|search', n.text + n.identifier, re.I))]


def parse_track(root):
    from .music_control import TrackInfo
    region = player_region(root)
    if region is None:
        return None
    node, button, times = region
    fields = [n for n in text_fields(node)
              if clock_ms(n.text) is None and n.text not in {'HQ', 'SQ', '音质', '歌词'}]
    title = next((n.text for n in fields if re.search(r'(song|track).*title|songname', n.identifier, re.I)), '')
    artist = next((n.text for n in fields if re.search(r'artist|singer', n.identifier, re.I)), '')
    if (not title or not artist) and len(fields) == 2:
        title, artist = fields[0].text, fields[1].text
    if not title or not artist:
        return None
    time_labels = [n.text for n in node.descendants() if n.role == 'AXStaticText' and clock_ms(n.text) is not None]
    remaining = next((i for i, label in enumerate(time_labels) if label.startswith('-')), None)
    if remaining is None:
        duration, position = max(times), min(times)
    else:
        position = max(0, times[1 - remaining])
        duration = position + abs(times[remaining])
    return TrackInfo(title, artist, '', 'playing' if playback_label(button.text) in PAUSE else 'paused',
                     max(0, min(position, duration)), duration, metadata_scope='mac-accessibility')


class NativeAX:
    """懒加载公开的 Cocoa / ApplicationServices；不申请或修改系统权限。"""
    def __init__(self, cancelled=lambda: False):
        import ApplicationServices as ax
        from AppKit import NSRunningApplication
        self.ax, self.apps = ax, NSRunningApplication
        self.cancelled = cancelled
        self.truncated = False
        ax.AXUIElementSetMessagingTimeout(ax.AXUIElementCreateSystemWide(), 0.12)

    def application(self):
        if not self.ax.AXIsProcessTrusted():
            raise PermissionError('[Mac QQ / 权限] 请在 macOS“系统设置 → 隐私与安全性 → 辅助功能”允许当前 Lili；升级后旧授权可能需要为当前版本重新选择，然后完全退出并重新打开六毛。')
        apps = list(self.apps.runningApplicationsWithBundleIdentifier_(BUNDLE_ID))
        if len(apps) != 1:
            raise OSError('[Mac QQ / 进程] 请打开并登录 QQ 音乐；需要唯一的 QQ 音乐进程。')
        return apps[0], self.ax.AXUIElementCreateApplication(apps[0].processIdentifier())

    def attribute(self, element, name, default=None):
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 and value is not None else default

    def attributes(self, element, names):
        try:
            error, values = self.ax.AXUIElementCopyMultipleAttributeValues(element, names, 0, None)
            if error == 0 and values is not None and len(values) == len(names):
                return dict(zip(names, values))
        except (AttributeError, TypeError):
            pass
        return {name: self.attribute(element, name) for name in names}

    def tree(self, root):
        deadline, count = time.monotonic() + 1.8, 0
        pending = deque([(root, None, 0)])
        result = None
        self.truncated = False
        names = ('AXRole', 'AXSubrole', 'AXValue', 'AXTitle', 'AXDescription',
                 'AXPlaceholderValue', 'AXIdentifier')
        while pending and count < 1600 and time.monotonic() < deadline and not self.cancelled():
            element, parent, depth = pending.popleft()
            if depth > 24:
                self.truncated = True
                continue
            count += 1
            attrs = self.attributes(element, names)
            # Unsupported attributes may be AXValue error wrappers, not strings.
            string = lambda key: attrs.get(key) if isinstance(attrs.get(key), str) else ''
            role = string('AXRole')
            text = string('AXValue') if role in {'AXStaticText', 'AXLink'} else ''
            text = text or string('AXTitle') or string('AXDescription') or string('AXPlaceholderValue')
            if role in MODE_ROLES:
                text = next((value for value in (string('AXValue'), string('AXDescription'), text)
                             if value in MODE_LABELS | PLAY | PAUSE), text)
            node = AXNode(role, text[:600], string('AXIdentifier'), native=element, subrole=string('AXSubrole'))
            if parent is None:
                result = node
            else:
                parent.children.append(node)
            children = self.attribute(element, 'AXWindows' if role == 'AXApplication' else 'AXChildren', ())
            if children:
                pending.extend((child, node, depth + 1) for child in children)
        self.truncated = self.truncated or bool(pending)
        return result

    def ensure_running(self, executable):
        """用户已有 QQ 一律不动；冷启动使用 Cocoa 配置，不在稍后再次 hide。"""
        if not self.ax.AXIsProcessTrusted():
            self.application()  # Preserve the actionable permission error; do not request/grant access.
        apps = list(self.apps.runningApplicationsWithBundleIdentifier_(BUNDLE_ID))
        if apps:
            if len(apps) != 1:
                raise OSError('[Mac QQ / 进程] 检测到多个 QQ，请保留一个客户端后重试。')
            return False
        from AppKit import NSWorkspace, NSWorkspaceOpenConfiguration
        from Foundation import NSURL
        config = NSWorkspaceOpenConfiguration.configuration()
        config.setActivates_(False)
        config.setHides_(True)
        NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_(
            NSURL.fileURLWithPath_(str(executable)), config, None)
        return True

    def action_names(self, node):
        error, names = self.ax.AXUIElementCopyActionNames(node.native, None)
        return tuple(names or ()) if error == 0 else ()

    def action(self, node, action='AXPress'):
        return bool(action in self.action_names(node)
                    and self.ax.AXUIElementPerformAction(node.native, action) == 0)

    def search(self, node, text):
        return self.ax.AXUIElementSetAttributeValue(node.native, 'AXValue', text) == 0


class QQMusicMacBridge:
    def __init__(self, *, backend=None, sleep=time.sleep,
                 clock=time.monotonic, cancelled=lambda: False):
        self.backend = backend
        self.sleep, self.clock, self.cancelled = sleep, clock, cancelled
        self.last_error = ''

    def _read(self):
        if self.backend is None:
            self.backend = NativeAX(cancelled=self.cancelled)
        app, native = self.backend.application()
        tree = self.backend.tree(native)
        if tree is None:
            raise OSError('[Mac QQ / 窗口] QQ 暂未公开可读取内容，请先打开客户端主窗口。')
        return app, tree

    def session(self):
        from .music_control import WindowsSessionSnapshot
        try:
            _app, tree = self._read()
            track = parse_track(tree)
            if track is None:
                raise OSError('[Mac QQ / 播放栏] 未读到明确歌名、歌手、播放状态和时间轴。请在 QQ 主窗口播放一次，再点“检查 QQ 适配”。')
            self.last_error = ''
            return WindowsSessionSnapshot(BUNDLE_ID, track, frozenset(('play', 'pause', 'toggle')))
        except (OSError, ImportError, AttributeError) as exc:
            self.last_error = str(exc)
            return None

    def control(self, source_id, action):
        if source_id != BUNDLE_ID or action not in {'play', 'pause', 'toggle'} or self.cancelled():
            return False
        try:
            _app, tree = self._read()
            region = player_region(tree)
            if region is None or parse_track(tree) is None:
                return False
            _node, button, _times = region
            playing = playback_label(button.text) in PAUSE
            if (action == 'pause' and not playing) or (action == 'play' and playing):
                return True
            return not self.cancelled() and self.backend.action(button)
        except (OSError, ImportError, AttributeError) as exc:
            self.last_error = str(exc)
            return False

    def play_candidate(self, executable: Path, selected):
        """在 QQ 自己的搜索框精确选曲；不猜测未经证实的私有播放 URI。"""
        try:
            info = plistlib.loads((executable/'Contents/Info.plist').read_bytes())
        except (OSError, ValueError) as exc:
            raise OSError('请在设置中选择 QQMusic.app。') from exc
        if info.get('CFBundleIdentifier') != BUNDLE_ID:
            raise OSError('所选应用不是 QQ 音乐。')
        if self.cancelled():
            raise OSError('音乐操作已取消。')
        if self.backend is None:
            self.backend = NativeAX(cancelled=self.cancelled)
        ensure = getattr(self.backend, 'ensure_running', None)
        launched = bool(ensure(executable)) if callable(ensure) else False
        deadline = self.clock() + 25
        stable = 0
        while True:
            if self.cancelled():
                raise OSError('音乐操作已取消。')
            try:
                _app, tree = self._read()
                labels = {n.text.strip() for n in tree.descendants()}
                blocked = bool(labels & (LOGIN_LABELS | BUSY_LABELS))
                stable = stable + 1 if not blocked and len(search_fields(tree)) == 1 else 0
                if stable >= 2:
                    break
                if not launched and not blocked and len(search_fields(tree)) != 1:
                    break  # Preserve the precise search-control diagnostic, without waiting for login.
                if (not launched and labels & LOGIN_LABELS) or self.clock() >= deadline:
                    raise QQClientNotReady('[Mac QQ / 登录] QQ 尚未登录或界面未就绪，未输入搜索或请求播放。请在 QQ 完成登录后重试。')
            except PermissionError:
                raise
            except QQClientNotReady:
                raise
            except OSError:
                if not launched or self.clock() >= deadline:
                    raise
            self.sleep(.25)
        fields = search_fields(tree)
        title = re.split(r'[（(]', selected.title, maxsplit=1)[0].strip()
        if len(fields) != 1 or not self.backend.search(fields[0], f'陈楚生 {title}'):
            raise OSError(f'[Mac QQ / 搜索框] 找到 {len(fields)} 个可识别搜索框；请打开 QQ 主窗口、确认已登录，再运行适配检查。')
        if not self.backend.action(fields[0], 'AXConfirm'):
            # A real search button is a supported fallback; never send Return to another app.
            _app, current = self._read()
            buttons = [n for n in current.descendants() if n.role == 'AXButton' and n.text in {'搜索', 'Search', '执行搜索'}]
            if len(buttons) != 1 or not self.backend.action(buttons[0]):
                raise OSError('[Mac QQ / 搜索确认] 搜索框不支持 AXConfirm，且没有唯一的可点击搜索按钮；未发送全局按键。')
        deadline = self.clock() + 4
        while not self.cancelled() and self.clock() < deadline:
            _app, tree = self._read()
            for node in tree.descendants():
                if node.role not in {'AXGroup', 'AXRow', 'AXCell'}:
                    continue
                elements = list(node.descendants())
                if len(elements) > 45:
                    continue
                texts = [n.text for n in text_fields(node)]
                artists = frozenset().union(*(artist_names(t) for t in texts))
                if not any(canonical(t) == canonical(selected.title) for t in texts) or not artist_names(selected.artist) <= artists:
                    continue
                buttons = [n for n in elements if n.role == 'AXButton'
                           and n.text in PLAY | {'播放歌曲', 'Play song'}]
                if len(buttons) == 1 and not self.cancelled() and self.backend.action(buttons[0]):
                    return
            self.sleep(0.25)
        raise OSError('[Mac QQ / 搜索结果] 未找到同时匹配完整版本歌名、歌手和播放按钮的歌曲行；没有改播其他内容。')

    def diagnostics(self, executable):
        """仅在本机汇总版本、授权和控件能力；不启动、播放、隐藏、改模式或读取凭据。"""
        from .music import normalize_music_client_path
        from . import __version__
        lines = [f'Lili {__version__} · macOS {platform.mac_ver()[0] or "未知"} · {platform.machine()}', '检查不会播放、暂停或改变 QQ 窗口。']
        app = normalize_music_client_path('qq', executable, platform_name='darwin')
        if app is None:
            return '\n'.join(lines + ['[应用选择] 未找到有效 QQMusic.app，请重新选择已安装的 QQ。'])
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        lines.append('QQ 音乐版本：' + str(info.get('CFBundleShortVersionString', '未知')))
        try:
            _app, tree = self._read()
            region = player_region(tree)
            track = parse_track(tree)
            fields = search_fields(tree)
            lines.append('辅助功能：已获授权；QQ 进程：唯一且可读取')
            lines.append(f'搜索框：{len(fields)} 个；播放栏：{"可识别" if region else "未识别"}；时间轴与歌曲：{"可读取" if track else "未识别"}')
            names = getattr(self.backend, 'action_names', None)
            confirm = bool(len(fields) == 1 and callable(names) and 'AXConfirm' in names(fields[0]))
            search_buttons = [n for n in tree.descendants() if n.role == 'AXButton' and n.text in {'搜索', 'Search', '执行搜索'}]
            lines.append(f'搜索确认：AXConfirm {"可用" if confirm else "未公开"}；明确搜索按钮：{len(search_buttons)} 个')
            modes = [n.text for n in tree.descendants() if n.role in MODE_ROLES and n.text in MODE_LABELS]
            lines.append('播放模式：' + (modes[0] if len(modes) == 1 else '未读到唯一按钮'))
            if getattr(self.backend, 'truncated', False):
                lines.append('界面读取达到限额；请切回 QQ 主窗口后重试。')
            lines.append('以上仅检查控件能力，尚未验证歌曲实际播放和连续换轮。')
        except (OSError, ImportError, AttributeError) as exc:
            lines.append(str(exc))
        return '\n'.join(lines)
