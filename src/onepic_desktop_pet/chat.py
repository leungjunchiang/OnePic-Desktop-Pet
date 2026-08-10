"""
本模块实现六毛的半透明聊天面板、AI 设置与生活提醒面板和后台请求线程。

职责范围：
- 提供不遮挡桌宠的圆角聊天窗口与清晰的本地/在线状态提示；
- 收集单条用户消息并发出信号，不在界面类中直接访问网络；
- 允许选择纯离线、Codex、Claude Code、DeepSeek、Kimi 或兼容接口并主动检测连接；
- 只把 API 令牌交给系统安全凭据库，不显示或持久化令牌明文；
- 在线请求放入 QThread，避免冻结桌面动画。

聊天文本仅在窗口当前进程的内存中保留，关闭应用后不会写入磁盘。
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .ai import (
    AIChatService,
    AIConnectionError,
    CredentialStore,
    PROVIDER_PRESETS,
    check_provider_connection,
    codex_available,
    claude_available,
    provider_defaults,
)
from .config import PetSettings


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


class AIReplyThread(QThread):
    """在后台执行一次可能较慢的 AI 请求。"""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        service: AIChatService,
        provider: str,
        message: str,
        history: list[tuple[str, str]],
        base_url: str,
        model: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.provider = provider
        self.message = message
        self.history = history
        self.base_url = base_url
        self.model = model

    def run(self) -> None:
        try:
            answer = self.service.reply(
                self.provider,
                self.message,
                self.history,
                self.base_url,
                self.model,
            )
        except AIConnectionError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("AI 连接遇到意外问题，已切回离线回答。")
        else:
            self.succeeded.emit(answer)


class ChatDialog(QDialog):
    """QQ 宠物式的轻量聊天窗口，但不复制其素材或商标。"""

    message_submitted = Signal(str)
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("和六毛聊聊")
        self.setObjectName("liliPanel")
        self.setMinimumSize(430, 520)
        self.resize(470, 580)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("六毛的小纸条")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        self.settings_button = QPushButton("AI 设置")
        self.settings_button.setObjectName("softButton")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        layout.addWidget(self.transcript, 1)

        entry = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("跟六毛说点什么……")
        self.input.setMaxLength(1200)
        self.input.returnPressed.connect(self._submit)
        entry.addWidget(self.input, 1)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._submit)
        entry.addWidget(self.send_button)
        layout.addLayout(entry)

        privacy = QLabel("🔒 对话不落盘。在线模式只把当前消息和最近少量上下文发给所选 AI。")
        privacy.setObjectName("status")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

    def _submit(self) -> None:
        message = " ".join(self.input.text().split())
        if not message:
            return
        self.input.clear()
        self.message_submitted.emit(message)

    def set_provider(self, provider: str) -> None:
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["offline"])
        if provider == "offline":
            detail = "纯离线 · 不联网"
        elif provider == "codex":
            detail = "Codex 已找到 · 临时只读会话" if codex_available() else "未找到 Codex · 会自动离线回答"
        elif provider == "claude":
            detail = "Claude Code 已找到 · 一次性无工具会话" if claude_available() else "未找到 Claude Code · 会自动离线回答"
        else:
            detail = f"{preset.label} · 在线模式"
        self.status_label.setText(detail)

    def append_message(self, role: str, text: str) -> None:
        color = "#426b7c" if role == "六毛" else "#496f9b"
        background = "#edf5f7" if role == "六毛" else "#eaf1fa"
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
        self.send_button.setText("六毛在想…" if busy else "发送")
        if not busy:
            self.input.setFocus()


class AISettingsDialog(QDialog):
    """编辑非敏感连接设置并把新令牌交给凭据库。"""

    def __init__(
        self,
        settings: PetSettings,
        credentials: CredentialStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.credentials = credentials
        self.setWindowTitle("Lili · 六毛设置")
        self.setObjectName("liliPanel")
        self.setMinimumWidth(500)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("连接与陪伴")
        title.setObjectName("title")
        layout.addWidget(title)

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
        layout.addLayout(form)

        self.token_status = QLabel()
        self.token_status.setObjectName("status")
        self.token_status.setWordWrap(True)
        layout.addWidget(self.token_status)
        self.connection_button = QPushButton("检测是否连接")
        self.connection_button.setObjectName("softButton")
        self.connection_button.clicked.connect(self._test_connection)
        layout.addWidget(self.connection_button)

        self.grumbling = QCheckBox("允许六毛偶尔发一句轻松的牢骚")
        self.grumbling.setChecked(settings.automatic_grumbling)
        layout.addWidget(self.grumbling)
        self.hourly = QCheckBox("整点报时（默认关闭，可随时取消）")
        self.hourly.setChecked(settings.hourly_announcement)
        layout.addWidget(self.hourly)
        self.app_awareness = QCheckBox("根据当前应用切换陪伴动作（只识别应用类别）")
        self.app_awareness.setChecked(settings.app_awareness)
        layout.addWidget(self.app_awareness)
        self.voice = QCheckBox("点击六毛时让他说“巴布达”")
        self.voice.setChecked(settings.voice_enabled)
        layout.addWidget(self.voice)
        self.lyric_inspiration = QCheckBox("偶尔显示陈楚生歌名意象的原创短句")
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
        self.music_service = QComboBox(); self.music_service.addItem("网易云音乐", "netease"); self.music_service.addItem("QQ 音乐", "qq")
        self.music_service.setCurrentIndex(max(0, self.music_service.findData(settings.music_service)))
        form.addRow("正版音乐入口", self.music_service)

        note = QLabel(
            "Codex/Claude Code 模式复用本机登录，不需要 API Key；DeepSeek/Kimi 令牌保存在系统安全凭据库。"
            "官方尚未提供让外部程序接管 Codex 内置宠物的接口，但 Lili 可作为独立宠物使用 Codex 对话。"
        )
        note.setObjectName("status")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("softButton")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("保存")
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._provider_changed()

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
        if provider == "codex":
            status = "已检测到本机 Codex。" if codex_available() else "暂未检测到 Codex，聊天时会使用离线回答。"
        elif provider == "claude":
            status = "已检测到本机 Claude Code。" if claude_available() else "暂未检测到 Claude Code，聊天时会使用离线回答。"
        elif enabled:
            status = "系统凭据库中已有令牌。" if self.credentials.has(provider) else "尚未保存令牌。"
        else:
            status = "所有回答都在本机生成。"
        self.token_status.setText(status)

    def _test_connection(self) -> None:
        """检测本机 Agent 登录或 API 地址与令牌，不发送聊天内容。"""

        provider = str(self.provider.currentData())
        self.connection_button.setEnabled(False)
        self.connection_button.setText("正在检测…")
        try:
            result = check_provider_connection(
                provider,
                self.credentials,
                self.base_url.text().strip(),
                self.token.text().strip(),
            )
        except AIConnectionError as exc:
            self.token_status.setText(f"❌ {exc}")
        except Exception:
            self.token_status.setText("❌ 检测遇到意外问题，请稍后重试。")
        else:
            self.token_status.setText(f"✅ {result}")
        finally:
            self.connection_button.setEnabled(True)
            self.connection_button.setText("检测是否连接")

    def apply(self) -> None:
        provider = str(self.provider.currentData())
        self.settings.ai_provider = provider
        self.settings.ai_base_url = self.base_url.text().strip()
        self.settings.ai_model = self.model.text().strip()
        self.settings.automatic_grumbling = self.grumbling.isChecked()
        self.settings.hourly_announcement = self.hourly.isChecked()
        self.settings.app_awareness = self.app_awareness.isChecked()
        self.settings.voice_enabled = self.voice.isChecked()
        self.settings.lyric_inspiration_enabled = self.lyric_inspiration.isChecked()
        self.settings.water_reminder_enabled = self.water.isChecked()
        self.settings.stand_reminder_enabled = self.stand.isChecked()
        self.settings.water_interval_minutes = self.water_minutes.value()
        self.settings.stand_interval_minutes = self.stand_minutes.value()
        self.settings.music_service = str(self.music_service.currentData())
        if provider not in {"offline", "codex", "claude"} and self.token.text().strip():
            self.credentials.set(provider, self.token.text())
