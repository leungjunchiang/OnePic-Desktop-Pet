"""
本模块实现六毛的半透明聊天面板、AI 设置与生活提醒面板。

职责范围：
- 提供不遮挡桌宠的圆角聊天窗口与 checking/connected/disconnected/error 状态提示；
- 禁止设置类按钮成为 QDialog 默认按钮，确保回车只发送消息，不会误触设置入口；
- 收集单条用户消息并发出信号，不在界面类中直接访问网络；
- 允许选择纯离线、Codex、Claude Code、DeepSeek、Kimi 或兼容接口并主动检测连接；
- 对在线回复采用小片段、定时批量渲染，避免等待整段文字或每个字符都重排全文；
- 分开显示 ChatGPT/Codex 图形应用与 Codex CLI 状态，并只在用户点击时打开 GUI；
- 音乐默认自动选择本机最可用 Provider，只把手动路径和优先项保留为高级选项；
- 分开显示“已检测应用”“已建立播放控制”“仅支持基础控制”，不把安装发现称为已连接；
- 只把 API 令牌交给系统安全凭据库，不显示或持久化令牌明文；
- 为复杂离线请求提供“重新连接 AI”和“去设置”按钮，但绝不自动打开设置窗口；
- 手动连接检测放入 QThread；聊天请求和自动重连由 chat_manager.py 管理。

聊天窗口只保留当前显示的渲染内容；有限的聊天会话由窗口层分开保存在本机，
用户可以清空显示、开始新对话或删除全部聊天记录。待办和提醒不属于聊天记录。
连接检测只向普通界面传递用户友好错误，不渲染 subprocess 命令或系统提示词。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from html import escape

if TYPE_CHECKING:
    from .music_control import MusicProviderManager

from PySide6.QtCore import QRect, QSize, QTimer, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
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
    user_message_for_ai_error,
)
from . import __version__
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
        codex_path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.credentials = credentials
        self.base_url = base_url
        self.token = token
        self.codex_path = codex_path

    def run(self) -> None:
        try:
            result = check_provider_connection(
                self.provider,
                self.credentials,
                self.base_url,
                self.token,
                self.codex_path,
            )
        except AIConnectionError as exc:
            self.failed.emit(user_message_for_ai_error(exc))
        except Exception:
            self.failed.emit("检测遇到意外问题，请稍后重试。")
        else:
            self.succeeded.emit(result)


class ChatDialog(QDialog):
    """QQ 宠物式的轻量聊天窗口，但不复制其素材或商标。"""

    message_submitted = Signal(str)
    stop_requested = Signal()
    settings_requested = Signal(str)
    rename_requested = Signal()
    reconnect_requested = Signal()
    clear_display_requested = Signal()
    new_conversation_requested = Signal()
    history_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        pet_name: str = "六毛",
    ) -> None:
        super().__init__(parent)
        self.pet_name = PET_NAME
        self._transcript_entries: list[tuple[str, str]] = []
        self._streaming_message_index: int | None = None
        self._streaming_pending_text = ""
        self._streaming_final_text: str | None = None
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_streaming_text)
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
        self.setMinimumSize(560, 520)
        self.resize(600, 580)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(f"和{self.pet_name}聊聊")
        self.pet_title = title
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        # Keep the common chat actions visible.  A tiny ellipsis tool button
        # with an implicit drop-down was difficult to discover and looked
        # different across Windows and macOS, so these are ordinary buttons.
        self.history_button = QPushButton("聊天记录")
        self.history_button.setObjectName("softButton")
        self.history_button.setAutoDefault(False)
        self.history_button.setDefault(False)
        self.history_button.clicked.connect(self.history_requested.emit)
        header.addWidget(self.history_button)
        self.new_conversation_button = QPushButton("新对话")
        self.new_conversation_button.setObjectName("softButton")
        self.new_conversation_button.setAutoDefault(False)
        self.new_conversation_button.setDefault(False)
        self.new_conversation_button.clicked.connect(
            self.new_conversation_requested.emit
        )
        header.addWidget(self.new_conversation_button)
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
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("softButton")
        self.stop_button.setAutoDefault(False)
        self.stop_button.setDefault(False)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.hide()
        entry.addWidget(self.stop_button)
        layout.addLayout(entry)

        privacy = QLabel("🔒 对话摘要和最近消息只保存在本机；在线模式只把角色设定、相关知识和有限上下文发给所选 AI。")
        privacy.setObjectName("status")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

    def set_pet_name(self, pet_name: str) -> None:
        """更新聊天窗口中显示的昵称，不清空已有对话。"""

        self.pet_name = PET_NAME
        self.setWindowTitle(f"和{self.pet_name}聊聊")
        self.pet_title.setText(f"和{self.pet_name}聊聊")
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
            degraded_connected = state == AgentConnectionState.CONNECTED.value and any(
                marker in detail for marker in ("不可用", "失败", "不兼容", "兼容连接")
            )
            if (state != AgentConnectionState.CONNECTED.value or degraded_connected) and detail:
                detail = user_message_for_ai_error(detail)
            suffix = "" if not detail or (state == AgentConnectionState.CONNECTED.value and not degraded_connected) else f"\n{detail}"
            detail = f"{preset.label} · {label}{suffix}"
        self.status_label.setText(detail)

    def show_recovery_actions(self, visible: bool) -> None:
        """复杂问题离线时才显示手动操作，不自动触发其中任何按钮。"""

        self.recovery_actions.setVisible(bool(visible))

    def append_message(self, role: str, text: str) -> None:
        self._cancel_streaming_flush()
        self._streaming_message_index = None
        self._transcript_entries.append((str(role), str(text)))
        self._render_transcript()

    def load_transcript(self, messages: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> None:
        """加载本机会话记录到当前显示，不改变 AI 连接或待办数据。"""

        self._cancel_streaming_flush()
        self._streaming_message_index = None
        self._transcript_entries = [
            (str(role), str(text)) for role, text in messages if str(text).strip()
        ]
        self._render_transcript()

    def clear_transcript(self) -> None:
        """只清空窗口显示，不删除本地聊天记录或 AI 上下文。"""

        self._cancel_streaming_flush()
        self._streaming_message_index = None
        self._transcript_entries.clear()
        self._render_transcript()

    def begin_streaming_message(self, role: str) -> None:
        """Create a temporary assistant bubble that can be updated per delta."""

        self._cancel_streaming_flush()
        self._streaming_message_index = len(self._transcript_entries)
        self._streaming_pending_text = ""
        self._streaming_final_text = None
        self._transcript_entries.append((str(role), ""))
        self._render_transcript()

    def append_streaming_delta(self, delta: str) -> None:
        """Queue one delta and render it in a small typewriter-like batch.

        A delta may contain several tokens or characters.  Rendering every
        signal immediately rebuilds the whole QTextBrowser HTML and makes a
        fast stream look slower than it is, so the pending text is flushed at
        most once per short interval.
        """

        if self._streaming_message_index is None:
            self.begin_streaming_message(self.pet_name)
        index = self._streaming_message_index
        if index is None or index >= len(self._transcript_entries):
            return
        self._streaming_pending_text += str(delta)
        self._schedule_streaming_flush()

    def finish_streaming_message(self, text: str) -> None:
        """Finish with the authoritative answer without duplicating a stream."""

        if self._streaming_message_index is None:
            self.append_message(self.pet_name, text)
            return
        index = self._streaming_message_index
        if index >= len(self._transcript_entries):
            self._cancel_streaming_flush()
            self._streaming_message_index = None
            return
        self._streaming_pending_text = ""
        self._streaming_final_text = str(text)
        self._flush_streaming_text()

    def _schedule_streaming_flush(self) -> None:
        if not self._stream_flush_timer.isActive():
            # 25ms is fast enough to feel immediate while avoiding a full HTML
            # rebuild for every token/character emitted by the transport.
            self._stream_flush_timer.start(25)

    def _cancel_streaming_flush(self) -> None:
        if self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()
        self._streaming_pending_text = ""
        self._streaming_final_text = None

    def _flush_streaming_text(self) -> None:
        index = self._streaming_message_index
        if index is None or index >= len(self._transcript_entries):
            self._cancel_streaming_flush()
            return

        role, current = self._transcript_entries[index]
        target = self._streaming_final_text
        if target is not None:
            # Once the authoritative answer is known, continue the same small
            # batches when it extends the visible prefix.  If post-processing
            # changed the prefix (for example a fallback error), replace it
            # immediately rather than showing a misleading mixed response.
            if not target.startswith(current):
                self._transcript_entries[index] = (role, target)
                self._streaming_message_index = None
                self._streaming_final_text = None
                self._render_transcript()
                return
            remaining = target[len(current) :]
            if not remaining:
                self._streaming_message_index = None
                self._streaming_final_text = None
                self._render_transcript()
                return
            piece = remaining[:4]
        else:
            if not self._streaming_pending_text:
                return
            piece = self._streaming_pending_text[:4]
            self._streaming_pending_text = self._streaming_pending_text[4:]

        updated = current + piece
        self._transcript_entries[index] = (role, updated)
        self._render_transcript()
        if target is not None and updated == target:
            self._streaming_message_index = None
            self._streaming_final_text = None
        elif target is not None or self._streaming_pending_text:
            self._stream_flush_timer.start(25)

    def _render_transcript(self) -> None:
        """Render the bounded in-memory transcript, keeping streaming simple."""

        blocks: list[str] = []
        for role, text in self._transcript_entries:
            is_pet = role != "你"
            color = "#426b7c" if is_pet else "#496f9b"
            background = "#edf5f7" if is_pet else "#eaf1fa"
            safe = escape(text).replace("\n", "<br>")
            blocks.append(
                f'<div style="margin:7px 2px;padding:9px 11px;border-radius:12px;'
                f'background:{background};"><b style="color:{color};">{escape(role)}</b><br>{safe}</div>'
            )
        self.transcript.setHtml("".join(blocks))
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.stop_button.setVisible(bool(busy) and self.stop_button.isEnabled())
        self.send_button.setText(f"{self.pet_name}在想…" if busy else "发送")
        if not busy:
            self.input.setFocus()

    def set_interrupt_available(self, available: bool) -> None:
        """Only Codex App Server exposes a stop button in this chat panel."""

        self.stop_button.setEnabled(bool(available))
        if not available:
            self.stop_button.hide()


class ChatHistoryDialog(QDialog):
    """查看、整理和删除本机保存的有限聊天记录。"""

    clear_all_requested = Signal()
    clear_display_requested = Signal()
    new_conversation_requested = Signal()
    session_deleted_requested = Signal(str, bool)

    def __init__(self, history_store, pet_name: str = "六毛", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history_store = history_store
        self.pet_name = pet_name
        self._sessions = []
        self._selected_session_id = ""
        self._mutation_enabled = True
        # This is a utility window in its own right.  In particular, it must
        # have its own taskbar/Dock entry and a real minimize button instead
        # of being owned by the chat dialog as a child sheet.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("聊天记录")
        self.setObjectName("liliPanel")
        self.setMinimumSize(760, 520)
        self.resize(920, 620)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        title = QLabel("聊天记录")
        title.setObjectName("title")
        layout.addWidget(title)
        hint = QLabel("记录只保存在本机；删除聊天记录不会删除待办和提醒。")
        hint.setObjectName("status")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        content = QHBoxLayout()
        sessions_panel = QVBoxLayout()
        sessions_label = QLabel("会话")
        sessions_label.setObjectName("status")
        sessions_panel.addWidget(sessions_label)
        self.session_list = QListWidget()
        self.session_list.setMinimumWidth(260)
        self.session_list.setWordWrap(True)
        self.session_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.session_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.session_list.currentRowChanged.connect(self._show_selected)
        sessions_panel.addWidget(self.session_list, 1)
        session_actions = QHBoxLayout()
        self.rename_session_button = QPushButton("编辑名称")
        self.rename_session_button.setObjectName("softButton")
        self.rename_session_button.setAutoDefault(False)
        self.rename_session_button.setDefault(False)
        self.rename_session_button.clicked.connect(self._rename_selected_session)
        session_actions.addWidget(self.rename_session_button)
        self.delete_session_button = QPushButton("删除这段")
        self.delete_session_button.setObjectName("softButton")
        self.delete_session_button.setAutoDefault(False)
        self.delete_session_button.setDefault(False)
        self.delete_session_button.clicked.connect(self._delete_selected_session)
        session_actions.addWidget(self.delete_session_button)
        sessions_panel.addLayout(session_actions)
        content.addLayout(sessions_panel)

        messages_panel = QVBoxLayout()
        messages_label = QLabel("消息（点击一条后可编辑或删除）")
        messages_label.setObjectName("status")
        messages_panel.addWidget(messages_label)
        self.message_list = QListWidget()
        self.message_list.setWordWrap(True)
        self.message_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.message_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.message_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.message_list.currentRowChanged.connect(
            lambda _row: self._update_action_state()
        )
        messages_panel.addWidget(self.message_list, 1)
        message_actions = QHBoxLayout()
        self.edit_message_button = QPushButton("编辑消息")
        self.edit_message_button.setObjectName("softButton")
        self.edit_message_button.setAutoDefault(False)
        self.edit_message_button.setDefault(False)
        self.edit_message_button.clicked.connect(self._edit_selected_message)
        message_actions.addWidget(self.edit_message_button)
        self.delete_message_button = QPushButton("删除消息")
        self.delete_message_button.setObjectName("softButton")
        self.delete_message_button.setAutoDefault(False)
        self.delete_message_button.setDefault(False)
        self.delete_message_button.clicked.connect(self._delete_selected_message)
        message_actions.addWidget(self.delete_message_button)
        messages_panel.addLayout(message_actions)
        content.addLayout(messages_panel, 1)
        layout.addLayout(content, 1)

        actions = QHBoxLayout()
        self.clear_display_button = QPushButton("清空当前显示")
        self.clear_display_button.setObjectName("softButton")
        self.clear_display_button.setAutoDefault(False)
        self.clear_display_button.setDefault(False)
        self.clear_display_button.clicked.connect(self.clear_display_requested.emit)
        actions.addWidget(self.clear_display_button)
        self.new_conversation_button = QPushButton("新对话")
        self.new_conversation_button.setObjectName("softButton")
        self.new_conversation_button.setAutoDefault(False)
        self.new_conversation_button.setDefault(False)
        self.new_conversation_button.clicked.connect(
            self.new_conversation_requested.emit
        )
        actions.addWidget(self.new_conversation_button)
        actions.addStretch(1)
        self.clear_all_button = QPushButton("删除全部聊天记录")
        self.clear_all_button.setObjectName("softButton")
        self.clear_all_button.setAutoDefault(False)
        self.clear_all_button.setDefault(False)
        self.clear_all_button.clicked.connect(self.clear_all_requested.emit)
        actions.addWidget(self.clear_all_button)
        self.close_button = QPushButton("关闭")
        self.close_button.setAutoDefault(False)
        self.close_button.setDefault(False)
        self.close_button.clicked.connect(self.close)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        """重新读取会话列表并显示最近一段记录。"""

        self._sessions = list(self.history_store.sessions())
        selected_id = self._selected_session_id
        if selected_id not in {session.session_id for session in self._sessions}:
            selected_id = self._sessions[0].session_id if self._sessions else ""
        self._selected_session_id = selected_id
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for session in self._sessions:
            marker = "（当前）" if session.session_id == self.history_store.current_session_id else ""
            updated = session.updated_at.replace("T", " ")
            item = QListWidgetItem(f"{session.title}{marker}\n{updated}")
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            self.session_list.addItem(item)
        if selected_id:
            for row, session in enumerate(self._sessions):
                if session.session_id == selected_id:
                    self.session_list.setCurrentRow(row)
                    break
        self.session_list.blockSignals(False)
        if selected_id:
            self._show_selected(self.session_list.currentRow())
        else:
            self._show_selected(-1)

    def _show_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._sessions):
            self._selected_session_id = ""
            self.message_list.clear()
            item = QListWidgetItem("还没有聊天记录。")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.message_list.addItem(item)
            self._update_action_state()
            return
        session = self._sessions[row]
        self._selected_session_id = session.session_id
        self.message_list.clear()
        for index, (role, text) in enumerate(session.messages):
            label = "你" if role == "user" else self.pet_name
            item = QListWidgetItem(f"{label}\n{text}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.message_list.addItem(item)
        if session.messages:
            self.message_list.setCurrentRow(0)
            self._refresh_message_item_sizes()
        else:
            item = QListWidgetItem("这段会话没有消息。")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.message_list.addItem(item)
        self._update_action_state()

    def resizeEvent(self, event) -> None:
        """让长消息随历史窗口宽度变化完整换行显示。"""

        super().resizeEvent(event)
        self._refresh_message_item_sizes()

    def _refresh_message_item_sizes(self) -> None:
        """按当前可视宽度计算每条消息的完整行高，避免内容被截断。"""

        width = max(220, self.message_list.viewport().width() - 18)
        metrics = QFontMetrics(self.message_list.font())
        for row in range(self.message_list.count()):
            item = self.message_list.item(row)
            if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
                continue
            rect = metrics.boundingRect(
                QRect(0, 0, width, 100000),
                Qt.TextFlag.TextWordWrap,
                item.text(),
            )
            item.setSizeHint(QSize(0, max(52, rect.height() + 20)))

    def set_mutation_enabled(self, enabled: bool) -> None:
        """生成回复时仍允许查看记录，但暂时锁定编辑和删除。"""

        self._mutation_enabled = bool(enabled)
        self._update_action_state()

    def _selected_session(self):
        if not self._selected_session_id:
            return None
        return self.history_store.get(self._selected_session_id)

    def _selected_message_index(self) -> int | None:
        item = self.message_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if isinstance(value, int) else None

    def _update_action_state(self) -> None:
        session = self._selected_session()
        has_message = session is not None and self._selected_message_index() is not None
        can_mutate = self._mutation_enabled and session is not None
        self.rename_session_button.setEnabled(can_mutate)
        self.delete_session_button.setEnabled(can_mutate)
        self.edit_message_button.setEnabled(can_mutate and has_message)
        self.delete_message_button.setEnabled(can_mutate and has_message)

    def _rename_selected_session(self) -> None:
        session = self._selected_session()
        if session is None or not self._mutation_enabled:
            return
        title, accepted = QInputDialog.getText(
            self,
            "编辑会话名称",
            "会话名称：",
            text=session.title,
        )
        title = " ".join(str(title).split())[:80]
        if accepted and title and self.history_store.rename_session(session.session_id, title):
            self.refresh()

    def _edit_selected_message(self) -> None:
        session = self._selected_session()
        index = self._selected_message_index()
        if session is None or index is None or not self._mutation_enabled:
            return
        role, current = session.messages[index]
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "编辑聊天记录",
            f"{('你' if role == 'user' else self.pet_name)}的消息：",
            current,
        )
        if accepted and str(text).strip() and self.history_store.update_message(
            session.session_id, index, str(text)
        ):
            self.refresh()

    def _delete_selected_message(self) -> None:
        session = self._selected_session()
        index = self._selected_message_index()
        if session is None or index is None or not self._mutation_enabled:
            return
        answer = QMessageBox.question(
            self,
            "删除这条消息",
            "确定删除选中的这条本地聊天记录吗？\n这不会删除待办和提醒。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and self.history_store.delete_message(
            session.session_id, index
        ):
            self.refresh()

    def _delete_selected_session(self) -> None:
        session = self._selected_session()
        if session is None or not self._mutation_enabled:
            return
        answer = QMessageBox.question(
            self,
            "删除这段聊天记录",
            f"确定删除“{session.title}”吗？\n待办、提醒和其他会话不会受到影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_current = session.session_id == self.history_store.current_session_id
        if self.history_store.delete_session(session.session_id):
            self.refresh()
            self.session_deleted_requested.emit(session.session_id, was_current)


class AISettingsDialog(QDialog):
    """编辑非敏感连接设置并把新令牌交给凭据库。"""

    program_update_requested = Signal()

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
        self.codex_path = QLineEdit(getattr(settings, "codex_executable_path", ""))
        self.codex_path.setPlaceholderText("留空自动查找；Windows 可填 codex.cmd 的完整路径")
        form.addRow("Codex 路径", self.codex_path)
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

        self.content_updates = QCheckBox(
            "自动检查补充内容更新（知识库、配置和素材，不替换程序）"
        )
        self.content_updates.setChecked(getattr(settings, "content_updates_enabled", True))
        self.content_updates.setToolTip(
            "关闭后不会在启动时自动检查内容补丁；托盘中的“检查补充内容更新”仍可手动执行。"
        )
        layout.addWidget(self.content_updates)

        self.program_updates = QCheckBox(
            "启动时自动检查程序更新（发现新版后可直接更新）"
        )
        self.program_updates.setChecked(
            getattr(settings, "program_updates_enabled", True)
        )
        self.program_updates.setToolTip(
            "开启后启动时自动检查新版程序；不会静默安装，发现新版后仍会先由你确认。Windows 会启动安装器，macOS 会打开 DMG。"
        )
        layout.addWidget(self.program_updates)
        self.program_update_button = QPushButton("立即检查并更新到最新版本…")
        self.program_update_button.setObjectName("softButton")
        self.program_update_button.setToolTip(
            "立即检查 GitHub Releases 的最新版本；发现新版后先确认，再下载、校验并启动安装。"
        )
        self.program_update_button.clicked.connect(self._request_program_update)
        layout.addWidget(self.program_update_button)

        self.version_label = QLabel(
            f"程序版本：{__version__} · 补充内容更新与程序更新分开"
        )
        self.version_label.setObjectName("status")
        self.version_label.setWordWrap(True)
        layout.addWidget(self.version_label)

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
        self.auto_pause_on_idle = QCheckBox("10分钟无键鼠操作时自动暂停")
        self.auto_pause_on_idle.setChecked(getattr(settings, "auto_pause_on_idle", True))
        self.auto_pause_on_idle.setToolTip(
            "只有键盘和鼠标连续10分钟都没有输入才暂停；回来后不会自动继续，必须点击继续工作。"
        )
        form.addRow("工作与计时", self.auto_pause_on_idle)
        self.auto_pause_on_fullscreen_video = QCheckBox("明确的播放器全屏时自动暂停")
        self.auto_pause_on_fullscreen_video.setChecked(
            getattr(settings, "auto_pause_on_fullscreen_video", True)
        )
        self.auto_pause_on_fullscreen_video.setToolTip(
            "只识别 VLC、IINA、mpv 等明确播放器；浏览器、Word、PDF、VS Code 全屏不会误判。"
        )
        form.addRow("", self.auto_pause_on_fullscreen_video)
        idle_hint = QLabel(
            "锁屏和睡眠会立即暂停。所有自动暂停都不会自动恢复；点击继续工作才会重新计时。"
        )
        idle_hint.setWordWrap(True)
        idle_hint.setObjectName("muted")
        form.addRow("规则说明", idle_hint)
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
            codex_path=self.codex_path.text().strip(),
            parent=self,
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

    def _request_program_update(self) -> None:
        """Close settings before the app-level updater shows its own UI."""

        self.program_update_requested.emit()
        self.accept()

    def apply(self) -> None:
        self.settings.owner_nickname = self.owner_nickname.text().strip()[:24]
        self.settings.pet_name = PET_NAME
        provider = str(self.provider.currentData())
        self.settings.ai_provider = provider
        self.settings.ai_base_url = self.base_url.text().strip()
        self.settings.ai_model = self.model.text().strip()
        self.settings.codex_executable_path = self.codex_path.text().strip()[:1200]
        self.settings.always_on_top = self.always_on_top.isChecked()
        self.settings.content_updates_enabled = self.content_updates.isChecked()
        self.settings.program_updates_enabled = self.program_updates.isChecked()
        self.settings.allow_autonomous_walk = self.allow_autonomous_walk.isChecked()
        self.settings.automatic_grumbling = self.grumbling.isChecked()
        self.settings.hourly_announcement = self.hourly.isChecked()
        self.settings.app_awareness = self.app_awareness.isChecked()
        self.settings.voice_enabled = self.voice.isChecked()
        self.settings.lyric_inspiration_enabled = self.lyric_inspiration.isChecked()
        self.settings.water_reminder_enabled = self.water.isChecked()
        self.settings.stand_reminder_enabled = self.stand.isChecked()
        self.settings.auto_pause_on_idle = self.auto_pause_on_idle.isChecked()
        self.settings.auto_pause_on_fullscreen_video = self.auto_pause_on_fullscreen_video.isChecked()
        self.settings.water_interval_minutes = self.water_minutes.value()
        self.settings.stand_interval_minutes = self.stand_minutes.value()
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
