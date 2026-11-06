"""实验性 Mac QQ 适配：保留已有窗口；用户主动点歌时可见地冷启动 QQ，不发送全局按键。

官方 Mac 客户端没有播放脚本字典。只在唯一 QQ 进程、明确播放栏和真实时间轴均可
读取时确认播放；缺少权限或控件不匹配时明确失败，不猜测播放状态或改控其他应用。
广度优先读取公开 AX 属性，兼容搜索子角色、占位文字与明确搜索按钮；只读自检不操作播放器。
缺少无障碍权限时，引导用户使用六毛设置中的系统授权快捷入口。
搜索前等待明确登录标记消失和搜索框就绪，不在登录恢复或验证界面输入歌曲。
普通静态登录文字不覆盖完整播放栏证据；若客户端公开 AXHidden 则过滤隐藏子控件。
登录、登录中与窗口未就绪分别说明，只读诊断输出阶段，不推测账户是否退出。
搜索结果按完整标题、歌手及已知专辑筛选，按钮不唯一时不点击；播放栏公开专辑则读取核对。
QQ 11.10.5 搜索不可用时使用已实测的单曲 ID 入口，识别其简化播放栏并通过 QQ 自身菜单控制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import time

from .qqmusic_online import artist_names, canonical
from .qqmusic_startup import QQClientNotReady, LOGIN_LABELS, BUSY_LABELS, AMBIGUOUS_LOGIN_LABELS

BUNDLE_ID = 'com.tencent.QQMusicMac'
PLAY = frozenset(('播放', '播放音乐', 'Play', 'Play music'))
PAUSE = frozenset(('暂停', '暂停播放', 'Pause', 'Pause playback'))
MODE_ROLES = frozenset(('AXButton', 'AXPopUpButton', 'AXMenuButton'))
MODE_LABELS = frozenset(('顺序播放', '列表循环', '单曲循环', '随机播放', '单曲播放'))


def supports_song_id(info):
    return (info.get('CFBundleShortVersionString') == '11.10.5'
            and any('qqmusicmac' in item.get('CFBundleURLSchemes', ())
                    for item in info.get('CFBundleURLTypes', ())))


@dataclass
class AXNode:
    role: str
    text: str = ''
    identifier: str = ''
    children: list = field(default_factory=list)
    native: object = None
    subrole: str = ''
    hidden: bool = False
    value_settable: bool = False

    def descendants(self):
        if self.hidden and self.role not in {'AXApplication', 'AXWindow'}:
            return
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
    if len({id(value[2]) for value in candidates}) != 1:
        return None  # Two independent player bars do not establish one playback state.
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
    nodes = list(tree.descendants())
    explicit = [n for n in nodes if n.role in {'AXSearchField', 'AXTextField', 'AXComboBox'}
                and (n.role == 'AXSearchField' or n.subrole == 'AXSearchField'
                     or re.search(r'搜索|search', n.text + n.identifier, re.I))]
    if explicit:
        return explicit
    # Some QQ Music builds expose the search input as an unlabeled AXTextField.
    # Accept it only when it is the sole editable, unlabeled text field in the
    # QQ accessibility tree. This avoids typing into login/profile controls.
    editable = [n for n in nodes if n.role == 'AXTextField' and n.value_settable]
    if len(editable) == 1 and not editable[0].text.strip() and not editable[0].identifier.strip():
        return editable
    return []


def startup_state(tree):
    """不把普通登录静态字样当作退出账号；真正登录控件、窗口或验证仍阻断。"""
    nodes = [n for n in tree.descendants() if not n.hidden]
    labels = {n.text.strip() for n in nodes}
    if labels & BUSY_LABELS:
        return 'signing-in'
    if labels & (LOGIN_LABELS - AMBIGUOUS_LOGIN_LABELS):
        return 'login-required'
    if any(n.text.strip() in AMBIGUOUS_LOGIN_LABELS and n.role != 'AXStaticText' for n in nodes):
        return 'login-required'
    if labels & AMBIGUOUS_LOGIN_LABELS and parse_track(tree) is None:
        return 'login-required'
    return 'ready' if len(search_fields(tree)) == 1 else 'loading'


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
    album = next((n.text for n in fields if re.search(r'album', n.identifier, re.I)), '')
    identity_fields = [n for n in fields if not re.search(r'album', n.identifier, re.I)]
    if (not title or not artist) and len(identity_fields) == 2:
        title, artist = identity_fields[0].text, identity_fields[1].text
    if not title or not artist:
        return None
    time_labels = [n.text for n in node.descendants() if n.role == 'AXStaticText' and clock_ms(n.text) is not None]
    remaining = next((i for i, label in enumerate(time_labels) if label.startswith('-')), None)
    if remaining is None:
        duration, position = max(times), min(times)
    else:
        position = max(0, times[1 - remaining])
        duration = position + abs(times[remaining])
    return TrackInfo(title, artist, album, 'playing' if playback_label(button.text) in PAUSE else 'paused',
                     max(0, min(position, duration)), duration, metadata_scope='mac-accessibility')


def compact_track(root):
    """QQ 11.10.5 同栏公开曲名、歌手和播放按钮，但没有时间轴。"""
    from .music_control import TrackInfo
    matches = []
    for group in root.descendants():
        labels = [n.text for n in group.children if n.role == 'AXUnknown' and not n.hidden]
        if not {'上一首', '下一首'} <= set(labels) or not any(s.startswith('播放模式（') for s in labels):
            continue
        states = [s for s in labels if s in PLAY | PAUSE]
        songs = [m.groups() for s in labels
                 if (m := re.fullmatch(r'歌曲名[：:]\s*(.+?)\s+-\s+歌手名[：:]\s*(.+)', s))]
        if len(states) == len(songs) == 1:
            title, artist = (s.strip() for s in songs[0])
            if title and artist:
                matches.append(TrackInfo(title, artist, '', 'playing' if states[0] in PAUSE else 'paused',
                                         metadata_scope='mac-qq-compact'))
    return matches[0] if len(matches) == 1 else None


def matching_song_buttons(tree, selected):
    """返回去重的精确结果按钮；已知专辑必须在同一歌曲行出现，不能点击第一个同名版本。"""
    matches = {}
    album = canonical((selected.native or {}).get('album', ''))
    for node in tree.descendants():
        if node.role not in {'AXGroup', 'AXRow', 'AXCell'}:
            continue
        elements = list(node.descendants())
        if len(elements) > 45:
            continue
        texts = [n.text for n in text_fields(node)]
        artists = frozenset().union(*(artist_names(t) for t in texts))
        if (not any(canonical(t) == canonical(selected.title) for t in texts)
                or not artist_names(selected.artist) <= artists
                or album and not any(canonical(t) == album for t in texts)):
            continue
        buttons = [n for n in elements if n.role == 'AXButton'
                   and n.text in PLAY | {'播放歌曲', 'Play song'}]
        if len(buttons) == 1:
            matches[id(buttons[0])] = buttons[0]
    return tuple(matches.values())


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
            raise PermissionError(
                '[Mac QQ / 权限] 请在六毛设置的 QQ 音乐区域点击“打开无障碍权限设置”，'
                '在系统应用权限列表中允许 Lili，再退出并重新打开六毛。'
            )
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
                 'AXPlaceholderValue', 'AXIdentifier', 'AXHidden')
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
            value_settable = False
            if role == 'AXTextField':
                try:
                    error, settable = self.ax.AXUIElementIsAttributeSettable(
                        element, 'AXValue', None)
                    value_settable = error == 0 and bool(settable)
                except (AttributeError, TypeError):
                    pass
            node = AXNode(role, text[:600], string('AXIdentifier'), native=element, subrole=string('AXSubrole'),
                          hidden=attrs.get('AXHidden') is True, value_settable=value_settable)
            if parent is None:
                result = node
            else:
                parent.children.append(node)
            children = (() if node.hidden and role not in {'AXApplication', 'AXWindow'} else
                        self.attribute(element, 'AXWindows' if role == 'AXApplication' else 'AXChildren', ()))
            if children:
                pending.extend((child, node, depth + 1) for child in children)
        self.truncated = self.truncated or bool(pending)
        return result

    def ensure_running(self, executable):
        """用户已有 QQ 一律不动；主动点歌的冷启动要显示 QQ 并让用户看见。"""
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
        # This is a direct user action. A hidden, non-activating launch leaves QQ
        # invisible and prevents the user from completing login or seeing its UI.
        config.setActivates_(True)
        config.setHides_(False)
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

    def dispatch_song_id(self, executable, song_id):
        if not re.fullmatch(r'[1-9][0-9]{0,9}', song_id) or int(song_id) > 4294967295:
            raise ValueError('[Mac QQ / 歌曲 ID] 所选歌曲没有有效的 QQ 曲目 ID。')
        if self.cancelled():
            raise QQClientNotReady('音乐操作已取消，未请求播放。')
        url = f'qqmusicmac://cmd_count==1&&cmd_0==playsong&&id_0=={song_id}'
        try:
            subprocess.run(['/usr/bin/open', '-a', str(executable), url], check=True,
                           capture_output=True, timeout=5)
        except (subprocess.SubprocessError, OSError) as exc:
            raise OSError('[Mac QQ / 歌曲 ID] 未能向 QQ 音乐发送播放请求。') from exc

    def player_menu_action(self, root, playing):
        def children(node, role, title=None):
            return [el for el in self.attribute(node, 'AXChildren', ()) or ()
                    if self.attribute(el, 'AXRole') == role
                    and (title is None or self.attribute(el, 'AXTitle') == title)]

        bar = self.attribute(root, 'AXMenuBar')
        menus = children(bar, 'AXMenuBarItem', '播放控制') if bar is not None else []
        if len(menus) != 1:
            return False
        label = '暂停' if playing else '播放'
        items = [item for menu in children(menus[0], 'AXMenu') for item in children(menu, 'AXMenuItem', label)]
        return (len(items) == 1 and not self.cancelled()
                and self.action(AXNode('AXMenuItem', label, native=items[0])))


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
            track = parse_track(tree) or compact_track(tree)
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
                track = compact_track(tree)
                menu_action = getattr(self.backend, 'player_menu_action', None)
                if track is None or not callable(menu_action):
                    return False
                playing = track.playback_status == 'playing'
                return ((action == 'play' and playing) or (action == 'pause' and not playing)
                        or (not self.cancelled() and menu_action(tree.native, playing)))
            _node, button, _times = region
            playing = playback_label(button.text) in PAUSE
            if (action == 'pause' and not playing) or (action == 'play' and playing):
                return True
            return not self.cancelled() and self.backend.action(button)
        except (OSError, ImportError, AttributeError) as exc:
            self.last_error = str(exc)
            return False

    def play_candidate(self, executable: Path, selected):
        """优先使用可操作的搜索框；兼容已验证客户端的单曲 ID 入口。"""
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
        dispatch = getattr(self.backend, 'dispatch_song_id', None) if supports_song_id(info) else None
        deadline = self.clock() + 25
        stable = blocked_reads = 0
        while True:
            if self.cancelled():
                raise OSError('音乐操作已取消。')
            try:
                _app, tree = self._read()
                state = startup_state(tree)
                blocked = state in {'login-required', 'signing-in'}
                if not blocked and callable(dispatch) and not search_fields(tree) and not self.cancelled():
                    dispatch(executable, selected.identifier)
                    return
                stable = stable + 1 if state == 'ready' else 0
                blocked_reads = blocked_reads + 1 if state == 'login-required' else 0
                if stable >= 2:
                    break
                if not launched and not blocked and len(search_fields(tree)) != 1:
                    break  # Preserve the precise search-control diagnostic, without waiting for login.
                if (not launched and blocked_reads >= 2) or self.clock() >= deadline:
                    detail = ('QQ 界面持续显示登录或验证提示，请在 QQ 完成对应操作后重试。' if state == 'login-required' else
                              'QQ 界面仍显示正在登录，请等待客户端恢复完成。' if state == 'signing-in' else
                              'QQ 界面或搜索控件尚未就绪，这不表示账号未登录；请运行“检查 QQ 适配”。')
                    raise QQClientNotReady('[Mac QQ / 就绪] ' + detail + '未输入搜索或请求播放，也未操作登录窗口。')
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
        deadline = self.clock() + 8
        while not self.cancelled() and self.clock() < deadline:
            _app, tree = self._read()
            buttons = matching_song_buttons(tree, selected)
            if len(buttons) == 1 and not self.cancelled() and self.backend.action(buttons[0]):
                return
            self.sleep(0.25)
        raise OSError('[Mac QQ / 搜索结果] 未找到唯一匹配完整歌名、歌手及已知专辑的歌曲行；没有改播同名版本。')

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
            if not fields and supports_song_id(info):
                lines.append('本版 QQ 支持按歌曲 ID 点播，无需搜索框；简化播放栏不支持六毛自动续播。')
            lines.append('就绪观察：' + {'ready': '可继续识别', 'loading': '窗口／搜索控件未就绪',
                'signing-in': '界面显示登录中', 'login-required': '界面显示登录／验证提示'}[startup_state(tree)])
            lines.append(f'搜索框：{len(fields)} 个；播放栏：{"可识别" if region else "未识别"}；时间轴与歌曲：{"可读取" if track else "未识别"}')
            if track is not None:
                lines.append('播放栏专辑：' + ('可读取并核对' if track.album else '未公开，无法仅凭播放栏确认同名专辑版本'))
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
