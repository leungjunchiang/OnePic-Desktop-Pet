"""æ­å­è‡ªä¹ å®¤ç•Œé¢ã€åŽå°åŒæ­¥çº¿ç¨‹å’ŒåŒå…­æ¯›æœ¬åœ°ä¸²é—¨çª—å£ã€‚"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabWidget,
    QVBoxLayout, QWidget, QSizePolicy,
)

from .resources import resource_path
from .accessories import SPECIAL_OUTFIT_SPRITES
from .social import SocialClient, SocialError
from .work_timer import format_work_duration


def _presence_working(presence: dict[str, Any]) -> bool:
    """Read both the legacy boolean and the new explicit presence status.

    Older dashboard functions only returned ``working`` while the repaired
    function also returns ``status``.  Keeping this normalization in the UI
    prevents a mixed-version pair of clients from showing a false rest state.
    """

    status = str(presence.get("status") or "").strip().casefold()
    if status in {"focus", "working", "ä¸“æ³¨", "å·¥ä½œ", "ä¸“æ³¨ä¸­", "æ­£åœ¨å·¥ä½œ"}:
        return True
    if status in {"rest", "idle", "offline", "ä¼‘æ¯", "ä¼‘æ¯ä¸­", "ç¦»çº¿"}:
        return False
    value = presence.get("working")
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "focus", "working", "ä¸“æ³¨", "å·¥ä½œ", "ä¸“æ³¨ä¸­", "æ­£åœ¨å·¥ä½œ"}
    return bool(value)


def _presence_status(presence: dict[str, Any]) -> str:
    """Return a stable user-facing status for old and new API payloads."""

    status = str(presence.get("status") or "").strip().casefold()
    if status in {"offline", "ç¦»çº¿"}:
        return "offline"
    if _presence_working(presence):
        return "focus"
    return "rest"


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
            room_id = self.presence.get("room_id")
            try:
                data = self.client.dashboard(room_id=room_id)
            except TypeError:
                # Keep third-party/test backends compatible while they adopt
                # the room-scoped dashboard argument.
                data = self.client.dashboard()
            self.completed.emit(data)
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.presence.get("room_id")) if callable(cached_loader) else None
            if cached is not None:
                self.completed.emit(cached)
            else:
                self.failed.emit(str(exc))


class SocialDashboardThread(QThread):
    """Fetch one dashboard without blocking the Qt GUI thread."""

    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, client: SocialClient, room_id: str | None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.room_id = room_id

    def run(self) -> None:
        try:
            try:
                data = self.client.dashboard(room_id=self.room_id)
            except TypeError:
                # Keep small offline/test backends compatible with the room
                # scoped dashboard while the real request stays off the GUI.
                data = self.client.dashboard()
            self.completed.emit(dict(data or {}))
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.room_id) if callable(cached_loader) else None
            if cached is not None:
                self.completed.emit(cached)
            else:
                self.failed.emit(str(exc))


class SocialEventThread(QThread):
    """Send a room event without freezing pet animation or the study window."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, client: SocialClient, event: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.event = event

    def run(self) -> None:
        try:
            self.client.record_room_event(**self.event)
            self.completed.emit()
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class BuddyCardWidget(QWidget):
    """æŠŠæ­å­çš„åœ¨çº¿ã€å·¥ä½œå’Œä»Šæ—¥æ—¶é•¿æ˜¾ç¤ºæˆä¸€çœ¼èƒ½çœ‹æ¸…çš„å¡ç‰‡ã€‚"""

    interaction_requested = Signal(dict, str)
    interaction_blocked = Signal(str)
    subscription_requested = Signal(dict, bool)

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self._cooldown_seconds = 15
        self._cooldown_until: dict[str, float] = {}
        self._buttons: dict[str, QPushButton] = {}
        self.setObjectName("buddyCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)
        online = bool(buddy.get("online"))
        working = _presence_working(buddy)
        nickname = str(buddy.get("nickname") or "æ­å­")
        is_self = bool(buddy.get("is_self"))
        status = _presence_status(buddy)
        status_text = {"focus": "æ­£åœ¨å·¥ä½œ", "rest": "æ­£åœ¨ä¼‘æ¯", "offline": "å·²ç¦»çº¿"}[status]
        headline = QLabel(
            f"{'ðŸŸ¢' if online else 'âšª'}  {nickname} çš„å…­æ¯›"
            f"{status_text}{'ï¼ˆæˆ‘ï¼‰' if is_self else ''}"
        )
        headline.setWordWrap(True)
        headline.setStyleSheet("font-size:15px;font-weight:600;color:#203847;")
        root.addWidget(headline)
        duration = buddy.get("today_seconds")
        time_text = "ä»Šæ—¥ä¸“æ³¨æ—¶é•¿å·²éšè—" if duration is None else f"å·²ä¸“æ³¨ {format_work_duration(duration)}"
        session_seconds = buddy.get("session_seconds")
        if session_seconds is not None and status == "focus":
            time_text = f"æœ¬è½®ä¸“æ³¨ {format_work_duration(session_seconds)}ã€€Â·ã€€{time_text}"
        focus = QLabel(time_text)
        focus.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        root.addWidget(focus)
        outfit = str(buddy.get("outfit_key") or "ç»å…¸å…­æ¯›")
        footer = QLabel(f"å½“å‰å¨ƒè¡£ï¼š{outfit}ã€€Â·ã€€åŒå‡»æˆ–é€‰ä¸­åŽå¯æ´¾å…­æ¯›ä¸²é—¨")
        footer.setStyleSheet("color:#61727d;font-size:11px;")
        footer.setWordWrap(True)
        root.addWidget(footer)
        actions = QHBoxLayout()
        for kind, label in (("poke", "æˆ³ä¸€ä¸‹"), ("cheer", "åŠ æ²¹"), ("drink", "é€’å¥¶èŒ¶")):
            button = QPushButton(label)
            button.setMinimumHeight(32)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.clicked.connect(lambda _checked=False, action=kind: self._request_interaction(action))
            if is_self:
                button.setEnabled(False)
                button.setToolTip("äº’åŠ¨æŒ‰é’®åªå¯¹æˆ¿é—´é‡Œçš„å…¶ä»–æ­å­å¼€æ”¾")
            self._buttons[kind] = button
            actions.addWidget(button)
        root.addLayout(actions)
        if not is_self:
            subscribe = QCheckBox("è®¢é˜…å¼€å·¥/ä¸‹ç­æé†’")
            subscribe.setChecked(bool(buddy.get("subscribed")))
            subscribe.stateChanged.connect(lambda state: self.subscription_requested.emit(self.buddy, bool(state)))
            root.addWidget(subscribe)

    def _request_interaction(self, kind: str) -> None:
        now = time.monotonic()
        remaining = self._cooldown_until.get(kind, 0.0) - now
        if remaining > 0:
            self.interaction_blocked.emit(f"äº’åŠ¨å†·å´ä¸­ï¼Œè¯· {int(remaining) + 1} ç§’åŽå†è¯•ã€‚")
            return
        self._cooldown_until[kind] = now + self._cooldown_seconds
        button = self._buttons.get(kind)
        if button is not None:
            button.setEnabled(False)
            button.setText(f"å·²å‘é€ ({self._cooldown_seconds}s)")
            QTimer.singleShot(self._cooldown_seconds * 1000, lambda: self._restore_button(kind))
        self.interaction_requested.emit(self.buddy, kind)

    def _restore_button(self, kind: str) -> None:
        button = self._buttons.get(kind)
        if button is None:
            return
        labels = {"poke": "æˆ³ä¸€ä¸‹", "cheer": "åŠ æ²¹", "drink": "é€’å¥¶èŒ¶"}
        button.setText(labels.get(kind, "äº’åŠ¨"))
        if not bool(self.buddy.get("is_self")):
            button.setEnabled(True)


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
        # ç¬¬ä¸€å¹•å±•ç¤ºåŒæ–¹×;âÚ$z{-®éÜj×FF–W2ævWB‡7G"†'VFG’ævWB‚'W6W%ö–B"’’¢–b&Wf–÷W2—2æ÷BæöæRæB&ööÂ‡&Wf–÷W2ævWB‚'v÷&¶–ær"’’Ò&ööÂ†'VFG’ævWB‚'v÷&¶–ær"’“ ¢7FFU÷FW‡BÒ.[ÈZx¾K‰>k:‚"–b'VFG’ævWB‚'v÷&¶–ær"’VÇ6R.{¹>iÙþK‰>k:‚ ¢6VÆbæ'VFG•÷7V'67&—F–öåöæ÷F–6RæVÖ—B†b'¶'VFG’ævWB‚væ–6¶æÖRrÂ~i
ÞZÙr—Ò·7FFU÷FW‡GÞK¨n8""¢v÷&¶–æuö6÷VçB³Ò–çB†&ööÂ†'VFG’ævWB‚'v÷&¶–ær"’’¢GW&F–öâÒ'VFG’ævWB‚'FöF•÷6V6öæG2"¢–bGW&F–öâ—2æ÷BæöæS¢f—6–&ÆU÷F÷FÂ³ÒÖ‚ƒÂ–çB†GW&F–öâ’¢—FVÓÕÆ—7Ev–FvWD—FVÒ‚“²—FVÒç6WDFF…Bä—FVÔFF&öÆRåW6W%&öÆRÆ'VFG’“²6VÆbæ'VFF–W2æFD—FVÒ†—FVÒ¢'VFG•÷v–FvWBÒ'VFG”6&Ev–FvWB†'VFG’Â6VÆbæ'VFF–W2¢'VFG•÷v–FvWBæ–çFW&7F–öå÷&WVW7FVBæ6öææV7B‡6VÆbå÷6VæEö–çFW&7F–öâ¢'VFG•÷v–FvWBæ–çFW&7F–öåö&Æö6¶VBæ6öææV7B†ÆÖ&FÖW76vS¢6VÆbå÷6WE÷7FGW2†ÖW76vRÂW'&÷#ÕG'VR’¢'VFG•÷v–FvWBç7V'67&—F–öå÷&WVW7FVBæ6öææV7B‡6VÆbå÷6WE÷7V'67&—F–öâ¢6VÆbæ'VFF–W2ç6WD—FVÕv–FvWB†—FVÒÂ'VFG•÷v–FvWB¢6VÆbå÷6WEö'VFG•ö—FVÕö†V–v‡B†—FVÒÂ'VFG•÷v–FvWB¢ÖU÷6V6öæG2Ò–çB†ÖU÷&W6Væ6RævWB‚'FöF•÷6V6öæG2"’÷"ÖRævWB‚'FöF•÷6V6öæG2"’÷"¢6VÆbç7GVG•÷7VÖÖ'’ç6WEFW‡B€¢b.xëYÊ‚·v÷&¶–æuö6÷VçGÒKØÞi
ÞZÙjÚ>YÊŽK‰>k:Ž8+~8 ¢b.h‰y¨NK¸®iz^K‰>k:‚¶f÷&ÖE÷v÷&µöGW&F–öâ†ÖU÷6V6öæG2—Þ8+~8 ¢b.h‹þ™{NXúþŠxYŽŠê¶f÷&ÖE÷v÷&µöGW&F–öâ‡f—6–&ÆU÷F÷FÂ—Ò ¢¢–bæ÷B6VVã ¢V×G’ÒÆ—7Ev–FvWD—FVÒ‚.‹ùŽk*iÈži
ÞZÙ8.x+žX{¾Kˆ¾ikž(	ÎyJŽi
ÞZÙzk{¾Xª(	ÞûÈÎKˆ‹[~[z^KÙÎi{n‹ùž˜xÎKÉ®i‹îzK®kˆ^jY®y¨NK‰>k:Ži{n™[þ8""¢V×G’ç6WDfÆw2…Bä—FVÔfÆräæô—FVÔfÆw2“²6VÆbæ'VFF–W2æFD—FVÒ†V×G’¢6VÆbåöf—EöÆ—7Eö†V–v‡B‡6VÆbæ'VFF–W2ÂCbÂ3c¢6VÆbæ–æ&÷‚æ6ÆV"‚¢f÷"&WVW7B–â6VÆbæFFævWB‚'&WVW7G2"’÷"µÓ ¢—FVÓÕÆ—7Ev–FvWD—FVÒ†b.i
ÞZÙyK>Šû~ûÉ§·&WVW7BævWB‚væ–6¶æÖRr—Ò"“²—FVÒç6WDFF…Bä—FVÔFF&öÆRåW6W%&öÆRÂ‚&'VFG’"Ç&WVW7B’“²6VÆbæ–æ&÷‚æFD—FVÒ†—FVÒ¢f÷"f—6—B–â6VÆbæFFævWB‚'f—6—G2"’÷"µÓ ¢—FVÓÕÆ—7Ev–FvWD—FVÒ†b.K‹.™zŽ˜(Šû~ûÉ§·f—6—BævWB‚væ–6¶æÖRr—Ò"“²—FVÒç6WDFF…Bä—FVÔFF&öÆRåW6W%&öÆRÂ‚'f—6—B"Çf—6—B’“²6VÆbæ–æ&÷‚æFD—FVÒ†—FVÒ¢–b6VÆbæ–æ&÷‚æ6÷VçB‚’ÓÒ ¢V×G’ÒÆ—7Ev–FvWD—FVÒ‚.[Ù>X˜Þk*iÈž[è^ZHNynyK>Šû~h‰nK‹.™zŽûÈÎiky¨N˜(Šû~KÉ®i‹îzK®YÊŽ‹ùž˜xÎ8""¢V×G’ç6WDfÆw2…Bä—FVÔfÆräæô—FVÔfÆw2“²6VÆbæ–æ&÷‚æFD—FVÒ†V×G’¢&öö×2ÒÆ—7B‡6VÆbæFFævWB‚'&öö×2"’÷"µÒ¢&Wf–÷W5÷&ööÕö–BÒ6VÆbæ7W'&VçE÷&ööÕö–@¢6VÆbåöÇ––æuöF6†&ö&BÒG'VP¢2&V'V–ÆF–ærF†RÆ—7B—2â–çFW&æÂ&VæFW"÷W&F–öââ7W&W72F†P¢2G&ç6–VçB'6VÆV7F–öâ6ÆV&VB"æB'6VÆV7F–öâ&W7F÷&VB"6–væÇ3°¢2÷F†W'v—6RV6‚F6†&ö&B&W7öç6R66†VGVÆW2æ÷F†W"æWGv÷&²7–æ2à¢6VÆbç&öö×2æ&Æö6µ6–væÇ2…G'VR¢6VÆbç&öö×2æ6ÆV"‚¢f÷"&ööÒ–â&öö×3 ¢&ööÕö—FVÒÒÆ—7Ev–FvWD—FVÒ€¢b'·&ööÒævWB‚væÖRr—Ò+r·&ööÒævWB‚vÖVÖ&W'2r—ÒK«¢+rh‹þ™{Nz·&ööÒævWB‚v–çf—FUö6öFRr—Ò ¢¢&ööÕö—FVÒç6WDFF…Bä—FVÔFF&öÆRåW6W%&öÆRÂ&ööÒ¢6VÆbç&öö×2æFD—FVÒ‡&ööÕö—FVÒ¢–b6VÆbç&öö×2æ6÷VçB‚’ÓÒ ¢V×G•÷&ööÒÒÆ—7Ev–FvWD—FVÒ‚.‹ùŽk*iÈžzxK«®ˆz®KšZêNûÉ¾X‰¾[»®YîXúþh¨®h‹þ™{NzXù{¹ži
ÞZÙ8""¢V×G•÷&ööÒç6WDfÆw2…Bä—FVÔfÆräæô—FVÔfÆw2“²6VÆbç&öö×2æFD—FVÒ†V×G•÷&ööÒ¢6VÆbæ7W'&VçE÷&ööÕö–BÒæöæP¢VÇ6S ¢26öÖ&òôÆ—7Bv–FvWG2Fòæ÷B6öç6—7FVçFÇ’6VÆV7BF†Rf—'7B—FVÐ¢2gFW"6ÆV"‚’7&÷72BÆFf÷&×2âv—F†÷WB6VÆV7FVB&ööÐ¢2F†RæW‡B†V'F&VBW6VBFò6VæB&ööÕö–CÔåTÄÂÂ6òF†R6W'fW"†@¢2æò&VÆ–&ÆRv’Fò76ö6–FRF†—2W6W"w2fö7W2v—F‚&ööÒà¢6VÆV7FVBÒÓ¢f÷"–æFW‚Â&ööÒ–âVçVÖW&FR‡&öö×2“ ¢&ööÕö–BÒ6VÆbå÷&ööÕö–Eög&öÕ÷–ÆöB‡&ööÒ¢–b&ööÕö–BæB&ööÕö–BÓÒ&Wf–÷W5÷&ööÕö–C ¢6VÆV7FVBÒ–æFW€¢'&V°¢–b6VÆV7FVBÂ ¢2öâf—'7B÷Vâ&VfW"F†R&ööÒv—F‚F†RÖ÷7BÖVÖ&W'2âF†—0¢2fö–G26–ÆVçFÇ’ÆæF–ær–ââöÆBöæR×W'6öâ&ööÒv†VâF†P¢2W6W"†2§W7B¦ö–æVB6†&VBv÷&·&ööÒà¢6VÆV7FVBÒÖ‚€¢&ævR†ÆVâ‡&öö×2’’À¢¶W“ÖÆÖ&F–æFWƒ¢–çB‡&öö×5¶–æFW…ÒævWB‚&ÖVÖ&W'2"’÷"’À¢¢6VÆbç&öö×2ç6WD7W'&VçE&÷r‡6VÆV7FVB¢6VÆV7FVE÷&ööÒÒ&öö×5·6VÆV7FVEÐ¢6VÆbæ7W'&VçE÷&ööÕö–BÒ6VÆbå÷&ööÕö–Eög&öÕ÷–ÆöB‡6VÆV7FVE÷&ööÒ¢6VÆbç&öö×2æ&Æö6µ6–væÇ2„fÇ6R¢6VÆbåöf—EöÆ—7Eö†V–v‡B‡6VÆbç&öö×2ÂS"ÂC¢6VÆbåöÇ––æuöF6†&ö&BÒfÇ6P¢–b6VÆbæ7W'&VçE÷&ööÕö–BÒ&Wf–÷W5÷&ööÕö–C ¢6VÆbç&ööÕö6†ævVBæVÖ—B‡6VÆbæ7W'&VçE÷&ööÕö–B¢2F†R&ööÒ×66÷VBVæGö–çB—2WF†÷&—FF—fRf÷"ÖVÖ&W'2æBWfVçG2à¢2¶VWF†RÆVv7’F÷ÖÆWfVÂf–VÆG226ö×F–&–Æ—G’fÆÆ&6²f÷ ¢2öÆFW"&÷‡’FWÆ÷–ÖVçG2æBF†RöffÆ–æRT’FW7G2à¢&ööÕöFWF–ÂÒ6VÆbæFFævWB‚&7W'&VçE÷&ööÒ"’÷"·Ð¢–bæ÷B—6–ç7Fæ6R‡&ööÕöFWF–ÂÂF–7B“ ¢&ööÕöFWF–ÂÒ·Ð¢–b6VÆbæ7W'&VçE÷&ööÕö–BæB6VÆbæ7W'&VçE÷&ööÕö–BÒ&Wf–÷W5÷&ööÕö–BæBæ÷B&ööÕöFWF–Ã ¢6VÆbå÷&ööÕ÷&Vg&W6…÷F–ÖW"ç7F'Bƒ¢&ööÕ÷V÷ÆRÒÆ—7B‡&ööÕöFWF–ÂævWB‚'&ööÕ÷V÷ÆR"’÷"6VÆbæFFævWB‚'&ööÕ÷V÷ÆR"’÷"µÒ’–b6VÆbæ7W'&VçE÷&ööÕö–BVÇ6RµÐ¢2Çv—2&VæFW"F†RÆö6ÂÖVÖ&W"2vVÆÂâF†RöÆB5ÂgVæ7F–öâöæÇ¢2&WGW&æVBVW'2Âv†–6‚ÖFRF†R&ööÒÆöö²Æ–¶RWfW'–&öG’v2&W7F–æp¢2v†VâF†RÆö6ÂF–ÖW"v2F†RöæÇ’7FFRf—6–&ÆR–âF†RT’à¢Æö6Å÷7FGW2Ò6VÆbåöfö7W5÷6æ6†÷@¢Æö6Å÷&W6Væ6RÒF–7B†ÖU÷&W6Væ6R¢–b—6–ç7Fæ6R†Æö6Å÷7FGW2ÂF–7B“ ¢Æö6Å÷&W6Væ6RÒ²¢¦Æö6Å÷&W6Væ6RÂ¢¦Æö6Å÷7FGW7Ð¢VÆ–bÆö6Å÷7FGW2—2æ÷BæöæS ¢Æö6Å÷&W6Væ6RÒ°¢¢¦Æö6Å÷&W6Væ6RÀ¢'7FGW2#¢vWFGG"†Æö6Å÷7FGW2Â'7FGW2"Â&–FÆR"’À¢'v÷&¶–ær#¢&ööÂ†vWFGG"†Æö6Å÷7FGW2Â&—5÷'Vææ–ær"ÂfÇ6R’’À¢'6W76–öå÷6V6öæG2#¢–çB†vWFGG"†Æö6Å÷7FGW2Â'6W76–öå÷6V6öæG2"Â’’À¢'FöF•÷6V6öæG2#¢–çB†vWFGG"†Æö6Å÷7FGW2Â'FöF•÷6V6öæG2"Â’’À¢Ð¢Æö6Å÷&W6Væ6RçWFFR‡°¢'W6W%ö–B#¢7G"†ÖRævWB‚'W6W%ö–B"’÷"ÖRævWB‚&–B"’÷"&ÖR"’À¢&æ–6¶æÖR#¢7G"†ÖRævWB‚&æ–6¶æÖR"’÷".h‰"’À¢&÷WFf—Eö¶W’#¢7G"†ÖU÷&W6Væ6RævWB‚&÷WFf—Eö¶W’"’÷"6VÆbæ÷WFf—Eö¶W’÷"ÖRævWB‚&÷WFf—Eö¶W’"’÷"""’À¢&öæÆ–æR#¢G'VRÀ¢&—5÷6VÆb#¢G'VRÀ¢Ò¢–b6VÆbæ7W'&VçE÷&ööÕö–C ¢&ööÕ÷V÷ÆRÒ¶Æö6Å÷&W6Væ6UÒ²·f÷"–â&ööÕ÷V÷ÆR–b7G"‡ævWB‚'W6W%ö–B"’’Ò7G"†Æö6Å÷&W6Væ6RævWB‚'W6W%ö–B"’•Ð¢VÇ6S ¢&ööÕ÷V÷ÆRÒµÐ¢6VÆbå÷&VæFW%÷&ööÕ÷V÷ÆR‡&ööÕ÷V÷ÆR¢vöÂÒ&ööÕöFWF–ÂævWB‚'&ööÕövöÂ"’÷"6VÆbæFFævWB‚'&ööÕövöÂ"’÷"·Ð¢7VÖÖ'’Ò&ööÕöFWF–ÂævWB‚'&ööÕ÷7VÖÖ'’"’÷"6VÆbæFFævWB‚'&ööÕ÷7VÖÖ'’"’÷"·Ð¢–b—6–ç7Fæ6R‡7VÖÖ'’ÂF–7B’æB7VÖÖ'“ ¢6VÆbç&ööÕ÷7VÖÖ'’ç6WEFW‡B€¢b.iÊÎh‹þ™{B¶–çB‡7VÖÖ'’ævWB‚vÖVÖ&W%ö6÷VçBr’÷"ÆVâ‡&ööÕ÷V÷ÆR’—ÒK«¢+r ¢b'¶–çB‡7VÖÖ'’ævWB‚vfö7W5ö6÷VçBr’÷"—ÒK«®jÚ>YÊŽK‰>k:‚+r ¢b.X[YÎK‰>k:‚¶f÷&ÖE÷v÷&µöGW&F–öâ†–çB‡7VÖÖ'’ævWB‚w6†&VEöfö7W5÷6V6öæG2r’÷"’—Ò ¢¢VÆ–b†6GG"‡6VÆbÂ'&ööÕ÷7VÖÖ'’"“ ¢6VÆbç&ööÕ÷7VÖÖ'’ç6WEFW‡B‚.KÚ[Ù>X˜Þk*iÈžXªXZ^[z^KÙÎ™{N8.X‰¾[»®[z^KÙÎ™{Nh‰n‹é>XZ^h‹þ™{NzXªXZ^YîûÈÎ‹ùž˜xÎh˜ÞKÉ®i‹îzK®X[YÎx«nh8""¢6VÆbå÷&ööÕövöÅ÷7FFRÒF–7B†vöÂ’–b—6–ç7Fæ6R†vöÂÂF–7B’VÇ6R·Ð¢66†VGVÆRÒ&ööÕöFWF–ÂævWB‚'&ööÕ÷66†VGVÆR"’÷"6VÆbæFFævWB‚'&ööÕ÷66†VGVÆR"’÷"·Ð¢6†ÆÆVævRÒ&ööÕöFWF–ÂævWB‚'&ööÕö6†ÆÆVævR"’÷"6VÆbæFFævWB‚'&ööÕö6†ÆÆVævR"’÷"·Ð¢6VÆbå÷&ööÕ÷66†VGVÆU÷7FFRÒF–7B‡66†VGVÆR’–b—6–ç7Fæ6R‡66†VGVÆRÂF–7B’VÇ6R·Ð¢6VÆbå÷&ööÕö6†ÆÆVævU÷7FFRÒF–7B†6†ÆÆVævR’–b—6–ç7Fæ6R†6†ÆÆVævRÂF–7B’VÇ6R·Ð¢6VÆbç&ööÕövöÅö'WGFöâç6WDVæ&ÆVB†&ööÂ‡6VÆbæ7W'&VçE÷&ööÕö–B’¢–b†6GG"‡6VÆbÂ'&ööÕ÷66†VGVÆUö'WGFöâ"“ ¢6VÆbç&ööÕ÷66†VGVÆUö'WGFöâç6WDVæ&ÆVB†&ööÂ‡6VÆbæ7W'&VçE÷&ööÕö–B’¢–b†6GG"‡6VÆbÂ'&ööÕö6†ÆÆVævUö'WGFöâ"“ ¢6VÆbç&ööÕö6†ÆÆVævUö'WGFöâç6WDVæ&ÆVB†&ööÂ‡6VÆbæ7W'&VçE÷&ööÕö–B’¢6VÆbç&ööÕöÆVfUö'WGFöâç6WDVæ&ÆVB†&ööÂ‡6VÆbæ7W'&VçE÷&ööÕö–B’¢6VÆbå÷&Vg&W6…÷&ööÕövöÅ÷FW‡B‚¢–b†6GG"‡6VÆbÂ'&ööÕ÷&—GVÂ"“ ¢–b6VÆbå÷&ööÕ÷66†VGVÆU÷7FFS ¢6VÆbç&ööÕ÷&—GVÂç6WEFW‡B€¢b.X[YÎ[È[zRþiKn[z^ûÉ§·6VÆbå÷&ööÕ÷66†VGVÆU÷7FFRævWB‚w7F'EöBrÂrÒÓ¢ÒÒr—Ò[È[zR+r ¢b'·6VÆbå÷&ööÕ÷66†VGVÆU÷7FFRævWB‚vVæEöBrÂrÒÓ¢ÒÒr—ÒiKn[zR ¢¢VÇ6S ¢6VÆbç&ööÕ÷&—GVÂç6WEFW‡B‚.X[YÎ[È[zRþiKn[z^ûÉ®iÊ®Šëî{Úâ"¢–b†6GG"‡6VÆbÂ'&ööÕö6†ÆÆVævR"“ ¢–b6VÆbå÷&ööÕö6†ÆÆVævU÷7FFS ¢6VÆbç&ööÕö6†ÆÆVævRç6WEFW‡B€¢b.X[YÎhÉh‰ŽûÉ§·6VÆbå÷&ööÕö6†ÆÆVævU÷7FFRævWB‚wF—FÆRrÂ~Kˆ‹[~ZèÎh‰r—Ò+r ¢b'¶f÷&ÖE÷v÷&µöGW&F–öâ†–çB‡6VÆbå÷&ööÕö6†ÆÆVævU÷7FFRævWB‚wF&vWE÷6V6öæG2r’÷"’—Ò+r ¢b.jøþK«¢¶–çB‡6VÆbå÷&ööÕö6†ÆÆVævU÷7FFRævWB‚wF&vWE÷&÷VæG2r’÷"—Ò‹Úâ ¢¢VÇ6S ¢6VÆbç&ööÕö6†ÆÆVævRç6WEFW‡B‚.X[YÎhÉh‰ŽûÉ®iÊ®Šëî{Úâ"¢7F—f—G’ÒÆ—7B‡&ööÕöFWF–ÂævWB‚'&ööÕö7F—f—G’"’÷"6VÆbæFFævWB‚'&ööÕö7F—f—G’"’÷"6VÆbæFFævWB‚&7F—f—G’"’÷"µÒ¢ÖUö–BÒ7G"†ÖRævWB‚'W6W%ö–B"’÷"ÖRævWB‚&–B"’÷"""¢f÷"WfVçB–â7F—f—G“ ¢–bæ÷B—6–ç7Fæ6R†WfVçBÂF–7B“ ¢6öçF–çVP¢WfVçEö–BÒ7G"†WfVçBævWB‚&–B"’÷"""¢F&vWEö–BÒ7G"†WfVçBævWB‚'F&vWEö–B"’÷"""¢–bWfVçEö–BæBWfVçEö–Bæ÷B–â6VÆbå÷6VVå÷&ööÕöWfVçEö–G3 ¢6VÆbå÷6VVå÷&ööÕöWfVçEö–G2æFB†WfVçEö–B¢—5÷F&vWBÒF&vWEö–BÓÒÖUö–B÷"€¢æ÷BF&vWEö–BæB7G"†WfVçBævWB‚'F&vWEöæ–6¶æÖR"’÷"""’ÓÒ7G"†ÖRævWB‚&æ–6¶æÖR"’÷"""¢¢–bÖUö–BæB—5÷F&vWBæB7G"†WfVçBævWB‚&7F÷%ö–B"’÷"""’ÒÖUö–C ¢6VÆbç&ööÕöWfVçE÷&V6V—fVBæVÖ—B†F–7B†WfVçB’¢6VÆbå÷&VæFW%÷&ööÕö7F—f—G’†7F—f—G’¢7F—fS×6VÆbæFFævWB‚&7F—fU÷f—6—G2"’÷"µÐ¢–b7F—fS¢6VÆbæ7F—fU÷f—6—BæVÖ—B†7F—fU³Ò¢–b6VÆbæFFævWB‚%÷7–æ5ööffÆ–æR"“ ¢vRÒ–çB‡6VÆbæFFævWB‚%÷7–æ5övUöÖ–çWFW2"’÷"¢vU÷FW‡BÒb.{ªb¶vWÒXˆn™)þX˜Ò"–bvRVÇ6R.X‰®h˜Ò ¢6VÆbå÷6WE÷7FGW2€¢b.[Ù>X˜Þizk9^‹ùîhê^ˆz®KšZêNûÈÎ[{.i‹îzK§¶vU÷FW‡GÞy¨NiÊÎYËx«nhûÉ¾{Ù{¹Îh.ZHÞYîKÉ®ˆz®XªŽYÎjÚ^8" ¢¢VÇ6S ¢6VÆbå÷6WE÷7FGW2‚.[{.X‹~ikûÈÎš^™Ú.Xh^ZëžiŠþiÈiky¨N8"" ¢FVb÷6fU÷&öf–ÆR‡6VÆb’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“¢&WGW&à¢6VÆbåö&Vv–åö7F–öâ‚.jÚ>YÊŽKùÞZÙŽ™©zxŠëî{Úî(
b"¢G'“ ¢ÖS×6VÆbæFFævWB‚&ÖR"’÷"·Ó²6VÆbæ6Æ–VçBçWFFU÷&öf–ÆR†æ–6¶æÖS×7G"†ÖRævWB‚&æ–6¶æÖR"’÷".XZÞjù¾i
ÞZÙ"’Çf—6–&–Æ—G“Ò&†–FFVâ"–b6VÆbæ†–FFVâæ—46†V6¶VB‚’VÇ6R&g&–VæG2"Ç6†÷uöW†7E÷F–ÖS×6VÆbæW†7Bæ—46†V6¶VB‚’ÆÆÆ÷u÷f—6—G3×6VÆbçf—6—G5öÆÆ÷vVBæ—46†V6¶VB‚’Æ÷WFf—Eö¶W“×6VÆbæ÷WFf—Eö¶W’“²6VÆbç&Vg&W6‚‚¢W†6WB6ö6–ÄW'&÷"2W†3¢6VÆbåöW'&÷"†W†2 ¢FVb÷6WE÷7V'67&—F–öâ‡6VÆbÂ'VFG“¢F–7E·7G"Âç•ÒÂVæ&ÆVC¢&ööÂ’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“ ¢&WGW&à¢'VFG•ö–BÒ7G"†'VFG’ævWB‚'W6W%ö–B"’÷"'VFG’ævWB‚&–B"’÷"""¢–bæ÷B'VFG•ö–C ¢&WGW&à¢G'“ ¢6WGFW"ÒvWFGG"‡6VÆbæ6Æ–VçBÂ'6WEö'VFG•÷7V'67&—F–öâ"ÂæöæR¢–b6ÆÆ&ÆR‡6WGFW"“ ¢6WGFW"†'VFG•ö–CÖ'VFG•ö–BÂöåöfö7W5÷7F'CÖVæ&ÆVBÂöåöfö7W5öVæCÖVæ&ÆVBÂ×WFVCÖæ÷BVæ&ÆVB¢VÇ6S ¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•÷6WEö'VFG•÷7V'67&—F–öâ"Â²&'VFG•ö–B#¢'VFG•ö–BÂ&öåöfö7W5÷7F'B#¢Væ&ÆVBÂ&öåöfö7W5öVæB#¢Væ&ÆVBÂ&×WFVB#¢æ÷BVæ&ÆVGÒ¢6VÆbå÷6WE÷7FGW2‚.i
ÞZÙx«nhŠê.™ˆ^[{.[ÈY
þ8""–bVæ&ÆVBVÇ6R.i
ÞZÙx«nhŠê.™ˆ^[{.X[>™zÞ8""¢W†6WB6ö6–ÄW'&÷"2W†3 ¢6VÆbåöW'&÷"†W†2 ¢FVböFEö'VFG’‡6VÆb’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“¢&WGW&à¢6öFRÆö³Õ–çWDF–ÆörævWEFW‡B‡6VÆbÂ.k{¾Xªi
ÞZÙ"Â.‹é>XZ^Zûžikžy¨B‚KØÞi
ÞZÙzûÉ¢"¢–bö²æB6öFS ¢6VÆbåö&Vv–åö7F–öâ‚.jÚ>YÊŽXù˜i
ÞZÙyK>Šû~(
b"¢G'“¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•öFEö'VFG•ö'•ö6öFR"Ç²&6öFR#¦6öFWÒ“²6VÆbç&Vg&W6‚‚“²6VÆbå÷6WE÷7FGW2‚.i
ÞZÙyK>Šû~[{.Xù˜8""¢W†6WB6ö6–ÄW'&÷"2W†3¢6VÆbåöW'&÷"†W†2¢FVb÷6VæE÷f—6—B‡6VÆb’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“¢&WGW&à¢—FVÓ×6VÆbæ'VFF–W2æ7W'&VçD—FVÒ‚¢–bæ÷B—FVÓ¢&WGW&â6VÆbåöW'&÷"…6ö6–ÄW'&÷"‚.Šû~XXŽ˜žhºžKˆKØÞi
ÞZÙ8""’¢'VFG’Ò—FVÒæFF…Bä—FVÔFF&öÆRåW6W%&öÆR¢–bæ÷B—6–ç7Fæ6R†'VFG’ÂF–7B“¢&WGW&â6VÆbåöW'&÷"…6ö6–ÄW'&÷"‚.Šû~XXŽ˜žhºžKˆKØÞi
ÞZÙ8""’¢6VÆbåö&Vv–åö7F–öâ‚.XZÞjù¾jÚ>YÊŽXxnZH~X{®Xù(
b"¢G'“ ¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•÷6VæE÷f—6—B"Ç²'F&vWB#¦'VFG•²'W6W%ö–B%ÒÂ'f—6—Eö¶–æB#¢'f—6—B'Ò“²6VÆbåöVæEö7F–öâ‚“²6VÆbå÷6WE÷7FGW2‚.XZÞjù¾[{.{¸þX{®XùûÈÎzØž[è^Zûžikžhê^Xù~K‹.™zŽ8""“²ÖW76vT&÷‚æ–æf÷&ÖF–öâ‡6VÆbÂ.[{.X{®Xù"Â.XZÞjù¾[{.{¸þX{®XùûÈÎzØž[è^Zûžikžhê^Xù~K‹.™zŽ8""¢W†6WB6ö6–ÄW'&÷"2W†3¢6VÆbåöW'&÷"†W†2¢FVbö66WEö–æ&÷‚‡6VÆb’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“¢&WGW&à¢—FVÓ×6VÆbæ–æ&÷‚æ7W'&VçD—FVÒ‚¢–bæ÷B—FVÓ¢&WGW&â6VÆbåöW'&÷"…6ö6–ÄW'&÷"‚.Šû~XXŽ˜žhºžKˆšžyK>Šû~h‰nK‹.™zŽ8""’¢¶–æBÆFFÖ—FVÒæFF…Bä—FVÔFF&öÆRåW6W%&öÆR¢6VÆbåö&Vv–åö7F–öâ‚.jÚ>YÊŽZHNyn˜žKŠÞy¨NyK>Šû~(
b"¢G'“ ¢–b¶–æCÓÒ&'VFG’#¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•÷&W7öæEö'VFG’"Ç²'&WVW7Eö–B#¦FF²&–B%ÒÂ&66WB#¥G'VWÒ¢VÇ6S¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•÷&W7öæE÷f—6—B"Ç²&WfVçEö–B#¦FF²&–B%ÒÂ&66WB#¥G'VWÒ¢6VÆbç&Vg&W6‚‚¢W†6WB6ö6–ÄW'&÷"2W†3¢6VÆbåöW'&÷"†W†2¢FVbö7&VFU÷&ööÒ‡6VÆb’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“¢&WGW&à¢æÖRÆö³Õ–çWDF–ÆörævWEFW‡B‡6VÆbÂ.X‰¾[»®ˆz®KšZêB"Â.ˆz®KšZêNYÞz{ûÉ¢"ÇFW‡CÒ.Zèž™Ùž[z^KÙÎ™{B"¢–bö²æBæÖS ¢6VÆbåö&Vv–åö7F–öâ‚.jÚ>YÊŽX‰¾[»®ˆz®KšZêN(
b"¢G'“¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•ö7&VFU÷&ööÒ"Ç²'&ööÕöæÖR#¦æÖWÒ“²6VÆbç&Vg&W6‚‚“²6VÆbå÷6WE÷7FGW2‚.ˆz®KšZêN[{.X‰¾[»®ûÈÎXúþKº^XˆnKª¾h‹þ™{NzK¨n8""¢W†6WB6ö6–ÄW'&÷"2W†3¢6VÆbåöW'&÷"†W†2¢FVbö¦ö–å÷&ööÒ‡6VÆb’ÓâæöæS ¢–bæ÷B6VÆbå÷&WV—&UöÆöv–â‚“¢&WGW&à¢6öFRÆö³Õ–çWDF–ÆörævWEFW‡B‡6VÆbÂ.XªXZ^ˆz®KšZêB"Â.‹é>XZR‚KØÞh‹þ™{NzûÉ¢"¢–bö²æB6öFS ¢6VÆbåö&Vv–åö7F–öâ‚.jÚ>YÊŽXªXZ^ˆz®KšZêN(
b"¢G'“¢6VÆbæ6Æ–VçBç'2‚&Æ–Æ•ö¦ö–å÷&ööÒ"Ç²&6öFR#¦6öFWÒ“²6VÆbç&Vg&W6‚‚“²6VÆbå÷6WE÷7FGW2‚.[{.XªXZ^ˆz®KšZêN8""¢W†6WB6ö6–ÄW'&÷"2W†3¢6VÆbåöW'&÷"†W†2 