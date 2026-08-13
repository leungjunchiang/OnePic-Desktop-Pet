"""æ­å­è‡ªä¹ å®¤ç•Œé¢ã€åŽå°åŒæ­¥çº¿ç¨‹å’ŒåŒå…­æ¯›æœ¬åœ°ä¸²é—¨çª—å£ã€‚"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from .resources import resource_path
from .accessories import SPECIAL_OUTFIT_SPRITES
from .social import SocialClient, SocialError
from .work_timer import format_work_duration


def _social_font() -> QFont:
    candidates = (
        (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"))
        if sys.platform == "win32"
        else (Path("/System/Library/Fonts/PingFang.ttc"), Path("/System/Library/Fonts/Hiragino Sans GB.ttc"))
    )
    family = ""
    for path in candidates:
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families: family = families[0]; break
    return QFont(family or "sans-serif", 10)


class SocialSyncThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, client: SocialClient, presence: dict[str, Any], parent=None) -> None:
        super().__init__(parent); self.client = client; self.presence = presence

    def run(self) -> None:
        try:
            self.client.heartbeat(**self.presence)
            self.completed.emit(self.client.dashboard())
        except SocialError as exc:
            self.failed.emit(str(exc))


class BuddyCardWidget(QWidget):
    """æŠŠæ­å­çš„åœ¨çº¿ã€å·¥ä½œå’Œä»Šæ—¥æ—¶é•¿æ˜¾ç¤ºæˆä¸€çœ¼èƒ½çœ‹æ¸…çš„å¡ç‰‡ã€‚"""

    interaction_requested = Signal(dict, str)

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self.setObjectName("buddyCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(3)
        online = bool(buddy.get("online"))
        working = bool(buddy.get("working"))
        nickname = str(buddy.get("nickname") or "æ­å­")
        headline = QLabel(
            f"{'ðŸŸ¢' if online else 'âšª'}  {nickname} çš„å…­æ¯›"
            f"{'æ­£åœ¨å·¥ä½œ' if working else 'æ­£åœ¨ä¼‘æ¯'}"
        )
        headline.setStyleSheet("font-size:15px;font-weight:600;color:#203847;")
        root.addWidget(headline)
        duration = buddy.get("today_seconds")
        time_text = "ä»Šæ—¥ä¸“æ³¨æ—¶é•¿å·²éšè—" if duration is None else f"å·²ä¸“æ³¨ {format_work_duration(duration)}"
        focus = QLabel(time_text)
        focus.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        root.addWidget(focus)
        outfit = str(buddy.get("outfit_key") or "ç»å…¸å…­æ¯›")
        footer = QLabel(f"å½“å‰å¨ƒè¡£ï¼š{outfit}ã€€Â·ã€€åŒå‡»æˆ–é€‰ä¸­åŽå¯æ´¾å…­æ¯›ä¸²é—¨")
        footer.setStyleSheet("color:#61727d;font-size:11px;")
        root.addWidget(footer)
        actions = QHBoxLayout()
        for kind, label in (("poke", "æˆ³ä¸€ä¸‹"), ("cheer", "åŠ æ²¹"), ("drink", "é€’å¥¶èŒ¶")):
            button = QPushButton(label)
            button.setMinimumHeight(24)
            button.clicked.connect(
                lambda _checked=False, action=kind: self.interaction_requested.emit(self.buddy, action)
            )
            actions.addWidget(button)
        root.addLayout(actions)


class BuddyVisitWindow(QWidget):
    """å®Œå…¨ç”±æœ¬åœ°ç´ æç»˜åˆ¶çš„åŒå…­æ¯›é™ªä¼´çª—å£ã€‚"""

    def __init__(self, parent=None) -> None:
        # Keep visits as ordinary top-level application windows.  The pet may
        # be pinned, but a visit must have a taskbar entry and never force
        # itself above the user's current application.
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setFont(_social_font())
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet("QWidget#card{background:rgba(238,246,249,235);border:1px solid rgba(90,110,120,80);border-radius:20px;} QLabel{color:#263746;font-family:'Microsoft YaHei UI','PingFang SC';} QPushButton{padding:8px;border-radius:10px;background:#d7ece8;}")
        card = QWidget(self); card.setObjectName("card")
        root = QVBoxLayout(self); root.addWidget(card); layout = QVBoxLayout(card)
        pets = QHBoxLayout(); self.mine = QLabel(); self.peer = QLabel()
        for label in (self.mine, self.peer):
            label.setFixedSize(220, 220)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setScaledContents(True)
            pets.addWidget(label)
        layout.addLayout(pets)
        self.title = QLabel("ä¸¤åªå…­æ¯›ä¸€èµ·å·¥ä½œä¸­"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter); self.title.setStyleSheet("font-size:18px;font-weight:700;")
        self.subtitle = QLabel("ðŸ’» å…­æ¯›ã€€ã€€å…­æ¯› ðŸ“–\nä¸€èµ·å·¥ä½œä¸­"); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock = QLabel("00:00:00"); self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter); self.clock.setStyleSheet("font-size:30px;font-weight:700;color:#087f74;")
        self.today = QLabel(); self.today.setAlignment(Qt.AlignmentFlag.AlignCenter); self.today.setStyleSheet("color:#61727d;")
        layout.addWidget(self.title); layout.addWidget(self.subtitle); layout.addWidget(self.clock); layout.addWidget(self.today)
        close = QPushButton("ç»“æŸè¿™æ¬¡ä¸²é—¨"); close.clicked.connect(self.hide_visit); layout.addWidget(close)
        self.elapsed = 0
        self._phase = 0
        self._mine_outfit = ""
        self._peer_outfit = ""
        self._mine_actions = ("02-office.png", "22-thermos.png", "04-guitar.png")
        self._peer_actions = ("09-night-reading.png", "03-headphones.png", "19-tea.png")
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.resize(520, 430)
        self.active_visit_id = ""
        self.visible_requested = False
        self.user_minimized = False
        self._presented_visit_id = ""

    def show_peer(self, peer: dict[str, Any], mine_outfit: str = "", mine_today: int = 0) -> None:
        visit_id = str(peer.get("id") or peer.get("visit_id") or "")
        # Heartbeat/dashboard refreshes are idempotent.  Never resurrect a
        # visit the user has minimized or hidden; only a new visit id may ask
        # the window to become visible.
        if visit_id and visit_id == self._presented_visit_id:
            return
        self.active_visit_id = visit_id
        self._presented_visit_id = visit_id
        self.visible_requested = True
        self.user_minimized = False
        nickname = str(peer.get("nickname") or "æ­å­")
        self.title.setText(f"{nickname} çš„å…­æ¯›æ¥ä¸²é—¨äº†")
        self.subtitle.setText(f"ðŸ’» ä½ çš„å…­æ¯›ã€€ã€€{nickname} çš„å…­æ¯› ðŸ“–\nä¸€èµ·å·¥ä½œä¸­")
        peer_today = peer.get("today_seconds")
        peer_text = "æ—¶é•¿éšè—" if peer_today is None else format_work_duration(peer_today)
        self.today.setText(f"ä½ ä»Šæ—¥ {format_work_duration(mine_today)}ã€€Â·ã€€{nickname} ä»Šæ—¥ {peer_text}")
        self._mine_outfit = mine_outfit
        self._peer_outfit = str(peer.get("outfit_key") or "")
        self._phase = 0
        self.elapsed = 0
        started = peer.get("visit_started_at")
        if started:
            try:
                self.elapsed = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(str(started))).total_seconds()))
            except ValueError:
                self.elapsed = 0
        self._refresh_pets()
        self._tick()
        self.show()

    def hide_visit(self) -> None:
        """Hide this visit without allowing a background refresh to reopen it."""
        self.visible_requested = False
        self.user_minimized = False
        self.hide()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.WindowStateChange:
            self.user_minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
        super().changeEvent(event)

    def _load_pet(self, label: QLabel, outfit_key: str, fallback_name: str) -> None:
        relative = SPECIAL_OUTFIT_SPRITES.get(outfit_key, f"assets/pet/daily-actions/{fallback_name}")
        pix = QPixmap(str(resource_path(relative)))
        label.setPixmap(pix)

    def _refresh_pets(self) -> None:
        mine_action = self._mine_actions[self._phase % len(self._mine_actions)]
        peer_action = self._peer_actions[self._phase % len(self._peer_actions)]
        # ç¬¬ä¸€å¹•å±•ç¤ºåŒæ–¹å½“å‰å¨ƒè¡£ï¼ŒåŽç»­åŠ¨ä½œå‡ç”±æœ¬åœ°è½®æ¢ï¼Œä¸åŒæ­¥åŠ¨ç”»å¸§ã€‚
        self._load_pet(self.mine, self._mine_outfit if self._phase == 0 else "", mine_action)
        self._load_pet(self.peer, self._peer_outfit if self._phase == 0 else "", peer_action)

    def _tick(self) -> None:
        if self.isVisible():
            self.elapsed += 1
            if self.elapsed % 15 == 0:
                self._phase += 1
                self._refresh_pets()
        h, rest = divmod(self.elapsed, 3600); m, s = divmod(rest, 60)
        self.clock.setText(f"{h:02d}:{m:02d}:{s:02d}")


class SocialHubDialog(QDialog):
    """æä¾›é¦–é¡µã€èŠå¤©ã€ä¸“æ³¨ã€æˆ‘çš„å››ä¸ªæ¸…æ™°é¡µé¢åŠç»Ÿä¸€æ“ä½œåé¦ˆã€‚"""

    active_visit = Signal(dict)
    focus_start_requested = Signal()
    focus_pause_requested = Signal()
    focus_finish_requested = Signal()

    def __init__(self, client: SocialClient, outfit_key: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.outfit_key = outfit_key
        self.data: dict[str, Any] = {}
        self.current_room_id: str | None = None
        self._focus_snapshot: Any = None
        self.setFont(_social_font())
        # Make this a normal independent utility window.  QDialog's default
        # flags differ by platform and can omit the minimize button when a
        # parent is supplied, which made the study room feel like a modal
        # sheet on Windows.  It deliberately does not include Tool or
        # WindowStaysOnTopHint: minimizing it must only hide this window and
        # never affect the desktop pet or its timers.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("å…­æ¯›æ­å­è‡ªä¹ å®¤")
        self.resize(760, 760)
        self.setMinimumSize(680, 660)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#pageTitle { font-size:24px; font-weight:700; }
            QLabel#sectionTitle { font-size:17px; font-weight:650; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:9px; padding:7px 10px; }
            QFrame#card, QWidget#buddyCard { background:#ffffff; border:1px solid #d6e1e6; border-radius:14px; }
            QLineEdit, QListWidget { background:#ffffff; border:1px solid #b9c8d0; border-radius:10px; padding:7px; }
            QTabWidget::pane { border:0; }
            QTabBar::tab { min-width:105px; padding:10px 16px; color:#526872; }
            QTabBar::tab:selected { color:#087f74; font-weight:700; border-bottom:3px solid #38a397; }
            QPushButton { min-height:20px; padding:8px 14px; border:0; border-radius:9px; background:#d7ece8; color:#204c4a; font-weight:600; }
            QPushButton:hover { background:#c2e2dd; }
            QPushButton:disabled { color:#91a1a8; background:#e8eef0; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 20)
        root.setSpacing(9)
        title = QLabel("å…­æ¯›æ­å­è‡ªä¹ å®¤")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel("ä¸€èµ·èŠå¤©ã€ä¸“æ³¨å’Œä¸²é—¨ï¼›æœªç™»å½•æ—¶ï¼Œå…­æ¯›ä»å¯å®Œæ•´ç¦»çº¿é™ªä¼´ã€‚")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)
        self.status_label = QLabel("é¡µé¢å·²å‡†å¤‡å¥½")
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._home_page(), "é¦–é¡µ")
        self.tabs.addTab(self._chat_page(), "èŠå¤©")
        self.tabs.addTab(self._focus_page(), "ä¸“æ³¨")
        self.tabs.addTab(self._mine_page(), "æˆ‘çš„")
        root.addWidget(self.tabs, 1)
        self._update_account_state()
        if client.signed_in:
            QTimer.singleShot(50, self.refresh)

    def set_focus_snapshot(self, snapshot: Any) -> None:
        """Render the desktop timer state without creating a second timer."""

        self._focus_snapshot = snapshot
        if not hasattr(self, "focus_status"):
            return
        status = getattr(snapshot, "status", None)
        if isinstance(snapshot, dict):
            status = snapshot.get("status")
            session_seconds = snapshot.get("session_seconds", 0)
            today_seconds = snapshot.get("today_seconds", 0)
        else:
            session_seconds = getattr(snapshot, "session_seconds", 0)
            today_seconds = getattr(snapshot, "today_seconds", 0)
        labels = {"focus": "ä¸“æ³¨ä¸­", "rest": "ä¼‘æ¯ä¸­", "idle": "å°šæœªå¼€å§‹"}
        self.focus_status.setText(labels.get(str(status), "ç­‰å¾…åŒæ­¥"))
        self.focus_clock.setText(format_work_duration(int(session_seconds)))
        self.focus_today.setText(f"ä»Šæ—¥ç´¯è®¡ {format_work_duration(int(today_seconds))}")
        self.focus_start.setEnabled(str(status) != "focus")
        self.focus_pause.setEnabled(str(status) == "focus")
        self.focus_finish.setEnabled(int(session_seconds) > 0 or int(today_seconds) > 0)

    @staticmethod
    def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("sectÛ~ô¶‰žËkºwµç@ôE1¥ÍÑ]¥‘•Ñ%Ñ•´ ‹–*ƒ–—š"ÿ¦^Ó–B;¾ò3¢þg¦3’òkšbûž’ë’â¢Öß’âOšÎ£žj–·š¾o–J3žÒ¿¢º‡š^Û¦VÿŽˆ¤(€€€€€€€€€€€•µÁÑä¹Í•Ñ±…Ì¡EÐ¹%Ñ•µ±…œ¹9½%Ñ•µ±…Ì¤(€€€€€€€€€€€Í•±˜¹É½½µ}µ•µ‰•ÉÌ¹…‘‘%Ñ•´¡•µÁÑä¤((€€€‘•˜}É•¹‘•É}É½½µ}…Ñ¥Ù¥Ñä¡Í•±˜°•¹ÑÉ¥•Ìè±¥ÍÑm¹åt¤€´ø9½¹”è(€€€€€€€¥˜¹½Ð¡…Í…ÑÑÈ¡Í•±˜°€‰É½½µ}…Ñ¥Ù¥Ñäˆ¤è(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€Í•±˜¹É½½µ}…Ñ¥Ù¥Ñä¹±•…È ¤(€€€€€€€™½È•¹ÑÉä¥¸•¹ÑÉ¥•Íl´àétè(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡•¹ÑÉä°‘¥Ð¤è(€€€€€€€€€€€€€€€Ñ•áÐ€ôÍÑÈ¡•¹ÑÉä¹•Ð ‰Ñ•áÐˆ¤½È•¹ÑÉä¹•Ð ‰µ•ÍÍ…”ˆ¤½È€ˆˆ¤(€€€€€€€€€€€€€€€¥˜¹½ÐÑ•áÐè(€€€€€€€€€€€€€€€€€€€Ñ•áÐ€ô˜‰í•¹ÑÉä¹•Ð ¹¥­¹…µ”œ°€ŸšB·–¶@œ¥ôí•¹ÑÉä¹•Ð ­¥¹œ°€ŸšnÓšZÃ’êž*Ûšœ¥ôˆ(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Ñ•áÐ€ôÍÑÈ¡•¹ÑÉä¤(€€€€€€€€€€€¥˜Ñ•áÐè(€€€€€€€€€€€€€€€Í•±˜¹É½½µ}…Ñ¥Ù¥Ñä¹…‘‘%Ñ•´¡Ñ•áÐ¤(€€€€€€€¥˜Í•±˜¹É½½µ}…Ñ¥Ù¥Ñä¹½Õ¹Ð ¤€ôô€Àè(€€€€€€€€€€€Í•±˜¹É½½µ}…Ñ¥Ù¥Ñä¹…‘‘%Ñ•´ ‹š"ÿ¦^Ó–*£š’òkšbûž’ë–ò–ž/’âOšÎ£Ž–º3š"C’â¢ö»–J3–·š¾o’êK–*£Žˆ¤((€€€‘•˜}µ¥¹•}Á…”¡Í•±˜¤€´øE]¥‘•Ðè(€€€€€€€Á…”€ôE]¥‘•Ð ¤ì±…å½ÕÐ€ôEY	½á1…å½ÕÐ¡Á…”¤ì±…å½ÕÐ¹Í•ÑMÁ…¥¹œ ÄÈ¤(€€€€€€€Í•±˜¹…½Õ¹Ñ}ÍÑ…¬€ôEMÑ…­•‘]¥‘•Ð ¤(€€€€€€€Í•±˜¹…½Õ¹Ñ}ÍÑ…¬¹…‘‘]¥‘•Ð¡Í•±˜¹}…ÕÑ¡}…É ¤¤(€€€€€€€Í•±˜¹…½Õ¹Ñ}ÍÑ…¬¹…‘‘]¥‘•Ð¡Í•±˜¹}ÁÉ½™¥±•}…É ¤¤(€€€€€€€±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í•±˜¹…½Õ¹Ñ}ÍÑ…¬¤(€€€€€€€ÁÉ•Ù¥•Ý}…É°ÁÉ•Ù¥•Ý}±…å½ÕÐ€ôÍ•±˜¹}…É (€€€€€€€€€€€€‹žfï–öW–B;–>¿’î—–k’î’æ ˆ°(€€€€€€€€€€€€‹¢Ò›–>ß–>«žR£’ê;šB·–¶C’â;žž’êë¢«’æƒ–º“¾òo¢+–’§Ž¢º‡š^ÛŽ–*£’ös–J3žšïžêÿ¦f«’òÓ’â7žfï–öW’æ¢÷’öÿžR£Žˆ°(€€€€€€€€¤(€€€€€€€ÁÉ•Ù¥•Ý}±…å½ÕÐ¹…‘‘]¥‘•Ð¡E1…‰•° ‹ŠˆƒšÞï–*ƒšB·–¶C–æÛš~—žr/–r£žêÿž*Ûšq»Šˆƒ–"o–îëžž’êë’âOšÎ£š"ÿ¦^Ñq»Šˆƒš:—šRÛ’âË¦^£¦
¢¾ß–æÛ’â¢Öß¢º‡š^Øˆ¤¤(€€€€€€€±…å½ÕÐ¹…‘‘]¥‘•Ð¡ÁÉ•Ù¥•Ý}…É¤(€€€€€€€±…å½ÕÐ¹…‘‘MÑÉ•Ñ  ¤(€€€€€€€É•ÑÕÉ¸Á…”((€€€‘•˜}…ÕÑ¡}…É¡Í•±˜¤€´øE]¥‘•Ðè(€€€€€€€…É°±…å½ÕÐ€ôÍ•±˜¹}…É ‹¢Ò›–>Üˆ°€‹¦
»žºÇ–>«žR£’ê;žfï–öW¾òo–¾ž‚’â7’òk’þw–¶c–r 1¥±§Žˆ¤(€€€€€€€…ÕÑ¡}Ñ…‰Ì€ôEQ…‰]¥‘•Ð ¤(€€€€€€€±½¥¸€ôE]¥‘•Ð ¤ì±½¥¹}±…å½ÕÐ€ôEY	½á1…å½ÕÐ¡±½¥¸¤ì±½¥¹}™½É´€ôE½Éµ1…å½ÕÐ ¤(€€€€€€€Í•±˜¹±½¥¹}•µ…¥°€ôE1¥¹•‘¥Ð ¤ìÍ•±˜¹±½¥¹}Á…ÍÍÝ½É€ôE1¥¹•‘¥Ð ¤ìÍ•±˜¹±½¥¹}Á…ÍÍÝ½É¹Í•Ñ¡½5½‘”¡E1¥¹•‘¥Ð¹¡½5½‘”¹A…ÍÍÝ½É¤(€€€€€€€±½¥¹}™½É´¹…‘‘I½Ü ‹¦
»žºÄˆ°Í•±˜¹±½¥¹}•µ…¥°¤ì±½¥¹}™½É´¹…‘‘I½Ü ‹–¾ž‚ˆ°Í•±˜¹±½¥¹}Á…ÍÍÝ½É¤(€€€€€€€±½¥¹}±…å½ÕÐ¹…‘‘1…å½ÕÐ¡±½¥¹}™½É´¤ì±½¥¹}‰ÕÑÑ½¸€ôEAÕÍ¡	ÕÑÑ½¸ ‹žfï–öTˆ¤(€€€€€€€±½¥¹}‰ÕÑÑ½¸¹±¥­•¹½¹¹•Ð¡Í•±˜¹}±½¥¸¤ì±½¥¹}±…å½ÕÐ¹…‘‘]¥‘•Ð¡±½¥¹}‰ÕÑÑ½¸¤ì±½¥¹}±…å½ÕÐ¹…‘‘MÑÉ•Ñ  ¤(€€€€€€€É•¥ÍÑ•È€ôE]¥‘•Ð ¤ìÉ•¥ÍÑ•É}±…å½ÕÐ€ôEY	½á1…å½ÕÐ¡É•¥ÍÑ•È¤ìÉ•¥ÍÑ•É}™½É´€ôE½Éµ1…å½ÕÐ ¤(€€€€€€€Í•±˜¹Í¥¹ÕÁ}¹¥­¹…µ”€ôE1¥¹•‘¥Ð ‹–·š¾ošB·–¶@ˆ¤ìÍ•±˜¹Í¥¹ÕÁ}•µ…¥°€ôE1¥¹•‘¥Ð ¤ìÍ•±˜¹Í¥¹ÕÁ}Á…ÍÍÝ½É€ôE1¥¹•‘¥Ð ¤ìÍ•±˜¹Í¥¹ÕÁ}Á…ÍÍÝ½É¹Í•Ñ¡½5½‘”¡E1¥¹•‘¥Ð¹¡½5½‘”¹A…ÍÍÝ½É¤(€€€€€€€É•¥ÍÑ•É}™½É´¹…‘‘I½Ü ‹šb×žžÀˆ°Í•±˜¹Í¥¹ÕÁ}¹¥­¹…µ”¤ìÉ•¥ÍÑ•É}™½É´¹…‘‘I½Ü ‹¦
»žºÄˆ°Í•±˜¹Í¥¹ÕÁ}•µ…¥°¤ìÉ•¥ÍÑ•É}™½É´¹…‘‘I½Ü ‹–¾ž‚ˆ°Í•±˜¹Í¥¹ÕÁ}Á…ÍÍÝ½É¤(€€€€€€€É•¥ÍÑ•É}±…å½ÕÐ¹…‘‘1…å½ÕÐ¡É•¥ÍÑ•É}™½É´¤ìÍ¥¹ÕÁ}‰ÕÑÑ½¸€ôEAÕÍ¡	ÕÑÑ½¸ ‹šÎ£–0ˆ¤(€€€€€€€Í¥¹ÕÁ}‰ÕÑÑ½¸¹±¥­•¹½¹¹•Ð¡Í•±˜¹}Í¥¹ÕÀ¤ìÉ•¥ÍÑ•É}±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í¥¹ÕÁ}‰ÕÑÑ½¸¤ìÉ•¥ÍÑ•É}±…å½ÕÐ¹…‘‘MÑÉ•Ñ  ¤(€€€€€€€…ÕÑ¡}Ñ…‰Ì¹…‘‘Q…ˆ¡±½¥¸°€‹žfï–öTˆ¤ì…ÕÑ¡}Ñ…‰Ì¹…‘‘Q…ˆ¡É•¥ÍÑ•È°€‹šÎ£–0ˆ¤(€€€€€€€±…å½ÕÐ¹…‘‘]¥‘•Ð¡…ÕÑ¡}Ñ…‰Ì¤(€€€€€€€É•ÑÕÉ¸…É((€€€‘•˜}ÁÉ½™¥±•}…É¡Í•±˜¤€´øE]¥‘•Ðè(€€€€€€€…É°±…å½ÕÐ€ôÍ•±˜¹}…É ‹š"Gžj¢Ò›–>Üˆ°€‹žº‡žBšB·–¶Cž‚Ž–>¿¢žšŸ–J3’âË¦^£šv¦fCŽˆ¤(€€€€€€€Í•±˜¹¥‘•¹Ñ¥Ñä€ôE1…‰•° ¤ìÍ•±˜¹¥‘•¹Ñ¥Ñä¹Í•ÑMÑå±•M¡••Ð ‰™½¹ÐµÍ¥é”èÄáÁàí™½¹ÐµÝ•¥¡ÐèØÔÀìˆ¤ìÍ•±˜¹¥‘•¹Ñ¥Ñä¹Í•Ñ]½É‘]É…À¡QÉÕ”¤(€€€€€€€±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í•±˜¹¥‘•¹Ñ¥Ñä¤(€€€€€€€Í•±˜¹¡¥‘‘•¸€ôE¡•­	½à ‹¦jC¢ê¬ˆ¤(€€€€€€€Í•±˜¹•á…Ð€ôE¡•­	½à ‹šbûž’ë–ž†»š^Û¦Vüˆ¤(€€€€€€€Í•±˜¹Ù¥Í¥ÑÍ}…±±½Ý•€ôE¡•­	½à ‹–¢ºãšB·–¶C’âË¦^ ˆ¤(€€€€€€€±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í•±˜¹¡¥‘‘•¸¤ì±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í•±˜¹•á…Ð¤ì±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í•±˜¹Ù¥Í¥ÑÍ}…±±½Ý•¤(€€€€€€€Í…Ù”€ôEAÕÍ¡	ÕÑÑ½¸ ‹’þw–¶c¦jCžž¢ºûžö¸ˆ¤ìÍ…Ù”¹±¥­•¹½¹¹•Ð¡Í•±˜¹}Í…Ù•}ÁÉ½™¥±”¤ì±…å½ÕÐ¹…‘‘]¥‘•Ð¡Í…Ù”¤(€€€€€€€±½½ÕÐ€ôEAÕÍ¡	ÕÑÑ½¸ ‹¦–ë¢Ò›–>Üˆ¤ì±½½ÕÐ¹±¥­•¹½¹¹•Ð¡Í•±˜¹}±½½ÕÐ¤ì±…å½ÕÐ¹…‘‘]¥‘•Ð¡±½½ÕÐ¤(€€€€€€€±…å½ÕÐ¹…‘‘MÑÉ•Ñ  ¤(€€€€€€€É•ÑÕÉ¸…É((€€€‘•˜}Í•Ñ}ÍÑ…ÑÕÌ¡Í•±˜°µ•ÍÍ…”èÍÑÈ°€¨°•ÉÉ½Èè‰½½°€ô…±Í”¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÍÑ…ÑÕÍ}±…‰•°¹Í•ÑQ•áÐ¡µ•ÍÍ…”¤(€€€€€€€½±½È€ô€ˆ„ÌÍ„Í„ˆ¥˜•ÉÉ½È•±Í”€ˆŒÀàÝ˜ÜÐˆ(€€€€€€€‰…­É½Õ¹€ô€ˆ˜Ý”Õ”Ôˆ¥˜•ÉÉ½È•±Í”€ˆ”Å•™•Œˆ(€€€€€€€Í•±˜¹ÍÑ…ÑÕÍ}±…‰•°¹Í•ÑMÑå±•M¡••Ð¡˜‰‰…­É½Õ¹éí‰…­É½Õ¹‘ôí½±½Èéí½±½Éôí‰½É‘•ÈµÉ…‘¥ÕÌèåÁàíÁ…‘‘¥¹œèÝÁà€ÄÁÁàìˆ¤((€€€‘•˜}‰•¥¹}…Ñ¥½¸¡Í•±˜°µ•ÍÍ…”èÍÑÈ¤€´ø9½¹”è(€€€€€€€Í•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ¡µ•ÍÍ…”¤(€€€€€€€¥˜EÁÁ±¥…Ñ¥½¸¹½Ù•ÉÉ¥‘•ÕÉÍ½È ¤¥Ì9½¹”è(€€€€€€€€€€€EÁÁ±¥…Ñ¥½¸¹Í•Ñ=Ù•ÉÉ¥‘•ÕÉÍ½È¡EÐ¹ÕÉÍ½ÉM¡…Á”¹]…¥ÑÕÉÍ½È¤(€€€€€€€EÁÁ±¥…Ñ¥½¸¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}•¹‘}…Ñ¥½¸ ¤€´ø9½¹”è(€€€€€€€¥˜EÁÁ±¥…Ñ¥½¸¹½Ù•ÉÉ¥‘•ÕÉÍ½È ¤¥Ì¹½Ð9½¹”è(€€€€€€€€€€€EÁÁ±¥…Ñ¥½¸¹É•ÍÑ½É•=Ù•ÉÉ¥‘•ÕÉÍ½È ¤((€€€‘•˜}É•ÅÕ¥É•}±½¥¸¡Í•±˜¤€´ø‰½½°è(€€€€€€€¥˜Í•±˜¹±¥•¹Ð¹Í¥¹•‘}¥¸è(€€€€€€€€€€€É•ÑÕÉ¸QÉÕ”(€€€€€€€Í•±˜¹Ñ…‰Ì¹Í•ÑÕÉÉ•¹Ñ%¹‘•à Ì¤(€€€€€€€Í•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹¢¾ß–#–r£Šsš"GžjŠw¦†×¦v‹žfï–öW¾òo–Û’î[žšïžêÿ–*¢÷’î7–>¿š¶–âã’öÿžR£Žˆ°•ÉÉ½ÈõQÉÕ”¤(€€€€€€€É•ÑÕÉ¸…±Í”((€€€‘•˜}ÕÁ‘…Ñ•}…½Õ¹Ñ}ÍÑ…Ñ”¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹…½Õ¹Ñ}ÍÑ…¬¹Í•ÑÕÉÉ•¹Ñ%¹‘•à Ä¥˜Í•±˜¹±¥•¹Ð¹Í¥¹•‘}¥¸•±Í”€À¤(€€€€€€€¥˜¹½ÐÍ•±˜¹±¥•¹Ð¹Í¥¹•‘}¥¸è(€€€€€€€€€€€Í•±˜¹}™¥±±}Í¥¹•‘}½ÕÑ}Á±…•¡½±‘•ÉÌ ¤((€€€‘•˜}™¥±±}Í¥¹•‘}½ÕÑ}Á±…•¡½±‘•ÉÌ¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹‰Õ‘‘¥•Ì¹±•…È ¤ìÍ•±˜¹‰Õ‘‘¥•Ì¹…‘‘%Ñ•´ ‹žfï–öW–B;¾ò3¢þg¦3’òkšbûž’ëšB·–¶Cžj–r£žêÿ’â;’âOšÎ£ž*ÛšŽˆ¤(€€€€€€€Í•±˜¹¥¹‰½à¹±•…È ¤ìÍ•±˜¹¥¹‰½à¹…‘‘%Ñ•´ ‹žfï–öW–B;–>¿š:—šRÛšB·–¶CžRÏ¢¾ß’â;’âË¦^£¦
¢¾ßŽˆ¤(€€€€€€€Í•±˜¹É½½µÌ¹±•…È ¤ìÍ•±˜¹É½½µÌ¹…‘‘%Ñ•´ ‹žfï–öW–B;–>¿–"o–îëš"[–*ƒ–—žž’êë¢«’æƒ–º“Žˆ¤(€€€€€€€¥˜¡…Í…ÑÑÈ¡Í•±˜°€‰É½½µ}µ•µ‰•ÉÌˆ¤è(€€€€€€€€€€€Í•±˜¹}É•¹‘•É}É½½µ}Á•½Á±”¡mt¤(€€€€€€€¥˜¡…Í…ÑÑÈ¡Í•±˜°€‰É½½µ}…Ñ¥Ù¥Ñäˆ¤è(€€€€€€€€€€€Í•±˜¹}É•¹‘•É}É½½µ}…Ñ¥Ù¥Ñä¡mt¤((€€€‘•˜}•ÉÉ½È¡Í•±˜°•áŒèá•ÁÑ¥½¸¤€´ø9½¹”è(€€€€€€€Í•±˜¹}•¹‘}…Ñ¥½¸ ¤(€€€€€€€Í•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ¡ÍÑÈ¡•áŒ¤°•ÉÉ½ÈõQÉÕ”¤(€€€€€€€E5•ÍÍ…•	½à¹Ý…É¹¥¹œ¡Í•±˜°€‹–·š¾ošB·–¶C¢«’æƒ–ºˆ°ÍÑÈ¡•áŒ¤¤((€€€‘•˜}Í¥¹ÕÀ¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£–"o–îë¢Ò›–>ßŠ˜ˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Í¥¹•€ôÍ•±˜¹±¥•¹Ð¹Í¥¹}ÕÀ¡Í•±˜¹Í¥¹ÕÁ}•µ…¥°¹Ñ•áÐ ¤°Í•±˜¹Í¥¹ÕÁ}Á…ÍÍÝ½É¹Ñ•áÐ ¤°Í•±˜¹Í¥¹ÕÁ}¹¥­¹…µ”¹Ñ•áÐ ¤¤(€€€€€€€€€€€Í•±˜¹}•¹‘}…Ñ¥½¸ ¤(€€€€€€€€€€€¥˜Í¥¹•è(€€€€€€€€€€€€€€€Í•±˜¹}ÕÁ‘…Ñ•}…½Õ¹Ñ}ÍÑ…Ñ” ¤ìÍ•±˜¹É•™É•Í  ¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Í•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹šÎ£–3š"C–*¾ò3¢¾ß–"Ã¦
»žºÇž†»¢º“–B;–n{šv—žfï–öWŽˆ¤(€€€€€€€€€€€€€€€E5•ÍÍ…•	½à¹¥¹™½Éµ…Ñ¥½¸ (€€€€€€€€€€€€€€€€€€€Í•±˜°(€€€€€€€€€€€€€€€€€€€€‹¢¾ßž†»¢º“¦
»žºÄˆ°(€€€€€€€€€€€€€€€€€€€€‹šÎ£–3š"C–*Ž¢¾ß–"Ã¦
»žºÇ–º3š"Cž†»¢º“¾ò3žÛ–B;–n{–"Ã¢þg¦3žfï–öWŽ	q¹q¸ˆ(€€€€€€€€€€€€€€€€€€€€‹ž†»¢º“¦†×’òkš&O–ò–·š¾o¦†çžn»¦†×¦v‹¾ò3’â7¦r¢š–B¿–* ±½…±¡½ÍÐƒšr7–*‡Žˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒè(€€€€€€€€€€€Í•±˜¹}•ÉÉ½È¡•áŒ¤((€€€‘•˜}±½¥¸¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£žfï–öWšB·–¶C¢«’æƒ–º“Š˜ˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Í•±˜¹±¥•¹Ð¹Í¥¹}¥¸¡Í•±˜¹±½¥¹}•µ…¥°¹Ñ•áÐ ¤°Í•±˜¹±½¥¹}Á…ÍÍÝ½É¹Ñ•áÐ ¤¤(€€€€€€€€€€€Í•±˜¹}•¹‘}…Ñ¥½¸ ¤ìÍ•±˜¹}ÕÁ‘…Ñ•}…½Õ¹Ñ}ÍÑ…Ñ” ¤ìÍ•±˜¹Ñ…‰Ì¹Í•ÑÕÉÉ•¹Ñ%¹‘•à À¤ìÍ•±˜¹É•™É•Í  ¤(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒè(€€€€€€€€€€€Í•±˜¹}•ÉÉ½È¡•áŒ¤((€€€‘•˜}±½½ÕÐ¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹±¥•¹Ð¹Í¥¹}½ÕÐ ¤ìÍ•±˜¹‘…Ñ„€ôíôìÍ•±˜¹}ÕÁ‘…Ñ•}…½Õ¹Ñ}ÍÑ…Ñ” ¤ìÍ•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹–ÞË¦–ë¢Ò›–>ß¾ò3–·š¾ožîŸžî·žšïžêÿ¦f«’òÓŽˆ¤((€€€‘•˜É•™É•Í ¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£–"ßšZÃšB·–¶C’â;’âOšÎ£ž*ÛšŠ˜ˆ¤(€€€€€€€ÑÉäèÍ•±˜¹‘…Ñ„õÍ•±˜¹±¥•¹Ð¹‘…Í¡‰½…É ¤(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤ìÉ•ÑÕÉ¸(€€€€€€€Í•±˜¹}•¹‘}…Ñ¥½¸ ¤(€€€€€€€µ”õÍ•±˜¹‘…Ñ„¹•Ð ‰µ”ˆ¤½ÈíôìÍ•±˜¹¥‘•¹Ñ¥Ñä¹Í•ÑQ•áÐ¡˜‰íµ”¹•Ð ¹¥­¹…µ”œ°Ÿ–·š¾ošB·–¶@œ¥ôƒ
Üƒš"GžjšB·–¶Cž‚¾òiíµ”¹•Ð ¥¹Ù¥Ñ•}½‘”œ°œ´´´´´´´´œ¥ôˆ¤(€€€€€€€Í•±˜¹¡¥‘‘•¸¹Í•Ñ¡•­•¡µ”¹•Ð ‰Ù¥Í¥‰¥±¥Ñäˆ¤€ôô€‰¡¥‘‘•¸ˆ¤ìÍ•±˜¹•á…Ð¹Í•Ñ¡•­•¡‰½½°¡µ”¹•Ð ‰Í¡½Ý}•á…Ñ}Ñ¥µ”ˆ±QÉÕ”¤¤¤ìÍ•±˜¹Ù¥Í¥ÑÍ}…±±½Ý•¹Í•Ñ¡•­•¡‰½½°¡µ”¹•Ð ‰…±±½Ý}Ù¥Í¥ÑÌˆ±QÉÕ”¤¤¤(€€€€€€€Í•±˜¹‰Õ‘‘¥•Ì¹±•…È ¤(€€€€€€€Á•½Á±”ô¡Í•±˜¹‘…Ñ„¹•Ð ‰‰Õ‘‘¥•Ìˆ¤½Èmt¤¬¡Í•±˜¹‘…Ñ„¹•Ð ‰É½½µ}Á•½Á±”ˆ¤½Èmt¤(€€€€€€€Í••¸õÍ•Ð ¤(€€€€€€€Ý½É­¥¹}½Õ¹Ð€ô€À(€€€€€€€Ù¥Í¥‰±•}Ñ½Ñ…°€ô€À(€€€€€€€™½È‰Õ‘‘ä¥¸Á•½Á±”è(€€€€€€€€€€€¥˜‰Õ‘‘ä¹•Ð ‰ÕÍ•É}¥ˆ¤¥¸Í••¸è½¹Ñ¥¹Õ”(€€€€€€€€€€€Í••¸¹…‘¡‰Õ‘‘ä¹•Ð ‰ÕÍ•É}¥ˆ¤¤(€€€€€€€€€€€Ý½É­¥¹}½Õ¹Ð€¬ô¥¹Ð¡‰½½°¡‰Õ‘‘ä¹•Ð ‰Ý½É­¥¹œˆ¤¤¤(€€€€€€€€€€€‘ÕÉ…Ñ¥½¸€ô‰Õ‘‘ä¹•Ð ‰Ñ½‘…å}Í•½¹‘Ìˆ¤(€€€€€€€€€€€¥˜‘ÕÉ…Ñ¥½¸¥Ì¹½Ð9½¹”èÙ¥Í¥‰±•}Ñ½Ñ…°€¬ôµ…à À°¥¹Ð¡‘ÕÉ…Ñ¥½¸¤¤(€€€€€€€€€€€¥Ñ•´õE1¥ÍÑ]¥‘•Ñ%Ñ•´ ¤ì¥Ñ•´¹Í•ÑM¥é•!¥¹Ð¡EM¥é” À°€ÄÈÔ¤¤ì¥Ñ•´¹Í•Ñ…Ñ„¡EÐ¹%Ñ•µ…Ñ…I½±”¹UÍ•ÉI½±”±‰Õ‘‘ä¤ìÍ•±˜¹‰Õ‘‘¥•Ì¹…‘‘%Ñ•´¡¥Ñ•´¤(€€€€€€€€€€€‰Õ‘‘å}Ý¥‘•Ð€ô	Õ‘‘å…É‘]¥‘•Ð¡‰Õ‘‘ä°Í•±˜¹‰Õ‘‘¥•Ì¤(€€€€€€€€€€€‰Õ‘‘å}Ý¥‘•Ð¹¥¹Ñ•É…Ñ¥½¹}É•ÅÕ•ÍÑ•¹½¹¹•Ð¡Í•±˜¹}Í•¹‘}¥¹Ñ•É…Ñ¥½¸¤(€€€€€€€€€€€Í•±˜¹‰Õ‘‘¥•Ì¹Í•Ñ%Ñ•µ]¥‘•Ð¡¥Ñ•´°‰Õ‘‘å}Ý¥‘•Ð¤(€€€€€€€µ•}Í•½¹‘Ì€ô¥¹Ð ¡Í•±˜¹‘…Ñ„¹•Ð ‰µ”ˆ¤½Èíô¤¹•Ð ‰Ñ½‘…å}Í•½¹‘Ìˆ¤½È€À¤(€€€€€€€Í•±˜¹ÍÑÕ‘å}ÍÕµµ…Éä¹Í•ÑQ•áÐ (€€€€€€€€€€€˜‹ž:Ã–r íÝ½É­¥¹}½Õ¹Ñôƒ’ö7šB·–¶Cš¶–r£’âOšÎ£Ž
ßŽ ˆ(€€€€€€€€€€€˜‹š"Gžj’î+š^—’âOšÎ í™½Éµ…Ñ}Ý½É­}‘ÕÉ…Ñ¥½¸¡µ•}Í•½¹‘Ì¥÷Ž
ßŽ ˆ(€€€€€€€€€€€˜‹š"ÿ¦^Ó–>¿¢ž–B#¢º„í™½Éµ…Ñ}Ý½É­}‘ÕÉ…Ñ¥½¸¡Ù¥Í¥‰±•}Ñ½Ñ…°¥ôˆ(€€€€€€€€¤(€€€€€€€¥˜¹½ÐÍ••¸è(€€€€€€€€€€€•µÁÑä€ôE1¥ÍÑ]¥‘•Ñ%Ñ•´ ‹¢þcšÊ‡šr'šB·–¶CŽž
ç–ï’â/šZçŠsžR£šB·–¶Cž‚šÞï–*ƒŠw¾ò3’â¢Öß–Þ—’ösš^Û¢þg¦3’òkšbûž’ëšâš–kžj’âOšÎ£š^Û¦VÿŽˆ¤(€€€€€€€€€€€•µÁÑä¹Í•Ñ±…Ì¡EÐ¹%Ñ•µ±…œ¹9½%Ñ•µ±…Ì¤ìÍ•±˜¹‰Õ‘‘¥•Ì¹…‘‘%Ñ•´¡•µÁÑä¤(€€€€€€€Í•±˜¹¥¹‰½à¹±•…È ¤(€€€€€€€™½ÈÉ•ÅÕ•ÍÐ¥¸Í•±˜¹‘…Ñ„¹•Ð ‰É•ÅÕ•ÍÑÌˆ¤½Èmtè(€€€€€€€€€€€¥Ñ•´õE1¥ÍÑ]¥‘•Ñ%Ñ•´¡˜‹šB·–¶CžRÏ¢¾ß¾òiíÉ•ÅÕ•ÍÐ¹•Ð ¹¥­¹…µ”œ¥ôˆ¤ì¥Ñ•´¹Í•Ñ…Ñ„¡EÐ¹%Ñ•µ…Ñ…I½±”¹UÍ•ÉI½±”° ‰‰Õ‘‘äˆ±É•ÅÕ•ÍÐ¤¤ìÍ•±˜¹¥¹‰½à¹…‘‘%Ñ•´¡¥Ñ•´¤(€€€€€€€™½ÈÙ¥Í¥Ð¥¸Í•±˜¹‘…Ñ„¹•Ð ‰Ù¥Í¥ÑÌˆ¤½Èmtè(€€€€€€€€€€€¥Ñ•´õE1¥ÍÑ]¥‘•Ñ%Ñ•´¡˜‹’âË¦^£¦
¢¾ß¾òiíÙ¥Í¥Ð¹•Ð ¹¥­¹…µ”œ¥ôˆ¤ì¥Ñ•´¹Í•Ñ…Ñ„¡EÐ¹%Ñ•µ…Ñ…I½±”¹UÍ•ÉI½±”° ‰Ù¥Í¥Ðˆ±Ù¥Í¥Ð¤¤ìÍ•±˜¹¥¹‰½à¹…‘‘%Ñ•´¡¥Ñ•´¤(€€€€€€€¥˜Í•±˜¹¥¹‰½à¹½Õ¹Ð ¤€ôô€Àè(€€€€€€€€€€€•µÁÑä€ôE1¥ÍÑ]¥‘•Ñ%Ñ•´ ‹–öO–&7šÊ‡šr'–ú–’žBžRÏ¢¾ßš"[’âË¦^£¾ò3šZÃžj¦
¢¾ß’òkšbûž’ë–r£¢þg¦3Žˆ¤(€€€€€€€€€€€•µÁÑä¹Í•Ñ±…Ì¡EÐ¹%Ñ•µ±…œ¹9½%Ñ•µ±…Ì¤ìÍ•±˜¹¥¹‰½à¹…‘‘%Ñ•´¡•µÁÑä¤(€€€€€€€Í•±˜¹É½½µÌ¹±•…È ¤(€€€€€€€™½ÈÉ½½´¥¸Í•±˜¹‘…Ñ„¹•Ð ‰É½½µÌˆ¤½Èmtè(€€€€€€€€€€€É½½µ}¥Ñ•´€ôE1¥ÍÑ]¥‘•Ñ%Ñ•´ (€€€€€€€€€€€€€€€˜‰íÉ½½´¹•Ð ¹…µ”œ¥ôƒ
ÜíÉ½½´¹•Ð µ•µ‰•ÉÌœ¥ôƒ’êèƒ
Üƒš"ÿ¦^Óž‚íÉ½½´¹•Ð ¥¹Ù¥Ñ•}½‘”œ¥ôˆ(€€€€€€€€€€€€¤(€€€€€€€€€€€É½½µ}¥Ñ•´¹Í•Ñ…Ñ„¡EÐ¹%Ñ•µ…Ñ…I½±”¹UÍ•ÉI½±”°É½½´¤(€€€€€€€€€€€Í•±˜¹É½½µÌ¹…‘‘%Ñ•´¡É½½µ}¥Ñ•´¤(€€€€€€€¥˜Í•±˜¹É½½µÌ¹½Õ¹Ð ¤€ôô€Àè(€€€€€€€€€€€•µÁÑå}É½½´€ôE1¥ÍÑ]¥‘•Ñ%Ñ•´ ‹¢þcšÊ‡šr'žž’êë¢«’æƒ–º“¾òo–"o–îë–B;–>¿š*+š"ÿ¦^Óž‚–>GžîgšB·–¶CŽˆ¤(€€€€€€€€€€€•µÁÑå}É½½´¹Í•Ñ±…Ì¡EÐ¹%Ñ•µ±…œ¹9½%Ñ•µ±…Ì¤ìÍ•±˜¹É½½µÌ¹…‘‘%Ñ•´¡•µÁÑå}É½½´¤(€€€€€€€É½½µ}Á•½Á±”€ô±¥ÍÐ¡Í•±˜¹‘…Ñ„¹•Ð ‰É½½µ}Á•½Á±”ˆ¤½Èmt¤(€€€€€€€Í•±˜¹}É•¹‘•É}É½½µ}Á•½Á±”¡É½½µ}Á•½Á±”¤(€€€€€€€½…°€ôÍ•±˜¹‘…Ñ„¹•Ð ‰É½½µ}½…°ˆ¤½Èíô(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡½…°°‘¥Ð¤…¹½…°è(€€€€€€€€€€€Ñ…É•Ð€ô¥¹Ð¡½…°¹•Ð ‰Ñ…É•Ñ}Í•½¹‘Ìˆ¤½È½…°¹•Ð ‰Ñ…É•Ñ}µ¥¹ÕÑ•Ìˆ°€À¤€¨€ØÀ¤(€€€€€€€€€€€ÕÉÉ•¹Ð€ô¥¹Ð¡½…°¹•Ð ‰½µÁ±•Ñ•‘}Í•½¹‘Ìˆ¤½È½…°¹•Ð ‰ÕÉÉ•¹Ñ}Í•½¹‘Ìˆ°€À¤¤(€€€€€€€€€€€Í•±˜¹É½½µ}½…°¹Í•ÑQ•áÐ (€€€€€€€€€€€€€€€˜‹–Ç–B3žn»š‚¾òií™½Éµ…Ñ}Ý½É­}‘ÕÉ…Ñ¥½¸¡ÕÉÉ•¹Ð¥ô€¼í™½Éµ…Ñ}Ý½É­}‘ÕÉ…Ñ¥½¸¡Ñ…É•Ð¥ôˆ(€€€€€€€€€€€€€€€¥˜Ñ…É•Ð•±Í”˜‹–Ç–B3žn»š‚¾òií½…°¹•Ð Ñ¥Ñ±”œ¤½È€Ÿ’â¢Öß’âOšÎ ôˆ(€€€€€€€€€€€€¤(€€€€€€€•±¥˜¡…Í…ÑÑÈ¡Í•±˜°€‰É½½µ}½…°ˆ¤è(€€€€€€€€€€€Í•±˜¹É½½µ}½…°¹Í•ÑQ•áÐ ‹–Âkšr«¢ºûžö»–Ç–B3žn»š‚¾òo–"o–îëš"ÿ¦^Ó–B;–>¿’î—žRÄM½¥…°A$ƒš>C’úožn»š‚šVÃš6»Žˆ¤(€€€€€€€Í•±˜¹}É•¹‘•É}É½½µ}…Ñ¥Ù¥Ñä¡±¥ÍÐ¡Í•±˜¹‘…Ñ„¹•Ð ‰É½½µ}…Ñ¥Ù¥Ñäˆ¤½ÈÍ•±˜¹‘…Ñ„¹•Ð ‰…Ñ¥Ù¥Ñäˆ¤½Èmt¤¤(€€€€€€€…Ñ¥Ù”õÍ•±˜¹‘…Ñ„¹•Ð ‰…Ñ¥Ù•}Ù¥Í¥ÑÌˆ¤½Èmt(€€€€€€€¥˜…Ñ¥Ù”èÍ•±˜¹…Ñ¥Ù•}Ù¥Í¥Ð¹•µ¥Ð¡…Ñ¥Ù•lÁt¤(€€€€€€€Í•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹–ÞË–"ßšZÃ¾ò3¦†×¦v‹––ºçšb¿šršZÃžjŽˆ¤((€€€‘•˜}Í…Ù•}ÁÉ½™¥±”¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£’þw–¶c¦jCžž¢ºûžö»Š˜ˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€µ”õÍ•±˜¹‘…Ñ„¹•Ð ‰µ”ˆ¤½ÈíôìÍ•±˜¹±¥•¹Ð¹ÕÁ‘…Ñ•}ÁÉ½™¥±”¡¹¥­¹…µ”õÍÑÈ¡µ”¹•Ð ‰¹¥­¹…µ”ˆ¤½È€‹–·š¾ošB·–¶@ˆ¤±Ù¥Í¥‰¥±¥Ñäô‰¡¥‘‘•¸ˆ¥˜Í•±˜¹¡¥‘‘•¸¹¥Í¡•­• ¤•±Í”€‰™É¥•¹‘Ìˆ±Í¡½Ý}•á…Ñ}Ñ¥µ”õÍ•±˜¹•á…Ð¹¥Í¡•­• ¤±…±±½Ý}Ù¥Í¥ÑÌõÍ•±˜¹Ù¥Í¥ÑÍ}…±±½Ý•¹¥Í¡•­• ¤±½ÕÑ™¥Ñ}­•äõÍ•±˜¹½ÕÑ™¥Ñ}­•ä¤ìÍ•±˜¹É•™É•Í  ¤(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤(€€€‘•˜}…‘‘}‰Õ‘‘ä¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€½‘”±½¬õE%¹ÁÕÑ¥…±½œ¹•ÑQ•áÐ¡Í•±˜°‹šÞï–*ƒšB·–¶@ˆ°‹¢úO–—–¾çšZçžj€àƒ’ö7šB·–¶Cž‚¾òhˆ¤(€€€€€€€¥˜½¬…¹½‘”è(€€€€€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£–>G¦šB·–¶CžRÏ¢¾ßŠ˜ˆ¤(€€€€€€€€€€€ÑÉäèÍ•±˜¹±¥•¹Ð¹ÉÁŒ ‰±¥±¥}…‘‘}‰Õ‘‘å}‰å}½‘”ˆ±ì‰½‘”ˆé½‘•ô¤ìÍ•±˜¹É•™É•Í  ¤ìÍ•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹šB·–¶CžRÏ¢¾ß–ÞË–>G¦Žˆ¤(€€€€€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤(€€€‘•˜}Í•¹‘}Ù¥Í¥Ð¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€¥Ñ•´õÍ•±˜¹‰Õ‘‘¥•Ì¹ÕÉÉ•¹Ñ%Ñ•´ ¤(€€€€€€€¥˜¹½Ð¥Ñ•´èÉ•ÑÕÉ¸Í•±˜¹}•ÉÉ½È¡M½¥…±ÉÉ½È ‹¢¾ß–#¦'š.§’â’ö7šB·–¶CŽˆ¤¤(€€€€€€€‰Õ‘‘ä€ô¥Ñ•´¹‘…Ñ„¡EÐ¹%Ñ•µ…Ñ…I½±”¹UÍ•ÉI½±”¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡‰Õ‘‘ä°‘¥Ð¤èÉ•ÑÕÉ¸Í•±˜¹}•ÉÉ½È¡M½¥…±ÉÉ½È ‹¢¾ß–#¦'š.§’â’ö7šB·–¶CŽˆ¤¤(€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹–·š¾oš¶–r£––’–ë–>GŠ˜ˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Í•±˜¹±¥•¹Ð¹ÉÁŒ ‰±¥±¥}Í•¹‘}Ù¥Í¥Ðˆ±ì‰Ñ…É•Ðˆé‰Õ‘‘ål‰ÕÍ•É}¥‰t°‰Ù¥Í¥Ñ}­¥¹ˆè‰Ù¥Í¥Ð‰ô¤ìÍ•±˜¹}•¹‘}…Ñ¥½¸ ¤ìÍ•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹–·š¾o–ÞËžî?–ë–>G¾ò3ž¶'–ú–¾çšZçš:—–>_’âË¦^£Žˆ¤ìE5•ÍÍ…•	½à¹¥¹™½Éµ…Ñ¥½¸¡Í•±˜°‹–ÞË–ë–>Dˆ°‹–·š¾o–ÞËžî?–ë–>G¾ò3ž¶'–ú–¾çšZçš:—–>_’âË¦^£Žˆ¤(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤(€€€‘•˜}…•ÁÑ}¥¹‰½à¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€¥Ñ•´õÍ•±˜¹¥¹‰½à¹ÕÉÉ•¹Ñ%Ñ•´ ¤(€€€€€€€¥˜¹½Ð¥Ñ•´èÉ•ÑÕÉ¸Í•±˜¹}•ÉÉ½È¡M½¥…±ÉÉ½È ‹¢¾ß–#¦'š.§’â¦†çžRÏ¢¾ßš"[’âË¦^£Žˆ¤¤(€€€€€€€­¥¹±‘…Ñ„õ¥Ñ•´¹‘…Ñ„¡EÐ¹%Ñ•µ…Ñ…I½±”¹UÍ•ÉI½±”¤(€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£–’žB¦'’â·žjžRÏ¢¾ßŠ˜ˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜­¥¹ôô‰‰Õ‘‘äˆèÍ•±˜¹±¥•¹Ð¹ÉÁŒ ‰±¥±¥}É•ÍÁ½¹‘}‰Õ‘‘äˆ±ì‰É•ÅÕ•ÍÑ}¥ˆé‘…Ñ…l‰¥‰t°‰…•ÁÐˆéQÉÕ•ô¤(€€€€€€€€€€€•±Í”èÍ•±˜¹±¥•¹Ð¹ÉÁŒ ‰±¥±¥}É•ÍÁ½¹‘}Ù¥Í¥Ðˆ±ì‰•Ù•¹Ñ}¥ˆé‘…Ñ…l‰¥‰t°‰…•ÁÐˆéQÉÕ•ô¤(€€€€€€€€€€€Í•±˜¹É•™É•Í  ¤(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤(€€€‘•˜}É•…Ñ•}É½½´¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€¹…µ”±½¬õE%¹ÁÕÑ¥…±½œ¹•ÑQ•áÐ¡Í•±˜°‹–"o–îë¢«’æƒ–ºˆ°‹¢«’æƒ–º“–B7žžÃ¾òhˆ±Ñ•áÐô‹–º'¦vg–Þ—’ös¦^Ðˆ¤(€€€€€€€¥˜½¬…¹¹…µ”è(€€€€€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£–"o–îë¢«’æƒ–º“Š˜ˆ¤(€€€€€€€€€€€ÑÉäèÍ•±˜¹±¥•¹Ð¹ÉÁŒ ‰±¥±¥}É•…Ñ•}É½½´ˆ±ì‰É½½µ}¹…µ”ˆé¹…µ•ô¤ìÍ•±˜¹É•™É•Í  ¤ìÍ•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹¢«’æƒ–º“–ÞË–"o–îë¾ò3–>¿’î—–"’ê¯š"ÿ¦^Óž‚’êŽˆ¤(€€€€€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤(€€€‘•˜}©½¥¹}É½½´¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹}É•ÅÕ¥É•}±½¥¸ ¤èÉ•ÑÕÉ¸(€€€€€€€½‘”±½¬õE%¹ÁÕÑ¥…±½œ¹•ÑQ•áÐ¡Í•±˜°‹–*ƒ–—¢«’æƒ–ºˆ°‹¢úO–”€àƒ’ö7š"ÿ¦^Óž‚¾òhˆ¤(€€€€€€€¥˜½¬…¹½‘”è(€€€€€€€€€€€Í•±˜¹}‰•¥¹}…Ñ¥½¸ ‹š¶–r£–*ƒ–—¢«’æƒ–º“Š˜ˆ¤(€€€€€€€€€€€ÑÉäèÍ•±˜¹±¥•¹Ð¹ÉÁŒ ‰±¥±¥}©½¥¹}É½½´ˆ±ì‰½‘”ˆé½‘•ô¤ìÍ•±˜¹É•™É•Í  ¤ìÍ•±˜¹}Í•Ñ}ÍÑ…ÑÕÌ ‹–ÞË–*ƒ–—¢«’æƒ–º“Žˆ¤(€€€€€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒèÍ•±˜¹}•ÉÉ½È¡•áŒ¤