"""
本模块实现六毛的半透明聊天面板、AI 设置与生活提醒面板。

职责范围：
- 提供不遮挡桌宠的圆角聊天窗口与 checking/connected/disconnected/error 状态提示；
- 禁止设置类按钮成为 QDialog 默认按钮，确保回车只发送消息，不会误触设置入口；
- 收集单条用户消息并发出信号，不在界面类中直接访问网络；
- 允许选择纯离线、Codex、Claude Code、DeepSeek、Kimi 或兼容接口并主动检测连接；
- 分开显示 ChatGPT/Codex 图形应用与 Codex CLI 状态，并只在用户点击时打开 GUI；
- 音乐默认自动选择本机最可用 Provider，只把手动路径和优先项保留为高级选项；
- 分开显示“已检测应用”“已建立播放控制”“仅支持基础控制”，不把安装发现称为已连接；
- 只把 API 令牌交给系统安全凭据库，不显示或持久化令牌明文；
- 为复杂离线请求提供“重新连接 AI”和“去设置”按钮，但绝不自动打开设置窗口；
- 手动连接检测放入 QThread；聊天请求和自动重连由 chat_manager.py 管理。

聊天文本仅在窗口当前进程的内存中保留，关闭应用后不会写入磁盘。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from html import escape

if TYPE_CHECKING:
    from .music_control import MusicProviderManager

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .ai import (
    AIConnectionError,
    CredentialStore,
    PROVIDER_PRESETS,
    check_provider_connection,
    codex_detection_message,
    claude_available,
    find_codex_gui_app,
    launch_codex_gui,
    provider_defaults,
)
from .config import PET_NAME
from .config import PetSettings
from .chat_manager import AgentConnectionState, AgentManager


PANEL_STYLE = """
QDialog, QWidget#liliPanel {
    background: rgba(245, 247, 250, 205);
    color: #27313d;
    font-family: "PingFang SC", "Microsoft YaHei UI", sans-serif;
}
QTextBrowser {
    background: rgba(255, 255, 255, 168);
    border: 1px solid rgba(101, 116, 139, 120);
    border-radius: 16px;
    padding: 10px;
    font-size: 14px;
}
QLineEdit, QComboBox {
    background: white;
    border: 1px solid rgba(101, 116, 139, 130);
    border-radius: 11px;
    padding: 8px 10px;
    min-height: 20px;
}
QLineEdit:focus, QComboBox:focus { border-color: #58a6c7; }
QPushButton {
    color: white;
    background: #4f8099;
    border: none;
    border-radius: 11px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton:hover { background: #3d6d86; }
QPushButton:disabled { background: #c8aaa5; }
QPushButton#softButton { color: #405363; background: rgba(213, 229, 238, 180); }
QLabel#title { color: #334e61; font-size: 20px; font-weight: 700; }
QLabel#status { color: #667784; font-size: 12px; }
"""


class ConnectionCheckThread(QThread):
    """后台检测本机 Agent 或 API，避免设置窗口在检测时假死。"""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        provider: str,
        credentials: CredentialStore,
        base_url: str,
        token: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.credentials = credentials
        self.base_url = base_url
        self.token = token

    def run(self) -> None:
        try:
            result = check_provider_connection(
                self.provider,
                self.credentials,
                self.base_url,
                self.token,
            )
        except AIConnectionError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("检测遇到意外问题，请稍后重试。")
        else:
            self.succeeded.emit(result)


class ChatDialog(QDialog):
    """QQ 宠物式的轻量聊天窗口，但不复制其素材或商标。"""

    message_submitted = Signal(str)
    settings_requested = Signal(str)
    rename_requested = Signal()
    reconnect_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        pet_name: str = "六毛",
    ) -> None:
        super().__init__(parent)
        self.pet_name = PET_NAME
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle(f"和{self.pet_name}聊聊")
        self.setObjectName("liliPanel")
        self.setMinimumSize(430, 520)
        self.resize(470, 580)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(f"{self.pet_name}的小纸条")
        self.pet_title = title
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        self.rename_button = QPushButton("修改主人称呼")
        self.rename_button.setObjectName("softButton")
        self.rename_button.setToolTip("用于自习室、串门和搭子互动时区分不同六毛")
        self.rename_button.setAutoDefault(False)
        self.rename_button.setDefault(False)
        self.rename_button.clicked.connect(self.rename_requested.emit)
        header.addWidget(self.rename_button)
        self.settings_button = QPushButton("AI 设置")
        self.settings_button.setObjectName("softButton")
        self.settings_button.setAutoDefault(False)
        self.settings_button.setDefault(False)
        self.settings_button.clicked.connect(
            lambda _checked=False: self.settings_requested.emit("user_action")
        )
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.recovery_actions = QWidget()
        recovery_layout = QHBoxLayout(self.recovery_actions)
        recovery_layout.setContentsMargins(0, 0, 0, 0)
        recovery_layout.addStretch(1)
        self.reconnect_button = QPushButton("重新连接 AI")
        self.reconnect_button.setObjectName("softButton")
        self.reconnect_button.setAutoDefault(False)
        self.reconnect_button.setDefault(False)
        self.reconnect_button.clicked.connect(self.reconnect_requested.emit)
        recovery_layout.addWidget(self.reconnect_button)
        self.go_to_settings_button = QPushButton("去设置")
        self.go_to_settings_button.setObjectName("softButton")
        self.go_to_settings_button.setAutoDefault(False)
        self.go_to_settings_button.setDefault(False)
        self.go_to_settings_button.clicked.connect(
            lambda _checked=False: self.settings_requested.emit("user_action")
        )
        recovery_layout.addWidget(self.go_to_settings_button)
        self.recovery_actions.hide()
        layout.addWidget(self.recovery_actions)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        layout.addWidget(self.transcript, 1)

        entry = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(f"跟{self.pet_name}说点什么……")
        self.input.setMaxLength(1200)
        self.input.returnPressed.connect(self._submit)
        entry.addWidget(self.input, 1)
        self.send_button = QPushButton("发送")
        self.send_button.setAutoDefault(False)
        self.send_button.setDefault(False)
        self.send_button.clicked.connect(self._submit)
        entry.addWidget(self.send_button)
        layout.addLayout(entry)

        privacy = QLabel("🔒 对话摘要和最近消息只保存在本机；在线模式只把角色设定、相关知识和有限上下文发给所选 AI。")
        privacy.setObjectName("status")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

    def set_pet_name(self, pet_name: str) -> None:
        """更新聊天窗口中显示的昵称，不清空已有对话。"""

        self.pet_name = PET_NAME
        self.setWindowTitle(f"和{self.pet_name}聊聊")
        self.pet_title.setText(f"{self.pet_name}的小纸条")
        self.input.setPlaceholderText(f"跟{self.pet_name}说点什么……")

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭按钮只隐藏聊天窗，不关闭桌宠或丢失会话。"""

        event.ignore()
        self.hide()

    def _submit(self) -> None:
        message = " ".join(self.input.text().split())
        if not message:
            return
        self.input.clear()
        self.message_submitted.emit(message)

    def set_provider(
        self,
        provider: str,
        state: str = AgentConnectionState.CHECKING.value,
        detail: str = "",
    ) -> None:
        """只展示 AgentManager 缓存状态，不在 UI 线程执行检测。"""

        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["offline"])
        if provider == "offline":
            detail = "纯离线 · 不联网"
        else:
            state_labels = {
                AgentConnectionState.CHECKING.value: "正在后台检测",
                AgentConnectionState.CONNECTED.value: "已连接，优先使用 AI",
                AgentConnectionState.DISCONNECTED.value: "未连接，已自动使用离线陪伴",
                AgentConnectionState.ERROR.value: "暂时出错，已自动使用离线陪伴",
            }
            label = state_labels.get(state, "已自动使用离线陪伴")
            # AgentManager 的 detail 可能是“Codex 已连接。”，再拼在
            # “Codex（使用本机登录）· 已连接”下面会造成截图中的重复状态。
            # 成功状态只保留一个稳定标签；失败状态才显示诊断原因。
            suffix = "" if state == AgentConnectionState.CONNECTED.value else (f"\n{detail}" if detail else "")
            detail = f"{preset.label} · {label}{suffix}"
        self.status_label.setText(detail)

    def show_recovery_actions(self, visible: bool) -> None:
        """复杂问题离线时才显示手动操作，不自动触发其中任何按钮。"""

        self.recovery_actions.setVisible(bool(visible))

    def append_message(self, role: str, text: str) -> None:
        is_pet = role != "你"
        color = "#426b7c" if is_pet else "#496f9b"
        background = "#edf5f7" if is_pet else "#eaf1fa"
        safe = escape(text).replace("\n", "<br>")
        self.transcript.append(
            f'<div style="margin:7px 2px;padding:9px 11px;border-radius:12px;'
            f'background:{background};"><b style="color:{color};">{escape(role)}</b><br>{safe}</div>'
        )
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.send_button.setText(f"{self.pet_name}在想…" if busy else "发送")
        if not busy:
            self.input.setFocus()


class AISettingsDialog(QDialog):
    """编辑非敏感连接设置并把新令牌交给凭据库。"""

    def __init__(
        self,
        settings: PetSettings,
        credentials: CredentialStore,
        parent: QWidget | None = None,
        agent_manager: AgentManager | None = None,
        music_manager: MusicProviderManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.credentials = credentials
        self.agent_manager = agent_manager
        self.music_manager = music_manager
        self._connection_thread: ConnectionCheckThread | None = None
        self.setWindowTitle(f"Lili · {PET_NAME}设置")
        self.setObjectName("liliPanel")
        self.setMinimumWidth(500)
        self.resize(620, 760)
        self.setStyleSheet(PANEL_STYLE)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("连接与陪伴")
        title.setObjectName("title")
        outer_layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 4, 8, 4)
        layout.setSpacing(9)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)

        form = QFormLayout()
        self.provider = QComboBox()
        for key, preset in PROVIDER_PRESETS.items():
            self.provider.addItem(preset.label, key)
        index = self.provider.findData(settings.ai_provider)
        self.provider.setCurrentIndex(max(0, index))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("对话方式", self.provider)

        self.base_url = QLineEdit(settings.ai_base_url)
        form.addRow("API 地址", self.base_url)
        self.model = QLineEdit(settings.ai_model)
        form.addRow("模型", self.model)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("留空则保留已安全保存的令牌")
        form.addRow("API 令牌", self.token)
        self.owner_nickname = QLineEdit(getattr(settings, "owner_nickname", ""))
        self.owner_nickname.setMaxLength(24)
        self.owner_nickname.setPlaceholderText("例如：小梁、mianmian；留空则显示搭子家的六毛")
        form.addRow("主人称呼", self.owner_nickname)
        layout.addLayout(form)

        self.token_status = QLabel()
        self.token_status.setObjectName("status")
        self.token_status.setWordWrap(True)
        layout.addWidget(self.token_status)
        self.connection_button = QPushButton("检测是否连接")
        self.connection_button.setObjectName("softButton")
        self.connection_button.clicked.connect(self._test_connection)
        layout.addWidget(self.connection_button)
        self.open_chatgpt_button = QPushButton("打开 ChatGPT")
        self.open_chatgpt_button.setObjectName("softButton")
        self.open_chatgpt_button.clicked.connect(self._open_codex_gui)
        layout.addWidget(self.open_chatgpt_button)

        self.always_on_top = QCheckBox("始终置顶（关闭后为桌面模式，不抢输入焦点）")
        self.always_on_top.setChecked(settings.always_on_top)
        layout.addWidget(self.always_on_top)

        self.allow_autonomous_walk = QCheckBox(
            "允许六毛自动跑动（默认关闭；打开后会在桌面上来回移动）"
        )
        self.allow_autonomous_walk.setChecked(settings.allow_autonomous_walk)
        self.allow_autonomous_walk.setToolTip(
            "关闭时仍保留眨眼、坐下、睡觉和互动动画，只是不自动横向跑动。"
        )
        layout.addWidget(self.allow_autonomous_walk)

        self.grumbling = QCheckBox("允许六毛偶尔发一句轻松的牢骚")
        self.grumbling.setChecked(settings.automatic_grumbling)
        layout.addWidget(self.grumbling)
        self.hourly = QCheckBox("整点报时（默认关闭，可随时取消）")
        self.hourly.setChecked(settings.hourly_announcement)
        layout.addWidget(self.hourly)
        self.app_awareness = QCheckBox("根据当前应用切换陪伴动作（只识别应用类别）")
        self.app_awareness.setChecked(settings.app_awareness)
        layout.addWidget(self.app_awareness)
        self.voice = QCheckBox("双击右键时让六毛说“巴布达”")
        self.voice.setChecked(settings.voice_enabled)
        layout.addWidget(self.voice)
        self.lyric_inspiration = QCheckBox("定时显示歌名意象或本地歌词")
        self.lyric_inspiration.setChecked(settings.lyric_inspiration_enabled)
        layout.addWidget(self.lyric_inspiration)
        self.water = QCheckBox("喝水提醒")
        self.water.setChecked(settings.water_reminder_enabled)
        self.water_minutes = QSpinBox(); self.water_minutes.setRange(10, 240); self.water_minutes.setSuffix(" 分钟"); self.water_minutes.setValue(settings.water_interval_minutes)
        form.addRow(self.water, self.water_minutes)
        self.stand = QCheckBox("站立休息提醒")
        self.stand.setChecked(settings.stand_reminder_enabled)
        self.stand_minutes = QSpinBox(); self.stand_minutes.setRange(10, 240); self.stand_minutes.setSuffix(" 分钟"); self.stand_minutes.setValue(settings.stand_interval_minutes)
        form.addRow(self.stand, self.stand_minutes)
        self.auto_pause_idle = QCheckBox("键鼠无操作时自动暂停专注计时")
        self.auto_pause_idle.setChecked(settings.auto_pause_on_idle)
        self.idle_pause_minutes = QSpinBox(); self.idle_pause_minutes.setRange(1, 60); self.idle_pause_minutes.setSuffix(" 分钟"); self.idle_pause_minutes.setValue(max(1, settings.idle_pause_seconds // 60)); self.idle_pause_minutes.setToolTip("这是触发自动暂停的无操作阈值；回来后会显示这次实际离开了多久。")
        form.addRow(self.auto_pause_idle, self.idle_pause_minutes)
        self.music_service = QComboBox()
        for label, key in (
            ("自动选择（推荐）", "auto"),
            ("网易云音乐", "netease"),
            ("QQ 音乐", "qq"),
            ("酷狗音乐", "kugou"),
            ("Apple Music", "apple"),
            ("Spotify", "spotify"),
        ):
            self.music_service.addItem(label, key)
        self.music_service.setCurrentIndex(max(0, self.music_service.findData(settings.music_service)))
        self.music_service.currentIndexChanged.connect(self._music_provider_changed)
        form.addRow("优先播放器（高级）", self.music_service)
        self.music_status = QLabel()
        self.music_status.setObjectName("status")
        self.music_status.setWordWrap(True)
        form.addRow("自动选择状态", self.music_status)

        self.qq_music_path = QLineEdit(settings.qq_music_path)
        self.qq_music_path.setPlaceholderText("自动寻找，或选择 QQMusic.exe / QQMusic.app")
        qq_row = QWidget(); qq_layout = QHBoxLayout(qq_row); qq_layout.setContentsMargins(0, 0, 0, 0)
        qq_pick = QPushButton("选择…"); qq_pick.setObjectName("softButton"); qq_pick.clicked.connect(self._choose_qq_music)
        qq_layout.addWidget(self.qq_music_path, 1); qq_layout.addWidget(qq_pick)
        form.addRow("QQ 音乐程序", qq_row)

        self.netease_music_path = QLineEdit(settings.netease_music_path)
        self.netease_music_path.setPlaceholderText("自动寻找，或选择 cloudmusic.exe / 网易云音乐.app")
        netease_row = QWidget(); netease_layout = QHBoxLayout(netease_row); netease_layout.setContentsMargins(0, 0, 0, 0)
        netease_pick = QPushButton("选择…"); netease_pick.setObjectName("softButton"); netease_pick.clicked.connect(self._choose_netease_music)
        netease_layout.addWidget(self.netease_music_path, 1); netease_layout.addWidget(netease_pick)
        form.addRow("网易云程序", netease_row)

        self.kugou_music_path = QLineEdit(settings.kugou_music_path)
        self.kugou_music_path.setPlaceholderText("自动寻找，或选择 KuGou.exe / 酷狗音乐.app")
        kugou_row = QWidget(); kugou_layout = QHBoxLayout(kugou_row); kugou_layout.setContentsMargins(0, 0, 0, 0)
        kugou_pick = QPushButton("选择…"); kugou_pick.setObjectName("softButton"); kugou_pick.clicked.connect(self._choose_kugou_music)
        kugou_layout.addWidget(self.kugou_music_path, 1); kugou_layout.addWidget(kugou_pick)
        form.addRow("酷狗音乐程序", kugou_row)

        self.apple_music_path = QLineEdit(settings.apple_music_path)
        self.apple_music_path.setPlaceholderText("自动寻找，或选择 AppleMusic.exe / Music.app")
        apple_row = QWidget(); apple_layout = QHBoxLayout(apple_row); apple_layout.setContentsMargins(0, 0, 0, 0)
        apple_pick = QPushButton("选择…"); apple_pick.setObjectName("softButton"); apple_pick.clicked.connect(self._choose_apple_music)
        apple_layout.addWidget(self.apple_music_path, 1); apple_layout.addWidget(apple_pick)
        form.addRow("Apple Music 程序", apple_row)

        self.spotify_music_path = QLineEdit(settings.spotify_music_path)
        self.spotify_music_path.setPlaceholderText("自动寻找，或选择 Spotify.exe / Spotify.app")
        spotify_row = QWidget(); spotify_layout = QHBoxLayout(spotify_row); spotify_layout.setContentsMargins(0, 0, 0, 0)
        spotify_pick = QPushButton("选择…"); spotify_pick.setObjectName("softButton"); spotify_pick.clicked.connect(self._choose_spotify_music)
        spotify_layout.addWidget(self.spotify_music_path, 1); spotify_layout.addWidget(spotify_pick)
        form.addRow("Spotify 程序", spotify_row)

        self.babuda_audio_path = QLineEdit(settings.babuda_audio_path)
        self.babuda_audio_path.setPlaceholderText("选择第一段 babuda 音频；同目录多段会自动轮换")
        audio_row = QWidget(); audio_layout = QHBoxLayout(audio_row); audio_layout.setContentsMargins(0, 0, 0, 0)
        audio_pick = QPushButton("选择…"); audio_pick.setObjectName("softButton"); audio_pick.clicked.connect(self._choose_babuda_audio)
        audio_layout.addWidget(self.babuda_audio_path, 1); audio_layout.addWidget(audio_pick)
        form.addRow("巴布达音频", audio_row)

        self.local_lyrics_path = QLineEdit(settings.local_lyrics_path)
        self.local_lyrics_path.setPlaceholderText("可选：你有权使用的 TXT，每行一句")
        lyrics_row = QWidget(); lyrics_layout = QHBoxLayout(lyrics_row); lyrics_layout.setContentsMargins(0, 0, 0, 0)
        lyrics_pick = QPushButton("选择…"); lyrics_pick.setObjectName("softButton"); lyrics_pick.clicked.connect(self._choose_local_lyrics)
        lyrics_layout.addWidget(self.local_lyrics_path, 1); lyrics_layout.addWidget(lyrics_pick)
        form.addRow("本地歌词文本", lyrics_row)
        self.lyric_minutes = QSpinBox(); self.lyric_minutes.setRange(2, 120); self.lyric_minutes.setSuffix(" 分钟"); self.lyric_minutes.setValue(settings.lyric_interval_minutes)
        form.addRow("歌词气泡间隔", self.lyric_minutes)

        note = QLabel(
            "Codex/Claude Code 模式复用本机登录，不需要 API Key；DeepSeek/Kimi 令牌保存在系统安全凭据库。"
            "官方尚未提供让外部程序接管 Codex 内置宠物的接口，但 Lili 可作为独立宠物使用 Codex 对话。"
        )
        note.setObjectName("status")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("softButton")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(self.save_button)
        outer_layout.addLayout(buttons)
        self._provider_changed()
        self._music_provider_changed()

    def _music_provider_changed(self) -> None:
        """分别显示应用、Transport 与自动选歌能力，不把安装称为已连接。"""

        provider = str(self.music_service.currentData())
        if self.music_manager is None:
            self.music_status.setText("音乐播放器：自动选择\n当前使用：尚未开始播放\n首次播放时将检测本机播放器。")
            return
        if provider == "auto":
            self.music_status.setText(self.music_manager.auto_status_text())
            return
        self.music_status.setText(
            f"自动选择已开启；优先尝试{self.music_service.currentText()}。\n"
            f"{self.music_manager.provider_status_text(provider)}"
        )

    def _provider_changed(self) -> None:
        provider = str(self.provider.currentData())
        base_url, model = provider_defaults(provider)
        if provider not in {"offline", "codex", "claude"}:
            if not self.base_url.text().strip() or self.settings.ai_provider != provider:
                self.base_url.setText(base_url)
            if not self.model.text().strip() or self.settings.ai_provider != provider:
                self.model.setText(model)
        enabled = provider not in {"offline", "codex", "claude"}
        self.base_url.setEnabled(enabled)
        self.model.setEnabled(enabled)
        self.token.setEnabled(enabled)
        if provider != "offline" and self.agent_manager is not None:
            cached = self.agent_manager.status(provider)
            labels = {
                AgentConnectionState.CHECKING: "正在后台检测；当前聊天仍可使用离线陪伴。",
                AgentConnectionState.CONNECTED: cached.detail,
                AgentConnectionState.DISCONNECTED: f"{cached.detail}\n聊天会自动使用离线陪伴。",
                AgentConnectionState.ERROR: f"{cached.detail}\n稍后会低频自动重连。",
            }
            status = labels[cached.state]
        elif provider == "codex":
            status = codex_detection_message()
        elif provider == "claude":
            status = "已检测到本机 Claude Code。" if claude_available() else "暂未检测到 Claude Code，聊天时会使用离线回答。"
        elif enabled:
            status = "系统凭据库中已有令牌。" if self.credentials.has(provider) else "尚未保存令牌。"
        else:
            status = "所有回答都在本机生成。"
        self.token_status.setText(status)
        self.open_chatgpt_button.setVisible(provider == "codex")
        self.open_chatgpt_button.setEnabled(find_codex_gui_app() is not None)

    def _open_codex_gui(self) -> None:
        """由用户主动打开 ChatGPT Desktop App，不把 GUI 当作 Codex CLI。"""

        if launch_codex_gui():
            self.token_status.setText("已打开 ChatGPT；代码任务仍由独立的 Codex CLI 执行。")
        else:
            self.token_status.setText("未检测到可打开的 ChatGPT Desktop App。")

    def _choose_qq_music(self) -> None:
        """选择本机 QQ 音乐程序，不读取程序内容。"""

        path = self._choose_music_program("选择 QQ 音乐程序", self.qq_music_path.text())
        if path:
            self.qq_music_path.setText(path)

    def _choose_netease_music(self) -> None:
        """选择本机网易云音乐程序，不读取程序内容。"""

        path = self._choose_music_program("选择网易云音乐程序", self.netease_music_path.text())
        if path:
            self.netease_music_path.setText(path)

    def _choose_kugou_music(self) -> None:
        """选择本机酷狗音乐程序，不读取程序内容。"""

        path = self._choose_music_program("选择酷狗音乐程序", self.kugou_music_path.text())
        if path:
            self.kugou_music_path.setText(path)

    def _choose_apple_music(self) -> None:
        """选择本机 Apple Music 程序。"""

        path = self._choose_music_program("选择 Apple Music 程序", self.apple_music_path.text())
        if path:
            self.apple_music_path.setText(path)

    def _choose_spotify_music(self) -> None:
        """选择本机 Spotify 程序。"""

        path = self._choose_music_program("选择 Spotify 程序", self.spotify_music_path.text())
        if path:
            self.spotify_music_path.setText(path)

    def _choose_music_program(self, title: str, current: str) -> str:
        """Windows 选择 EXE，macOS 选择应用包目录；输入框仍允许手工粘贴路径。"""

        if sys.platform == "darwin":
            return QFileDialog.getExistingDirectory(self, title, current or "/Applications")
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            current,
            "程序 (*.exe);;所有文件 (*)",
        )
        return path

    def _choose_babuda_audio(self) -> None:
        """选择本地巴布达音频；同目录相同前缀文件会作为语气变体。"""

        path, _ = QFileDialog.getOpenFileName(self, "选择巴布达音频", self.babuda_audio_path.text(), "音频 (*.wav *.mp3 *.m4a *.aac *.ogg);;所有文件 (*)")
        if path:
            self.babuda_audio_path.setText(path)

    def _choose_local_lyrics(self) -> None:
        """选择用户有权在本机使用的逐行文本。"""

        path, _ = QFileDialog.getOpenFileName(self, "选择本地歌词文本", self.local_lyrics_path.text(), "文本 (*.txt);;所有文件 (*)")
        if path:
            self.local_lyrics_path.setText(path)

    def _test_connection(self) -> None:
        """在后台检测本机 Agent 登录或 API，不阻塞其余设置选项。"""

        if self._connection_thread is not None and self._connection_thread.isRunning():
            return
        provider = str(self.provider.currentData())
        self.connection_button.setEnabled(False)
        self.connection_button.setText("正在检测…")
        self.cancel_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self._connection_thread = ConnectionCheckThread(
            provider,
            self.credentials,
            self.base_url.text().strip(),
            self.token.text().strip(),
            self,
        )
        self._connection_thread.succeeded.connect(self._manual_check_succeeded)
        self._connection_thread.failed.connect(self._manual_check_failed)
        self._connection_thread.finished.connect(self._connection_finished)
        self._connection_thread.start()

    def _manual_check_succeeded(self, result: str) -> None:
        """手动检测成功时更新可见文案与共享缓存。"""

        self.token_status.setText(f"✅ {result}")
        if self.agent_manager is not None:
            provider = str(self.provider.currentData())
            if "未检测到 Codex CLI" in result:
                self.agent_manager.mark_disconnected(provider, result)
            else:
                self.agent_manager.mark_runtime_success(provider)

    def _manual_check_failed(self, error: str) -> None:
        """手动检测失败只更新当前设置页，不打开其他窗口。"""

        self.token_status.setText(f"❌ {error}")
        if self.agent_manager is not None:
            self.agent_manager.mark_runtime_error(str(self.provider.currentData()), error)

    def _connection_finished(self) -> None:
        """恢复检测与保存按钮并释放已完成的线程。"""

        thread = self._connection_thread
        self._connection_thread = None
        self.connection_button.setEnabled(True)
        self.connection_button.setText("检测是否连接")
        self.cancel_button.setEnabled(True)
        self.save_button.setEnabled(True)
        if thread is not None:
            thread.deleteLater()

    def apply(self) -> None:
        self.settings.owner_nickname = self.owner_nickname.text().strip()[:24]
        self.settings.pet_name = PET_NAME
        provider = str(self.provider.currentData())
        self.settings.ai_provider = provider
        self.settings.ai_base_url = self.base_url.text().strip()
        self.settings.ai_model = self.model.text().strip()
        self.settings.always_on_top = self.always_on_top.isChecked()
        self.settings.allow_autonomous_walk = self.allow_autonomous_walk.isChecked()
        self.settings.automatic_grumbling = self.grumbling.isChecked()
        self.settings.hourly_announcement = self.hourly.isChecked()
        self.settings.app_awareness = self.app_awareness.isChecked()
        self.settings.voice_enabled = self.voice.isChecked()
        self.settings.lyric_inspiration_enabled = self.lyric_inspiration.isChecked()
        self.settings.water_reminder_enabled = self.water.isChecked()
        self.settings.stand_reminder_enabled = self.stand.isChecked()
        self.settings.water_interval_minutes = self.water_minutes.value()
        self.settings.stand_interval_minutes = self.stand_minutes.value()
        self.settings.auto_pause_on_idle = self.auto_pause_idle.isChecked()
        self.settings.idle_pause_seconds = self.idle_pause_minutes.value() * 60
        self.settings.music_service = str(self.music_service.currentData())
        self.settings.qq_music_path = self.qq_music_path.text().strip()
        self.settings.netease_music_path = self.netease_music_path.text().strip()
        self.settings.kugou_music_path = self.kugou_music_path.text().strip()
        self.settings.apple_music_path = self.apple_music_path.text().strip()
        self.settings.spotify_music_path = self.spotify_music_path.text().strip()
        self.settings.babuda_audio_path = self.babuda_audio_path.text().strip()
        self.settings.local_lyrics_path = self.local_lyrics_path.text().strip()
        self.settings.lyric_interval_minutes = self.lyric_minutes.value()
        if provider not in {"offline", "codex", "claude"} and self.token.text().strip():
            self.credentials.set(provider, self.token.text())
