­r‡^Ñf¥–Ø¦{®ìyÊ'vÃ®¶›­"""
æœ¬æ¨¡å—å®ç°å…­æ¯›çš„åŠé€æ˜èŠå¤©é¢æ¿ã€AI è®¾ç½®ä¸ç”Ÿæ´»æé†’é¢æ¿ã€‚

èŒè´£èŒƒå›´ï¼š
- æä¾›ä¸é®æŒ¡æ¡Œå® çš„åœ†è§’èŠå¤©çª—å£ä¸ checking/connected/disconnected/error çŠ¶æ€æç¤ºï¼›
- ç¦æ­¢è®¾ç½®ç±»æŒ‰é’®æˆä¸º QDialog é»˜è®¤æŒ‰é’®ï¼Œç¡®ä¿å›è½¦åªå‘é€æ¶ˆæ¯ï¼Œä¸ä¼šè¯¯è§¦è®¾ç½®å…¥å£ï¼›
- æ”¶é›†å•æ¡ç”¨æˆ·æ¶ˆæ¯å¹¶å‘å‡ºä¿¡å·ï¼Œä¸åœ¨ç•Œé¢ç±»ä¸­ç›´æ¥è®¿é—®ç½‘ç»œï¼›
- å…è®¸é€‰æ‹©çº¯ç¦»çº¿ã€Codexã€Claude Codeã€DeepSeekã€Kimi æˆ–å…¼å®¹æ¥å£å¹¶ä¸»åŠ¨æ£€æµ‹è¿æ¥ï¼›
- åˆ†å¼€æ˜¾ç¤º ChatGPT/Codex å›¾å½¢åº”ç”¨ä¸ Codex CLI çŠ¶æ€ï¼Œå¹¶åªåœ¨ç”¨æˆ·ç‚¹å‡»æ—¶æ‰“å¼€ GUIï¼›
- éŸ³ä¹é»˜è®¤è‡ªåŠ¨é€‰æ‹©æœ¬æœºæœ€å¯ç”¨ Providerï¼ŒåªæŠŠæ‰‹åŠ¨è·¯å¾„å’Œä¼˜å…ˆé¡¹ä¿ç•™ä¸ºé«˜çº§é€‰é¡¹ï¼›
- åˆ†å¼€æ˜¾ç¤ºâ€œå·²æ£€æµ‹åº”ç”¨â€â€œå·²å»ºç«‹æ’­æ”¾æ§åˆ¶â€â€œä»…æ”¯æŒåŸºç¡€æ§åˆ¶â€ï¼Œä¸æŠŠå®‰è£…å‘ç°ç§°ä¸ºå·²è¿æ¥ï¼›
- åªæŠŠ API ä»¤ç‰Œäº¤ç»™ç³»ç»Ÿå®‰å…¨å‡­æ®åº“ï¼Œä¸æ˜¾ç¤ºæˆ–æŒä¹…åŒ–ä»¤ç‰Œæ˜æ–‡ï¼›
- ä¸ºå¤æ‚ç¦»çº¿è¯·æ±‚æä¾›â€œé‡æ–°è¿æ¥ AIâ€å’Œâ€œå»è®¾ç½®â€æŒ‰é’®ï¼Œä½†ç»ä¸è‡ªåŠ¨æ‰“å¼€è®¾ç½®çª—å£ï¼›
- æ‰‹åŠ¨è¿æ¥æ£€æµ‹æ”¾å…¥ QThreadï¼›èŠå¤©è¯·æ±‚å’Œè‡ªåŠ¨é‡è¿ç”± chat_manager.py ç®¡ç†ã€‚

èŠå¤©æ–‡æœ¬ä»…åœ¨çª—å£å½“å‰è¿›ç¨‹çš„å†…å­˜ä¸­ä¿ç•™ï¼Œå…³é—­åº”ç”¨åä¸ä¼šå†™å…¥ç£ç›˜ã€‚
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
    """åå°æ£€æµ‹æœ¬æœº Agent æˆ– APIï¼Œé¿å…è®¾ç½®çª—å£åœ¨æ£€æµ‹æ—¶å‡æ­»ã€‚"""

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
            self.failed.emit("æ£€æµ‹é‡åˆ°æ„å¤–é—®é¢˜ï¼Œè¯·ç¨åé‡è¯•ã€‚")
        else:
            self.succeeded.emit(result)


class ChatDialog(QDialog):
    """QQ å® ç‰©å¼çš„è½»é‡èŠå¤©çª—å£ï¼Œä½†ä¸å¤åˆ¶å…¶ç´ ææˆ–å•†æ ‡ã€‚"""

    message_submitted = Signal(str)
    settings_requested = Signal(str)
    rename_requested = Signal()
    reconnect_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        pet_name: str = "å…­æ¯›",
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
        self.setWindowTitle(f"å’Œ{self.pet_name}èŠèŠ")
        self.setObjectName("liliPanel")
        self.setMinimumSize(430, 520)
        self.resize(470, 580)
        self.setStyleSheet(PANEL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(f"{self.pet_name}çš„å°çº¸æ¡")
        self.pet_title = title
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        self.rename_button = QPushButton("ä¿®æ”¹ä¸»äººç§°å‘¼")
        self.rename_button.setObjectName("softButton")
        self.rename_button.setToolTip("ç”¨äºè‡ªä¹ å®¤ã€ä¸²é—¨å’Œæ­å­äº’åŠ¨æ—¶åŒºåˆ†ä¸åŒå…­æ¯›")
        self.rename_button.setAutoDefault(False)
        self.rename_button.setDefault(False)
        self.rename_button.clicked.connect(self.rename_requested.emit)
        header.addWidget(self.rename_button)
        self.settings_button = QPushButton("AI è®¾ç½®")
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
        self.reconnect_button = QPushButton("é‡æ–°è¿æ¥ AI")
        self.reconnect_button.setObjectName("softButton")
        self.reconnect_button.setAutoDefault(False)
        self.reconnect_button.setDefault(False)
        self.reconnect_button.clicked.connect(self.reconnect_requested.emit)
        recovery_layout.addWidget(self.reconnect_button)
        self.go_to_settings_button = QPushButton("å»è®¾ç½®")
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
        self.input.setPlaceholderText(f"è·Ÿ{self.pet_name}è¯´ç‚¹ä»€ä¹ˆâ€¦â€¦")
        self.input.setMaxLength(1200)
        self.input.returnPressed.connect(self._submit)
        entry.addWidget(self.input, 1)
        self.send_button = QPushButton("å‘é€")
        self.send_button.setAutoDefault(False)
        self.send_button.setDefault(False)
        self.send_button.clicked.connect(self._submit)
        entry.addWidget(self.send_button)
        layout.addLayout(entry)

        privacy = QLabel("ğŸ”’ å¯¹è¯æ‘˜è¦å’Œæœ€è¿‘æ¶ˆæ¯åªä¿å­˜åœ¨æœ¬æœºï¼›åœ¨çº¿æ¨¡å¼åªæŠŠè§’è‰²è®¾å®šã€ç›¸å…³çŸ¥è¯†å’Œæœ‰é™ä¸Šä¸‹æ–‡å‘ç»™æ‰€é€‰ AIã€‚")
        privacy.setObjectName("status")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

    def set_pet_name(self, pet_name: str) -> None:
        """æ›´æ–°èŠå¤©çª—å£ä¸­æ˜¾ç¤ºçš„æ˜µç§°ï¼Œä¸æ¸…ç©ºå·²æœ‰å¯¹è¯ã€‚"""

        self.pet_name = PET_NAME
        self.setWindowTitle(f"å’Œ{self.pet_name}èŠèŠ")
        self.pet_title.setText(f"{self.pet_name}çš„å°çº¸æ¡")
        self.input.setPlaceholderText(f"è·Ÿ{self.pet_name}è¯´ç‚¹ä»€ä¹ˆâ€¦â€¦")

    def closeEvent(self, event: QCloseEvent) -> None:
        """å…³é—­æŒ‰é’®åªéšè—èŠå¤©çª—ï¼Œä¸å…³é—­æ¡Œå® æˆ–ä¸¢å¤±ä¼šè¯ã€‚"""

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
        """åªå±•ç¤º AgentManager ç¼“å­˜çŠ¶æ€ï¼Œä¸åœ¨ UI çº¿ç¨‹æ‰§è¡Œæ£€æµ‹ã€‚"""

        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["offline"])
        if provider == "offline":
            detail = "çº¯ç¦»çº¿ Â· ä¸è”ç½‘"
        else:
            state_labels = {
                AgentConnectionState.CHECKING.value: "æ­£åœ¨åå°æ£€æµ‹",
                AgentConnectionState.CONNECTED.value: "å·²è¿æ¥ï¼Œä¼˜å…ˆä½¿ç”¨ AI",
                AgentConnectionState.DISCONNECTED.value: "æœªè¿æ¥ï¼Œå·²è‡ªåŠ¨ä½¿ç”¨ç¦»çº¿é™ªä¼´",
                AgentConnectionState.ERROR.value: "æš‚æ—¶å‡ºé”™ï¼Œå·²è‡ªåŠ¨ä½¿ç”¨ç¦»çº¿é™ªä¼´",
            }
            label = state_labels.get(state, "å·²è‡ªåŠ¨ä½¿ç”¨ç¦»çº¿é™ªä¼´")
            # AgentManager çš„ detail å¯èƒ½æ˜¯â€œCodex å·²è¿æ¥ã€‚â€ï¼Œå†æ‹¼åœ¨
            # â€œCodexï¼ˆä½¿ç”¨æœ¬æœºç™»å½•ï¼‰Â· å·²è¿æ¥â€ä¸‹é¢ä¼šé€ æˆæˆªå›¾ä¸­çš„é‡å¤çŠ¶æ€ã€‚
            # æˆåŠŸçŠ¶æ€åªä¿ç•™ä¸€ä¸ªç¨³å®šæ ‡ç­¾ï¼›å¤±è´¥çŠ¶æ€æ‰æ˜¾ç¤ºè¯Šæ–­åŸå› ã€‚
            suffix = "" if state == AgentConnectionState.CONNECTED.value else (f"\n{detail}" if detail else "")
            detail = f"{preset.label} Â· {label}{suffix}"
        self.status_label.setText(detail)

    def show_recovery_actions(self, visible: bool) -> None:
        """å¤æ‚é—®é¢˜ç¦»çº¿æ—¶æ‰æ˜¾ç¤ºæ‰‹åŠ¨æ“ä½œï¼Œä¸è‡ªåŠ¨è§¦å‘å…¶ä¸­ä»»ä½•æŒ‰é’®ã€‚"""

        self.recovery_actions.setVisible(bool(visible))

    def append_message(self, role: str, text: str) -> None:
        is_pet = role != "ä½ "
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
        self.send_button.setText(f"{self.pet_name}åœ¨æƒ³â€¦" if busy else "å‘é€")
        if not busy:
            self.input.setFocus()


class AISettingsDialog(QDialog):
    """ç¼–è¾‘éæ•æ„Ÿè¿æ¥è®¾ç½®å¹¶æŠŠæ–°ä»¤ç‰Œäº¤ç»™å‡­æ®åº“ã€‚"""

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
        self.setWindowTitle(f"Lili Â· {PET_NAME}è®¾ç½®")
        self.setObjectName("liliPanel")
        self.setMinimumWidth(500)
        self.resize(620, 760)
        self.setStyleSheet(PANEL_STYLE)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("è¿æ¥ä¸é™ªä¼´")
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
        form.addRow("å¯¹è¯æ–¹å¼", self.provider)

        self.base_url = QLineEdit(settings.ai_base_url)
        form.addRow("API åœ°å€", self.base_url)
        self.model = QLineEdit(settings.ai_model)
        form.addRow("æ¨¡å‹", self.model)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("ç•™ç©ºåˆ™ä¿ç•™å·²å®‰å…¨ä¿å­˜çš„ä»¤ç‰Œ")
        form.addRow("API ä»¤ç‰Œ", self.token)
        self.owner_nickname = QLineEdit(getattr(settings, "owner_nickname", ""))
        self.owner_nickname.setMaxLength(24)
        self.owner_nickname.setPlaceholderText("ä¾‹å¦‚ï¼šå°æ¢ã€mianmianï¼›ç•™ç©ºåˆ™æ˜¾ç¤ºæ­å­å®¶çš„å…­æ¯›")
        form.addRow("ä¸»äººç§°å‘¼", self.owner_nickname)
        layout.addLayout(form)

        self.token_status = QLabel()
        self.token_status.setObjectName("status")
        self.token_status.setWordWrap(True)
        layout.addWidget(self.token_status)
        self.connection_button = QPushButton("æ£€æµ‹æ˜¯å¦è¿æ¥")
        self.connection_button.setObjectName("softButton")
        self.connection_button.clicked.connect(self._test_connection)
        layout.addWidget(self.connection_button)
        self.open_chatgpt_button = QPushButton("æ‰“å¼€ ChatGPT")
        self.open_chatgpt_button.setObjectName("softButton")
        self.open_chatgpt_button.clicked.connect(self._open_codex_gui)
        layout.addWidget(self.open_chatgpt_button)

        self.always_on_×n·¶‰ËkºwµçM½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€ÅÅ}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ìÅÅ}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ìÅÅ}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}ÅÅ}µÕÍ¥Œ¤(€€€€€€€ÅÅ}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹ÅÅ}µÕÍ¥}Á…Ñ °€Ä¤ìÅÅ}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡ÅÅ}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‰EDƒ¦~Ï’æC¢/–ê<ˆ°ÅÅ}É½Ü¤((€€€€€€€Í•±˜¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ €ôE1¥¹•‘¥Ğ¡Í•ÑÑ¥¹Ì¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ ¤(€€€€€€€Í•±˜¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ ¹Í•ÑA±…•¡½±‘•ÉQ•áĞ ‹¢«–*£–¾ïš&û¾ò3š"[¦'š.¤±½Õ‘µÕÍ¥Œ¹•á”€¼ƒöGšbO’êG¦~Ï’æ@¹…ÁÀˆ¤(€€€€€€€¹•Ñ•…Í•}É½Ü€ôE]¥‘•Ğ ¤ì¹•Ñ•…Í•}±…å½ÕĞ€ôE!	½á1…å½ÕĞ¡¹•Ñ•…Í•}É½Ü¤ì¹•Ñ•…Í•}±…å½ÕĞ¹Í•Ñ½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€¹•Ñ•…Í•}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ì¹•Ñ•…Í•}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ì¹•Ñ•…Í•}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}¹•Ñ•…Í•}µÕÍ¥Œ¤(€€€€€€€¹•Ñ•…Í•}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ °€Ä¤ì¹•Ñ•…Í•}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡¹•Ñ•…Í•}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‹öGšbO’êG¢/–ê<ˆ°¹•Ñ•…Í•}É½Ü¤((€€€€€€€Í•±˜¹­Õ½Õ}µÕÍ¥}Á…Ñ €ôE1¥¹•‘¥Ğ¡Í•ÑÑ¥¹Ì¹­Õ½Õ}µÕÍ¥}Á…Ñ ¤(€€€€€€€Í•±˜¹­Õ½Õ}µÕÍ¥}Á…Ñ ¹Í•ÑA±…•¡½±‘•ÉQ•áĞ ‹¢«–*£–¾ïš&û¾ò3š"[¦'š.¤-Õ½Ô¹•á”€¼ƒ¦ß._¦~Ï’æ@¹…ÁÀˆ¤(€€€€€€€­Õ½Õ}É½Ü€ôE]¥‘•Ğ ¤ì­Õ½Õ}±…å½ÕĞ€ôE!	½á1…å½ÕĞ¡­Õ½Õ}É½Ü¤ì­Õ½Õ}±…å½ÕĞ¹Í•Ñ½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€­Õ½Õ}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ì­Õ½Õ}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ì­Õ½Õ}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}­Õ½Õ}µÕÍ¥Œ¤(€€€€€€€­Õ½Õ}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹­Õ½Õ}µÕÍ¥}Á…Ñ °€Ä¤ì­Õ½Õ}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡­Õ½Õ}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‹¦ß._¦~Ï’æC¢/–ê<ˆ°­Õ½Õ}É½Ü¤((€€€€€€€Í•±˜¹…ÁÁ±•}µÕÍ¥}Á…Ñ €ôE1¥¹•‘¥Ğ¡Í•ÑÑ¥¹Ì¹…ÁÁ±•}µÕÍ¥}Á…Ñ ¤(€€€€€€€Í•±˜¹…ÁÁ±•}µÕÍ¥}Á…Ñ ¹Í•ÑA±…•¡½±‘•ÉQ•áĞ ‹¢«–*£–¾ïš&û¾ò3š"[¦'š.¤ÁÁ±•5ÕÍ¥Œ¹•á”€¼5ÕÍ¥Œ¹…ÁÀˆ¤(€€€€€€€…ÁÁ±•}É½Ü€ôE]¥‘•Ğ ¤ì…ÁÁ±•}±…å½ÕĞ€ôE!	½á1…å½ÕĞ¡…ÁÁ±•}É½Ü¤ì…ÁÁ±•}±…å½ÕĞ¹Í•Ñ½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€…ÁÁ±•}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ì…ÁÁ±•}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ì…ÁÁ±•}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}…ÁÁ±•}µÕÍ¥Œ¤(€€€€€€€…ÁÁ±•}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹…ÁÁ±•}µÕÍ¥}Á…Ñ °€Ä¤ì…ÁÁ±•}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡…ÁÁ±•}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‰ÁÁ±”5ÕÍ¥Œƒ¢/–ê<ˆ°…ÁÁ±•}É½Ü¤((€€€€€€€Í•±˜¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ €ôE1¥¹•‘¥Ğ¡Í•ÑÑ¥¹Ì¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ ¤(€€€€€€€Í•±˜¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ ¹Í•ÑA±…•¡½±‘•ÉQ•áĞ ‹¢«–*£–¾ïš&û¾ò3š"[¦'š.¤MÁ½Ñ¥™ä¹•á”€¼MÁ½Ñ¥™ä¹…ÁÀˆ¤(€€€€€€€ÍÁ½Ñ¥™å}É½Ü€ôE]¥‘•Ğ ¤ìÍÁ½Ñ¥™å}±…å½ÕĞ€ôE!	½á1…å½ÕĞ¡ÍÁ½Ñ¥™å}É½Ü¤ìÍÁ½Ñ¥™å}±…å½ÕĞ¹Í•Ñ½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€ÍÁ½Ñ¥™å}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ìÍÁ½Ñ¥™å}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ìÍÁ½Ñ¥™å}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}ÍÁ½Ñ¥™å}µÕÍ¥Œ¤(€€€€€€€ÍÁ½Ñ¥™å}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ °€Ä¤ìÍÁ½Ñ¥™å}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡ÍÁ½Ñ¥™å}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‰MÁ½Ñ¥™äƒ¢/–ê<ˆ°ÍÁ½Ñ¥™å}É½Ü¤((€€€€€€€Í•±˜¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ €ôE1¥¹•‘¥Ğ¡Í•ÑÑ¥¹Ì¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ ¤(€€€€€€€Í•±˜¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ ¹Í•ÑA±…•¡½±‘•ÉQ•áĞ ‹¦'š.§²³’âšºÔ‰…‰Õ‘„ƒ¦~Ï¦ŠG¾òo–B3n»–öW–’kšº×’òk¢«–*£¢ö»š6ˆˆ¤(€€€€€€€…Õ‘¥½}É½Ü€ôE]¥‘•Ğ ¤ì…Õ‘¥½}±…å½ÕĞ€ôE!	½á1…å½ÕĞ¡…Õ‘¥½}É½Ü¤ì…Õ‘¥½}±…å½ÕĞ¹Í•Ñ½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€…Õ‘¥½}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ì…Õ‘¥½}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ì…Õ‘¥½}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}‰…‰Õ‘…}…Õ‘¥¼¤(€€€€€€€…Õ‘¥½}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ °€Ä¤ì…Õ‘¥½}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡…Õ‘¥½}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‹–ŞÓ–â¢úû¦~Ï¦ŠDˆ°…Õ‘¥½}É½Ü¤((€€€€€€€Í•±˜¹±½…±}±åÉ¥Í}Á…Ñ €ôE1¥¹•‘¥Ğ¡Í•ÑÑ¥¹Ì¹±½…±}±åÉ¥Í}Á…Ñ ¤(€€€€€€€Í•±˜¹±½…±}±åÉ¥Í}Á…Ñ ¹Í•ÑA±…•¡½±‘•ÉQ•áĞ ‹–>¿¦'¾òk’öƒšr'šv’öÿR£jQaS¾ò3š¾?¢†3’â–>”ˆ¤(€€€€€€€±åÉ¥Í}É½Ü€ôE]¥‘•Ğ ¤ì±åÉ¥Í}±…å½ÕĞ€ôE!	½á1…å½ÕĞ¡±åÉ¥Í}É½Ü¤ì±åÉ¥Í}±…å½ÕĞ¹Í•Ñ½¹Ñ•¹ÑÍ5…É¥¹Ì À°€À°€À°€À¤(€€€€€€€±åÉ¥Í}Á¥¬€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦'š.§Š˜ˆ¤ì±åÉ¥Í}Á¥¬¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤ì±åÉ¥Í}Á¥¬¹±¥­•¹½¹¹•Ğ¡Í•±˜¹}¡½½Í•}±½…±}±åÉ¥Ì¤(€€€€€€€±åÉ¥Í}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡Í•±˜¹±½…±}±åÉ¥Í}Á…Ñ °€Ä¤ì±åÉ¥Í}±…å½ÕĞ¹…‘‘]¥‘•Ğ¡±åÉ¥Í}Á¥¬¤(€€€€€€€™½É´¹…‘‘I½Ü ‹šr³–rÃš¶3¢¾7šZšr°ˆ°±åÉ¥Í}É½Ü¤(€€€€€€€Í•±˜¹±åÉ¥}µ¥¹ÕÑ•Ì€ôEMÁ¥¹	½à ¤ìÍ•±˜¹±åÉ¥}µ¥¹ÕÑ•Ì¹Í•ÑI…¹” È°€ÄÈÀ¤ìÍ•±˜¹±åÉ¥}µ¥¹ÕÑ•Ì¹Í•ÑMÕ™™¥à ˆƒ–"¦J|ˆ¤ìÍ•±˜¹±åÉ¥}µ¥¹ÕÑ•Ì¹Í•ÑY…±Õ”¡Í•ÑÑ¥¹Ì¹±åÉ¥}¥¹Ñ•ÉÙ…±}µ¥¹ÕÑ•Ì¤(€€€€€€€™½É´¹…‘‘I½Ü ‹š¶3¢¾7šÂSšÎ‡¦^Ó¦jPˆ°Í•±˜¹±åÉ¥}µ¥¹ÕÑ•Ì¤((€€€€€€€¹½Ñ”€ôE1…‰•° (€€€€€€€€€€€€‰½‘•à½±…Õ‘”½‘”ƒš¢‡–ò?–’7R£šr³šrëfï–öW¾ò3’â7¦r¢šA$-•ç¾òm••ÁM••¬½-¥µ¤ƒ’î“&3’şw–¶c–r£Îïî–º'–£–·š6»–êOˆ(€€€€€€€€€€€€‹–ºcšZç–Âkšr«š>C’úo¢º§–’[¦£¢/–ê?š:—º„½‘•àƒ–ö»–ºƒ&§jš:—–>¾ò3’ö1¥±¤ƒ–>¿’ös’âë.³®/–ºƒ&§’öÿR ½‘•àƒ–¾ç¢¾wˆ(€€€€€€€€¤(€€€€€€€¹½Ñ”¹Í•Ñ=‰©•Ñ9…µ” ‰ÍÑ…ÑÕÌˆ¤(€€€€€€€¹½Ñ”¹Í•Ñ]½É‘]É…À¡QÉÕ”¤(€€€€€€€±…å½ÕĞ¹…‘‘]¥‘•Ğ¡¹½Ñ”¤((€€€€€€€‰ÕÑÑ½¹Ì€ôE!	½á1…å½ÕĞ ¤(€€€€€€€‰ÕÑÑ½¹Ì¹…‘‘MÑÉ•Ñ  Ä¤(€€€€€€€Í•±˜¹…¹•±}‰ÕÑÑ½¸€ôEAÕÍ¡	ÕÑÑ½¸ ‹–>[šÚ ˆ¤(€€€€€€€Í•±˜¹…¹•±}‰ÕÑÑ½¸¹Í•Ñ=‰©•Ñ9…µ” ‰Í½™Ñ	ÕÑÑ½¸ˆ¤(€€€€€€€Í•±˜¹…¹•±}‰ÕÑÑ½¸¹±¥­•¹½¹¹•Ğ¡Í•±˜¹É•©•Ğ¤(€€€€€€€‰ÕÑÑ½¹Ì¹…‘‘]¥‘•Ğ¡Í•±˜¹…¹•±}‰ÕÑÑ½¸¤(€€€€€€€Í•±˜¹Í…Ù•}‰ÕÑÑ½¸€ôEAÕÍ¡	ÕÑÑ½¸ ‹’şw–¶`ˆ¤(€€€€€€€Í•±˜¹Í…Ù•}‰ÕÑÑ½¸¹±¥­•¹½¹¹•Ğ¡Í•±˜¹…•ÁĞ¤(€€€€€€€‰ÕÑÑ½¹Ì¹…‘‘]¥‘•Ğ¡Í•±˜¹Í…Ù•}‰ÕÑÑ½¸¤(€€€€€€€½ÕÑ•É}±…å½ÕĞ¹…‘‘1…å½ÕĞ¡‰ÕÑÑ½¹Ì¤(€€€€€€€Í•±˜¹}ÁÉ½Ù¥‘•É}¡…¹• ¤(€€€€€€€Í•±˜¹}µÕÍ¥}ÁÉ½Ù¥‘•É}¡…¹• ¤((€€€‘•˜}µÕÍ¥}ÁÉ½Ù¥‘•É}¡…¹•¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹–"–"¯šbû’ë–êSR£QÉ…¹ÍÁ½ÉĞƒ’â;¢«–*£¦'š¶3¢÷–*o¾ò3’â7š*+–º'¢Ã’âë–ŞË¢ş{š:—ˆˆˆ((€€€€€€€ÁÉ½Ù¥‘•È€ôÍÑÈ¡Í•±˜¹µÕÍ¥}Í•ÉÙ¥”¹ÕÉÉ•¹Ñ…Ñ„ ¤¤(€€€€€€€¥˜Í•±˜¹µÕÍ¥}µ…¹…•È¥Ì9½¹”è(€€€€€€€€€€€Í•±˜¹µÕÍ¥}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ ‹¦~Ï’æCšJ·šRû–f£¾òk¢«–*£¦'š.¥q»–öO–&7’öÿR£¾òk–Âkšr«–ò–/šJ·šRùq»¦š[š²‡šJ·šRûš^Û–ÂššÖ/šr³šrëšJ·šRû–f£ˆ¤(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€¥˜ÁÉ½Ù¥‘•È€ôô€‰…ÕÑ¼ˆè(€€€€€€€€€€€Í•±˜¹µÕÍ¥}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ¡Í•±˜¹µÕÍ¥}µ…¹…•È¹…ÕÑ½}ÍÑ…ÑÕÍ}Ñ•áĞ ¤¤(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€Í•±˜¹µÕÍ¥}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ (€€€€€€€€€€€˜‹¢«–*£¦'š.§–ŞË–ò–B¿¾òo’òc–#–Âw¢¾UíÍ•±˜¹µÕÍ¥}Í•ÉÙ¥”¹ÕÉÉ•¹ÑQ•áĞ ¥÷	q¸ˆ(€€€€€€€€€€€˜‰íÍ•±˜¹µÕÍ¥}µ…¹…•È¹ÁÉ½Ù¥‘•É}ÍÑ…ÑÕÍ}Ñ•áĞ¡ÁÉ½Ù¥‘•È¥ôˆ(€€€€€€€€¤((€€€‘•˜}ÁÉ½Ù¥‘•É}¡…¹•¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÁÉ½Ù¥‘•È€ôÍÑÈ¡Í•±˜¹ÁÉ½Ù¥‘•È¹ÕÉÉ•¹Ñ…Ñ„ ¤¤(€€€€€€€‰…Í•}ÕÉ°°µ½‘•°€ôÁÉ½Ù¥‘•É}‘•™…Õ±ÑÌ¡ÁÉ½Ù¥‘•È¤(€€€€€€€¥˜ÁÉ½Ù¥‘•È¹½Ğ¥¸ì‰½™™±¥¹”ˆ°€‰½‘•àˆ°€‰±…Õ‘”‰ôè(€€€€€€€€€€€¥˜¹½ĞÍ•±˜¹‰…Í•}ÕÉ°¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤½ÈÍ•±˜¹Í•ÑÑ¥¹Ì¹…¥}ÁÉ½Ù¥‘•È€„ôÁÉ½Ù¥‘•Èè(€€€€€€€€€€€€€€€Í•±˜¹‰…Í•}ÕÉ°¹Í•ÑQ•áĞ¡‰…Í•}ÕÉ°¤(€€€€€€€€€€€¥˜¹½ĞÍ•±˜¹µ½‘•°¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤½ÈÍ•±˜¹Í•ÑÑ¥¹Ì¹…¥}ÁÉ½Ù¥‘•È€„ôÁÉ½Ù¥‘•Èè(€€€€€€€€€€€€€€€Í•±˜¹µ½‘•°¹Í•ÑQ•áĞ¡µ½‘•°¤(€€€€€€€•¹…‰±•€ôÁÉ½Ù¥‘•È¹½Ğ¥¸ì‰½™™±¥¹”ˆ°€‰½‘•àˆ°€‰±…Õ‘”‰ô(€€€€€€€Í•±˜¹‰…Í•}ÕÉ°¹Í•Ñ¹…‰±•¡•¹…‰±•¤(€€€€€€€Í•±˜¹µ½‘•°¹Í•Ñ¹…‰±•¡•¹…‰±•¤(€€€€€€€Í•±˜¹Ñ½­•¸¹Í•Ñ¹…‰±•¡•¹…‰±•¤(€€€€€€€¥˜ÁÉ½Ù¥‘•È€„ô€‰½™™±¥¹”ˆ…¹Í•±˜¹…•¹Ñ}µ…¹…•È¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€…¡•€ôÍ•±˜¹…•¹Ñ}µ…¹…•È¹ÍÑ…ÑÕÌ¡ÁÉ½Ù¥‘•È¤(€€€€€€€€€€€±…‰•±Ì€ôì(€€€€€€€€€€€€€€€•¹Ñ½¹¹•Ñ¥½¹MÑ…Ñ”¹!-%9è€‹š¶–r£–B;–>ÃššÖ/¾òo–öO–&7¢+–’§’î7–>¿’öÿR£šïêÿ¦f«’òÓˆ°(€€€€€€€€€€€€€€€•¹Ñ½¹¹•Ñ¥½¹MÑ…Ñ”¹=99Qè…¡•¹‘•Ñ…¥°°(€€€€€€€€€€€€€€€•¹Ñ½¹¹•Ñ¥½¹MÑ…Ñ”¹%M=99Qè˜‰í…¡•¹‘•Ñ…¥±õq»¢+–’§’òk¢«–*£’öÿR£šïêÿ¦f«’òÓˆ°(€€€€€€€€€€€€€€€•¹Ñ½¹¹•Ñ¥½¹MÑ…Ñ”¹II=Hè˜‰í…¡•¹‘•Ñ…¥±õq»¢7–B;’òk’ö;¦ŠG¢«–*£¦7¢ş{ˆ°(€€€€€€€€€€€ô(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô±…‰•±Ím…¡•¹ÍÑ…Ñ•t(€€€€€€€•±¥˜ÁÉ½Ù¥‘•È€ôô€‰½‘•àˆè(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô½‘•á}‘•Ñ•Ñ¥½¹}µ•ÍÍ…” ¤(€€€€€€€•±¥˜ÁÉ½Ù¥‘•È€ôô€‰±…Õ‘”ˆè(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‹–ŞËššÖ/–"Ãšr³šrè±…Õ‘”½‘—ˆ¥˜±…Õ‘•}…Ù…¥±…‰±” ¤•±Í”€‹šjšr«ššÖ/–"À±…Õ‘”½‘—¾ò3¢+–’§š^Û’òk’öÿR£šïêÿ–n{¶Sˆ(€€€€€€€•±¥˜•¹…‰±•è(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‹Îïî–·š6»–êO’â·–ŞËšr'’î“&3ˆ¥˜Í•±˜¹É•‘•¹Ñ¥…±Ì¹¡…Ì¡ÁÉ½Ù¥‘•È¤•±Í”€‹–Âkšr«’şw–¶c’î“&3ˆ(€€€€€€€•±Í”è(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‹š&šr'–n{¶S¦÷–r£šr³šrëRš"Cˆ(€€€€€€€Í•±˜¹Ñ½­•¹}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ¡ÍÑ…ÑÕÌ¤(€€€€€€€Í•±˜¹½Á•¹}¡…ÑÁÑ}‰ÕÑÑ½¸¹Í•ÑY¥Í¥‰±”¡ÁÉ½Ù¥‘•È€ôô€‰½‘•àˆ¤(€€€€€€€Í•±˜¹½Á•¹}¡…ÑÁÑ}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡™¥¹‘}½‘•á}Õ¥}…ÁÀ ¤¥Ì¹½Ğ9½¹”¤((€€€‘•˜}½Á•¹}½‘•á}Õ¤¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹RÇR£š"ß’âï–*£š&O–ò ¡…ÑAP•Í­Ñ½ÀÁÃ¾ò3’â7š*(U$ƒ–öO’öp½‘•à1'ˆˆˆ((€€€€€€€¥˜±…Õ¹¡}½‘•á}Õ¤ ¤è(€€€€€€€€€€€Í•±˜¹Ñ½­•¹}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ ‹–ŞËš&O–ò ¡…ÑAS¾òo’î‚’îï–*‡’î7RÇ.³®/j½‘•à1$ƒš&Ÿ¢†3ˆ¤(€€€€€€€•±Í”è(€€€€€€€€€€€Í•±˜¹Ñ½­•¹}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ ‹šr«ššÖ/–"Ã–>¿š&O–òj¡…ÑAP•Í­Ñ½ÀÁÃˆ¤((€€€‘•˜}¡½½Í•}ÅÅ}µÕÍ¥Œ¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§šr³šrèEDƒ¦~Ï’æC¢/–ê?¾ò3’â7¢¾ï–>[¢/–ê?––ºçˆˆˆ((€€€€€€€Á…Ñ €ôÍ•±˜¹}¡½½Í•}µÕÍ¥}ÁÉ½É…´ ‹¦'š.¤EDƒ¦~Ï’æC¢/–ê<ˆ°Í•±˜¹ÅÅ}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹ÅÅ}µÕÍ¥}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}¡½½Í•}¹•Ñ•…Í•}µÕÍ¥Œ¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§šr³šrëöGšbO’êG¦~Ï’æC¢/–ê?¾ò3’â7¢¾ï–>[¢/–ê?––ºçˆˆˆ((€€€€€€€Á…Ñ €ôÍ•±˜¹}¡½½Í•}µÕÍ¥}ÁÉ½É…´ ‹¦'š.§öGšbO’êG¦~Ï’æC¢/–ê<ˆ°Í•±˜¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}¡½½Í•}­Õ½Õ}µÕÍ¥Œ¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§šr³šrë¦ß._¦~Ï’æC¢/–ê?¾ò3’â7¢¾ï–>[¢/–ê?––ºçˆˆˆ((€€€€€€€Á…Ñ €ôÍ•±˜¹}¡½½Í•}µÕÍ¥}ÁÉ½É…´ ‹¦'š.§¦ß._¦~Ï’æC¢/–ê<ˆ°Í•±˜¹­Õ½Õ}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹­Õ½Õ}µÕÍ¥}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}¡½½Í•}…ÁÁ±•}µÕÍ¥Œ¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§šr³šrèÁÁ±”5ÕÍ¥Œƒ¢/–ê?ˆˆˆ((€€€€€€€Á…Ñ €ôÍ•±˜¹}¡½½Í•}µÕÍ¥}ÁÉ½É…´ ‹¦'š.¤ÁÁ±”5ÕÍ¥Œƒ¢/–ê<ˆ°Í•±˜¹…ÁÁ±•}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹…ÁÁ±•}µÕÍ¥}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}¡½½Í•}ÍÁ½Ñ¥™å}µÕÍ¥Œ¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§šr³šrèMÁ½Ñ¥™äƒ¢/–ê?ˆˆˆ((€€€€€€€Á…Ñ €ôÍ•±˜¹}¡½½Í•}µÕÍ¥}ÁÉ½É…´ ‹¦'š.¤MÁ½Ñ¥™äƒ¢/–ê<ˆ°Í•±˜¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}¡½½Í•}µÕÍ¥}ÁÉ½É…´¡Í•±˜°Ñ¥Ñ±”èÍÑÈ°ÕÉÉ•¹ĞèÍÑÈ¤€´øÍÑÈè(€€€€€€€€ˆˆ‰]¥¹‘½İÌƒ¦'š.¤a¾ò1µ…=Lƒ¦'š.§–êSR£–2n»–öW¾òo¢úO–—š†’î7–¢ºãš&/–Ş—Êc¢ÒÓ¢Ş¿–úˆˆˆ((€€€€€€€¥˜ÍåÌ¹Á±…Ñ™½É´€ôô€‰‘…Éİ¥¸ˆè(€€€€€€€€€€€É•ÑÕÉ¸E¥±•¥…±½œ¹•Ñá¥ÍÑ¥¹¥É•Ñ½Éä¡Í•±˜°Ñ¥Ñ±”°ÕÉÉ•¹Ğ½È€ˆ½ÁÁ±¥…Ñ¥½¹Ìˆ¤(€€€€€€€Á…Ñ °|€ôE¥±•¥…±½œ¹•Ñ=Á•¹¥±•9…µ” (€€€€€€€€€€€Í•±˜°(€€€€€€€€€€€Ñ¥Ñ±”°(€€€€€€€€€€€ÕÉÉ•¹Ğ°(€€€€€€€€€€€€‹¢/–ê<€ ¨¹•á”¤ìïš&šr'šZ’îØ€ ¨¤ˆ°(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸Á…Ñ ((€€€‘•˜}¡½½Í•}‰…‰Õ‘…}…Õ‘¥¼¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§šr³–rÃ–ŞÓ–â¢úû¦~Ï¦ŠG¾òo–B3n»–öWnã–B3–&7òšZ’îÛ’òk’ös’âë¢¾·šÂS–>c’öOˆˆˆ((€€€€€€€Á…Ñ °|€ôE¥±•¥…±½œ¹•Ñ=Á•¹¥±•9…µ”¡Í•±˜°€‹¦'š.§–ŞÓ–â¢úû¦~Ï¦ŠDˆ°Í•±˜¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ ¹Ñ•áĞ ¤°€‹¦~Ï¦ŠD€ ¨¹İ…Ø€¨¹µÀÌ€¨¹´Ñ„€¨¹……Œ€¨¹½œ¤ìïš&šr'šZ’îØ€ ¨¤ˆ¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}¡½½Í•}±½…±}±åÉ¥Ì¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹¦'š.§R£š"ßšr'šv–r£šr³šrë’öÿR£j¦C¢†3šZšr³ˆˆˆ((€€€€€€€Á…Ñ °|€ôE¥±•¥…±½œ¹•Ñ=Á•¹¥±•9…µ”¡Í•±˜°€‹¦'š.§šr³–rÃš¶3¢¾7šZšr°ˆ°Í•±˜¹±½…±}±åÉ¥Í}Á…Ñ ¹Ñ•áĞ ¤°€‹šZšr°€ ¨¹ÑáĞ¤ìïš&šr'šZ’îØ€ ¨¤ˆ¤(€€€€€€€¥˜Á…Ñ è(€€€€€€€€€€€Í•±˜¹±½…±}±åÉ¥Í}Á…Ñ ¹Í•ÑQ•áĞ¡Á…Ñ ¤((€€€‘•˜}Ñ•ÍÑ}½¹¹•Ñ¥½¸¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹–r£–B;–>ÃššÖ/šr³šrè•¹Ğƒfï–öWš"XA'¾ò3’â7¦bï–†{–Û’ög¢ºûö»¦'¦†çˆˆˆ((€€€€€€€¥˜Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…¥Ì¹½Ğ9½¹”…¹Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…¹¥ÍIÕ¹¹¥¹œ ¤è(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€ÁÉ½Ù¥‘•È€ôÍÑÈ¡Í•±˜¹ÁÉ½Ù¥‘•È¹ÕÉÉ•¹Ñ…Ñ„ ¤¤(€€€€€€€Í•±˜¹½¹¹•Ñ¥½¹}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡…±Í”¤(€€€€€€€Í•±˜¹½¹¹•Ñ¥½¹}‰ÕÑÑ½¸¹Í•ÑQ•áĞ ‹š¶–r£ššÖ/Š˜ˆ¤(€€€€€€€Í•±˜¹…¹•±}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡…±Í”¤(€€€€€€€Í•±˜¹Í…Ù•}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡…±Í”¤(€€€€€€€Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…€ô½¹¹•Ñ¥½¹¡•­Q¡É•… (€€€€€€€€€€€ÁÉ½Ù¥‘•È°(€€€€€€€€€€€Í•±˜¹É•‘•¹Ñ¥…±Ì°(€€€€€€€€€€€Í•±˜¹‰…Í•}ÕÉ°¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€Í•±˜¹Ñ½­•¸¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤°(€€€€€€€€€€€Í•±˜°(€€€€€€€€¤(€€€€€€€Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…¹ÍÕ••‘•¹½¹¹•Ğ¡Í•±˜¹}µ…¹Õ…±}¡•­}ÍÕ••‘•¤(€€€€€€€Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…¹™…¥±•¹½¹¹•Ğ¡Í•±˜¹}µ…¹Õ…±}¡•­}™…¥±•¤(€€€€€€€Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…¹™¥¹¥Í¡•¹½¹¹•Ğ¡Í•±˜¹}½¹¹•Ñ¥½¹}™¥¹¥Í¡•¤(€€€€€€€Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…¹ÍÑ…ÉĞ ¤((€€€‘•˜}µ…¹Õ…±}¡•­}ÍÕ••‘•¡Í•±˜°É•ÍÕ±ĞèÍÑÈ¤€´ø9½¹”è(€€€€€€€€ˆˆ‹š&/–*£ššÖ/š"C–*š^ÛšnÓšZÃ–>¿¢šZš†#’â;–Ç’ê¯òO–¶cˆˆˆ((€€€€€€€Í•±˜¹Ñ½­•¹}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ¡˜‹ŠríÉ•ÍÕ±Ñôˆ¤(€€€€€€€¥˜Í•±˜¹…•¹Ñ}µ…¹…•È¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€ÁÉ½Ù¥‘•È€ôÍÑÈ¡Í•±˜¹ÁÉ½Ù¥‘•È¹ÕÉÉ•¹Ñ…Ñ„ ¤¤(€€€€€€€€€€€¥˜€‹šr«ššÖ/–"À½‘•à1$ˆ¥¸É•ÍÕ±Ğè(€€€€€€€€€€€€€€€Í•±˜¹…•¹Ñ}µ…¹…•È¹µ…É­}‘¥Í½¹¹•Ñ•¡ÁÉ½Ù¥‘•È°É•ÍÕ±Ğ¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Í•±˜¹…•¹Ñ}µ…¹…•È¹µ…É­}ÉÕ¹Ñ¥µ•}ÍÕ•ÍÌ¡ÁÉ½Ù¥‘•È¤((€€€‘•˜}µ…¹Õ…±}¡•­}™…¥±•¡Í•±˜°•ÉÉ½ÈèÍÑÈ¤€´ø9½¹”è(€€€€€€€€ˆˆ‹š&/–*£ššÖ/–’Ç¢Ò—–>«šnÓšZÃ–öO–&7¢ºûö»¦†×¾ò3’â7š&O–ò–Û’î[ª_–>ˆˆˆ((€€€€€€€Í•±˜¹Ñ½­•¹}ÍÑ…ÑÕÌ¹Í•ÑQ•áĞ¡˜‹Šv0í•ÉÉ½Éôˆ¤(€€€€€€€¥˜Í•±˜¹…•¹Ñ}µ…¹…•È¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€Í•±˜¹…•¹Ñ}µ…¹…•È¹µ…É­}ÉÕ¹Ñ¥µ•}•ÉÉ½È¡ÍÑÈ¡Í•±˜¹ÁÉ½Ù¥‘•È¹ÕÉÉ•¹Ñ…Ñ„ ¤¤°•ÉÉ½È¤((€€€‘•˜}½¹¹•Ñ¥½¹}™¥¹¥Í¡•¡Í•±˜¤€´ø9½¹”è(€€€€€€€€ˆˆ‹š‹–’7ššÖ/’â;’şw–¶cš2'¦J»–æÛ¦+šRû–ŞË–º3š"Cjêÿ¢/ˆˆˆ((€€€€€€€Ñ¡É•…€ôÍ•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…(€€€€€€€Í•±˜¹}½¹¹•Ñ¥½¹}Ñ¡É•…€ô9½¹”(€€€€€€€Í•±˜¹½¹¹•Ñ¥½¹}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡QÉÕ”¤(€€€€€€€Í•±˜¹½¹¹•Ñ¥½¹}‰ÕÑÑ½¸¹Í•ÑQ•áĞ ‹ššÖ/šb¿–B›¢ş{š:”ˆ¤(€€€€€€€Í•±˜¹…¹•±}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡QÉÕ”¤(€€€€€€€Í•±˜¹Í…Ù•}‰ÕÑÑ½¸¹Í•Ñ¹…‰±•¡QÉÕ”¤(€€€€€€€¥˜Ñ¡É•…¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€Ñ¡É•…¹‘•±•Ñ•1…Ñ•È ¤((€€€‘•˜…ÁÁ±ä¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹½İ¹•É}¹¥­¹…µ”€ôÍ•±˜¹½İ¹•É}¹¥­¹…µ”¹Ñ•áĞ ¤¹ÍÑÉ¥À ¥lèÈÑt(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹Á•Ñ}¹…µ”€ôAQ}95(€€€€€€€ÁÉ½Ù¥‘•È€ôÍÑÈ¡Í•±˜¹ÁÉ½Ù¥‘•È¹ÕÉÉ•¹Ñ…Ñ„ ¤¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…¥}ÁÉ½Ù¥‘•È€ôÁÉ½Ù¥‘•È(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…¥}‰…Í•}ÕÉ°€ôÍ•±˜¹‰…Í•}ÕÉ°¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…¥}µ½‘•°€ôÍ•±˜¹µ½‘•°¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…±İ…åÍ}½¹}Ñ½À€ôÍ•±˜¹…±İ…åÍ}½¹}Ñ½À¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…±±½İ}…ÕÑ½¹½µ½ÕÍ}İ…±¬€ôÍ•±˜¹…±±½İ}…ÕÑ½¹½µ½ÕÍ}İ…±¬¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…ÕÑ½µ…Ñ¥}ÉÕµ‰±¥¹œ€ôÍ•±˜¹ÉÕµ‰±¥¹œ¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹¡½ÕÉ±å}…¹¹½Õ¹•µ•¹Ğ€ôÍ•±˜¹¡½ÕÉ±ä¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…ÁÁ}…İ…É•¹•ÍÌ€ôÍ•±˜¹…ÁÁ}…İ…É•¹•ÍÌ¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹Ù½¥•}•¹…‰±•€ôÍ•±˜¹Ù½¥”¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹±åÉ¥}¥¹ÍÁ¥É…Ñ¥½¹}•¹…‰±•€ôÍ•±˜¹±åÉ¥}¥¹ÍÁ¥É…Ñ¥½¸¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹İ…Ñ•É}É•µ¥¹‘•É}•¹…‰±•€ôÍ•±˜¹İ…Ñ•È¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹ÍÑ…¹‘}É•µ¥¹‘•É}•¹…‰±•€ôÍ•±˜¹ÍÑ…¹¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹İ…Ñ•É}¥¹Ñ•ÉÙ…±}µ¥¹ÕÑ•Ì€ôÍ•±˜¹İ…Ñ•É}µ¥¹ÕÑ•Ì¹Ù…±Õ” ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹ÍÑ…¹‘}¥¹Ñ•ÉÙ…±}µ¥¹ÕÑ•Ì€ôÍ•±˜¹ÍÑ…¹‘}µ¥¹ÕÑ•Ì¹Ù…±Õ” ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…ÕÑ½}Á…ÕÍ•}½¹}¥‘±”€ôÍ•±˜¹…ÕÑ½}Á…ÕÍ•}¥‘±”¹¥Í¡•­• ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹¥‘±•}Á…ÕÍ•}Í•½¹‘Ì€ôÍ•±˜¹¥‘±•}Á…ÕÍ•}µ¥¹ÕÑ•Ì¹Ù…±Õ” ¤€¨€ØÀ(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹µÕÍ¥}Í•ÉÙ¥”€ôÍÑÈ¡Í•±˜¹µÕÍ¥}Í•ÉÙ¥”¹ÕÉÉ•¹Ñ…Ñ„ ¤¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹ÅÅ}µÕÍ¥}Á…Ñ €ôÍ•±˜¹ÅÅ}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ €ôÍ•±˜¹¹•Ñ•…Í•}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹­Õ½Õ}µÕÍ¥}Á…Ñ €ôÍ•±˜¹­Õ½Õ}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹…ÁÁ±•}µÕÍ¥}Á…Ñ €ôÍ•±˜¹…ÁÁ±•}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ €ôÍ•±˜¹ÍÁ½Ñ¥™å}µÕÍ¥}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ €ôÍ•±˜¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹±½…±}±åÉ¥Í}Á…Ñ €ôÍ•±˜¹±½…±}±åÉ¥Í}Á…Ñ ¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤(€€€€€€€Í•±˜¹Í•ÑÑ¥¹Ì¹±åÉ¥}¥¹Ñ•ÉÙ…±}µ¥¹ÕÑ•Ì€ôÍ•±˜¹±åÉ¥}µ¥¹ÕÑ•Ì¹Ù…±Õ” ¤(€€€€€€€¥˜ÁÉ½Ù¥‘•È¹½Ğ¥¸ì‰½™™±¥¹”ˆ°€‰½‘•àˆ°€‰±…Õ‘”‰ô…¹Í•±˜¹Ñ½­•¸¹Ñ•áĞ ¤¹ÍÑÉ¥À ¤è(€€€€€€€€€€€Í•±˜¹É•‘•¹Ñ¥…±Ì¹Í•Ğ¡ÁÉ½Ù¥‘•È°Í•±˜¹Ñ½­•¸¹Ñ•áĞ ¤¤(