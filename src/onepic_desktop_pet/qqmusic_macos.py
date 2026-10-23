"""实验性 macOS QQ 后台适配：仅用已授权 AX 控件，不激活窗口、不发送全局按键。

官方 Mac 客户端没有播放脚本字典。只在唯一 QQ 进程、明确播放栏和真实时间轴均可
读取时确认播放；缺少权限或控件不匹配时明确失败，不猜测播放状态或改控其他应用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import plistlib
import re
import time

from .qqmusic_online import artist_names, canonical

BUNDLE_ID = 'com.tencent.QQMusicMac'
PLAY = frozenset(('播放', '播放音乐', 'Play', 'Play music'))
PAUSE = frozenset(('暂停', '暂停播放', 'Pause', 'Pause playback'))


@dataclass
class AXNode:
    role: str
    text: str = ''
    identifier: str = ''
    children: list = field(default_factory=list)
    native: object = None

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
        buttons = [n for n in children if n.role == 'AXButton' and n.text in PLAY | PAUSE]
        times = [clock_ms(n.text) for n in children if n.role == 'AXStaticText' and clock_ms(n.text) is not None]
        if len(buttons) == 1 and len(times) == 2:
            candidates.append((len(children), node, buttons[0], times))
    if not candidates:
        return None
    candidates.sort(key=lambda value: value[0])
    return candidates[0][1:]


def parse_track(root):
    from .music_control import TrackInfo
    region = player_region(root)
    if region is None:
        return None
    node, button, times = region
    fields = [n for n in node.descendants() if n.role == 'AXStaticText' and n.text
              and clock_ms(n.text) is None and n.text not in {'HQ', 'SQ', '音质', '歌词'}]
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
    return TrackInfo(title, artist, '', 'playing' if button.text in PAUSE else 'paused',
                     max(0, min(position, duration)), duration, metadata_scope='mac-accessibility')


class NativeAX:
    """懒加载公开的 Cocoa / ApplicationServices；不申请或修改系统权限。"""
    def __init__(self):
        import ApplicationServices as ax
        from AppKit import NSRunningApplication
        self.ax, self.apps = ax, NSRunningApplication
        ax.AXUIElementSetMessagingTimeout(ax.AXUIElementCreateSystemWide(), 0.12)

    def application(self):
        if not self.ax.AXIsProcessTrusted():
            raise PermissionError('请在 macOS“系统设置 → 隐私与安全性 → 辅助功能”允许 Lili 控制 QQ 音乐，然后重试。')
        apps = list(self.apps.runningApplicationsWithBundleIdentifier_(BUNDLE_ID))
        if len(apps) != 1:
            raise OSError('请先打开并登录 QQ 音乐；需要唯一的 QQ 音乐进程。')
        return apps[0], self.ax.AXUIElementCreateApplication(apps[0].processIdentifier())

    def attribute(self, element, name, default=None):
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 and value is not None else default

    def tree(self, root):
        deadline, count = time.monotonic() + 1.25, [0]
        def visit(element, depth):
            if depth > 12 or count[0] >= 450 or time.monotonic() >= deadline:
                return None
            count[0] += 1
            role = str(self.attribute(element, 'AXRole', ''))
            value = self.attribute(element, 'AXValue', '')
            text = str(value) if role == 'AXStaticText' and isinstance(value, str) else ''
            if not text:
                text = str(self.attribute(element, 'AXTitle', '') or self.attribute(element, 'AXDescription', ''))
            node = AXNode(role, text[:600], str(self.attribute(element, 'AXIdentifier', '')), native=element)
            attribute = 'AXWindows' if role == 'AXApplication' else 'AXChildren'
            for child in list(self.attribute(element, attribute, ())):
                result = visit(child, depth + 1)
                if result is not None:
                    node.children.append(result)
            return node
        return visit(root, 0)

    def action(self, node, action='AXPress'):
        error, names = self.ax.AXUIElementCopyActionNames(node.native, None)
        return bool(error == 0 and action in (names or ())
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
            self.backend = NativeAX()
        app, native = self.backend.application()
        tree = self.backend.tree(native)
        if tree is None:
            raise OSError('QQ 音乐暂未公开可读取的窗口内容。')
        return app, tree

    def session(self):
        from .music_control import WindowsSessionSnapshot
        try:
            _app, tree = self._read()
            track = parse_track(tree)
            if track is None:
                raise OSError('QQ 音乐未公开清晰的播放栏，请先在客户端播放一次，再重试。')
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
            playing = button.text in PAUSE
            if (action == 'pause' and not playing) or (action == 'play' and playing):
                return True
            return not self.cancelled() and self.backend.action(button)
        except (OSError, ImportError, AttributeError) as exc:
            self.last_error = str(exc)
            return False

    def background(self, _executable):
        try:
            if self.backend is None:
                self.backend = NativeAX()
            app, _native = self.backend.application()
            app.hide()
        except (OSError, ImportError, AttributeError):
            pass

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
        # 先打开并登录 QQ 是前提；不重复唤起客户端，避免唤起动作恢复旧音乐或抢焦点。
        _app, tree = self._read()
        fields = [n for n in tree.descendants() if n.role in {'AXSearchField', 'AXTextField'}
                  and (n.role == 'AXSearchField' or re.search(r'搜索|search', n.text + n.identifier, re.I))]
        if len(fields) != 1 or not self.backend.search(fields[0], f'{selected.artist} {selected.title}'):
            raise OSError('当前 QQ 版本未公开唯一的后台搜索框，请先打开客户端主窗口后重试。')
        if not self.backend.action(fields[0], 'AXConfirm'):
            raise OSError('当前 QQ 搜索框不支持后台确认；未切换到前台或发送全局按键。')
        deadline = self.clock() + 4
        while not self.cancelled() and self.clock() < deadline:
            _app, tree = self._read()
            for node in tree.descendants():
                if node.role not in {'AXGroup', 'AXRow'}:
                    continue
                elements = list(node.descendants())
                if len(elements) > 45:
                    continue
                texts = [n.text for n in elements if n.role == 'AXStaticText']
                artists = frozenset().union(*(artist_names(t) for t in texts))
                if not any(canonical(t) == canonical(selected.title) for t in texts) or not artist_names(selected.artist) <= artists:
                    continue
                buttons = [n for n in elements if n.role == 'AXButton'
                           and n.text in PLAY | {'播放歌曲', 'Play song'}]
                if len(buttons) == 1 and not self.cancelled() and self.backend.action(buttons[0]):
                    return
            self.sleep(0.25)
        raise OSError('未找到同时匹配完整歌名和歌手的 QQ 播放按钮，未改播其他内容。')
