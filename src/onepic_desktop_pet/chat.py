"""
本模块实现 Lili 的聊天面板、AI 设置面板和后台请求线程。

职责范围：
- 提供不遮挡桌宠的圆角聊天窗口与清晰的本地/在线状态提示；
- 收集单条用户消息并发出信号，不在界面类中直接访问网络；
- 允许选择纯离线、Codex、DeepSeek、Kimi 或兼容接口；
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
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .ai import (
    AIChatService,
    AIConnectionError,
    CredentialStore,
    PROVIDER_PRESETS,
    codex_available,
    provider_defaults,
)
from .config import PetSettings


PANEL_STYLE = """
QDialog, QWidget#liliPanel {
    background: #fff8e8;
    color: #3d2a24;
    font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;
}
QTextBrowser {
    background: #fffdf7;
    border: 2px solid #f1b5a9;
    border-radius: 16px;
    padding: 10px;
    font-size: 14px;
}
QLineEdit, QComboBox {
    background: white;
    border: 2px solid #eeb0a4;
    border-radius: 11px;
    padding: 8px 10px;
    min-height: 20px;
}
QLineEdit:focus, QComboBox:focus { border-color: #e6312a; }
QPushButton {
    color: white;
    background: #e6312a;
    border: none;
    border-radius: 11px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton:hover { background: #c92520; }
QPushButton:disabled { background: #c8aaa5; }
QPushButton#softButton { color: #8b302a; background: #f9d8cf; }
QLabel#title { color: #c82420; font-size: 20px; font-weight: 700; }
QLabel#status { color: #805e55; font-size: 12px; }
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
        self.setWindowTitle("和 Lili 聊聊")
        self.setObjectName("liliPanel")
        self.setMinimumSize(430, 520)
        self.resize(470, 580)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Lili 的小纸条")
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
        self.input.setPlaceholderText("跟 Lili 说点什么……")
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
        else:
            detail = f"{preset.label} · 在线模式"
        self.status_label.setText(detail)

    def append_message(self, role: str, text: str) -> None:
        color = "#d92d27" if role == "Lili" else "#3177a8"
        background = "#fff0df" if role == "Lili" else "#eaf6ff"
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
        self.send_button.setText("Lili 在想…" if busy else "发送")
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
        self.setWindowTitle("Lili AI 与陪伴设置")
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

        self.grumbling = QCheckBox("允许 Lili 偶尔发一句轻松的牢骚")
        self.grumbling.setChecked(settings.automatic_grumbling)
        layout.addWidget(self.grumbling)
        self.hourly = QCheckBox("整点报时（默认关闭，可随时取消）")
        self.hourly.setChecked(settings.hourly_announcement)
        layout.addWidget(self.hourly)

        note = QLabel(
            "Codex 模式复用本机登录，不需要 API Key；DeepSeek/Kimi 令牌保存在系统安全凭据库。"
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
        if provider not in {"offline", "codex"}:
            if not self.base_url.text().strip() or self.settings.ai_provider != provider:
                self.base_url.setText(base_url)
            if not self.model.text().strip() or self.settings.ai_provider != provider:
                self.model.setText(model)
        enabled = provider not in {"offline", "codex"}
        self.base_url.setEnabled(enabled)
        self.model.setEnabled(enabled)
        self.token.setEnabled(enabled)
        if provider == "codex":
            status = "已检测到本机 Codex。" if codex_available() else "暂未检测到 Codex，聊天时会使用离线回答。"
        elif enabled:
            status = "系统凭据库中已有令牌。" if self.credentials.has(provider) else "尚未保存令牌。"
        else:
            status = "所有回答都在本机生成。"
        self.token_status.setText(status)

    def apply(self) -> None:
        provider = str(self.provider.currentData())
        self.settings.ai_provider = provider
        self.settings.ai_base_url = self.base_url.text().strip()
        self.settings.ai_model = self.model.text().strip()
        self.settings.automatic_grumbling = self.grumbling.isChecked()
        self.settings.hourly_announcement = self.hourly.isChecked()
        if provider not in {"offline", "codex"} and self.token.text().strip():
            self.credentials.set(provider, self.token.text())
