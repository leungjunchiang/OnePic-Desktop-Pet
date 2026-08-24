"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。

账号注册会明确显示“等待邮箱确认”状态，并允许用户重新发送确认邮件；
邮箱确认页打开项目页面后，用户回到这里即可登录，不会把“没有即时 session”误报成注册失败。
"""

from __future__ import annotations

import sys
import time
import logging
from functools import cmp_to_key
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QLocale, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtCore import QCollator
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabWidget, QMenu,
    QVBoxLayout, QWidget, QSizePolicy,
)

from .resources import resource_path
from .accessories import SPECIAL_OUTFIT_SPRITES
from .social import SignupResult, SocialClient, SocialError, _heartbeat_payload, social_user_message
from .config import PET_NAME, clean_owner_nickname, social_pet_label
from .focus_analytics import MAX_ANALYTICS_DAY_SECONDS
from .work_timer import format_work_duration

LOGGER = logging.getLogger(__name__)

# Supabase returns timestamptz values with their UTC offset.  The room UI is
# intentionally fixed to China Standard Time instead of inheriting the
# machine's local timezone, so users in different regions see the same room
# timeline.  A fixed UTC+8 offset is sufficient for Beijing (no DST).
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

def _beijing_now() -> datetime:
    return datetime.now(BEIJING_TIMEZONE)


def _format_beijing_time(value: str) -> str:
    """Convert an ISO-8601 timestamp to the room's Beijing time (HH:MM)."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Server timestamps are timestamptz values.  Treat a legacy naive
        # value as UTC rather than silently using the user's machine timezone.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BEIJING_TIMEZONE).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError):
        return ""


def _room_focus_summary_text(summary: dict[str, Any], member_count: int = 0, focus_count: int = 0) -> str:
    """Render room focus as today's time plus the room's historical total.

    ``shared_focus_seconds`` remains a compatibility fallback for an older
    deployed function. New dashboards provide the two explicit room-scoped
    values so a member's personal daily total is never shown as the room
    total.
    """

    today_seconds = int(summary.get("today_shared_focus_seconds") or 0)
    cumulative_seconds = int(
        summary.get("cumulative_shared_focus_seconds")
        or summary.get("shared_focus_seconds")
        or 0
    )
    focus_text = (
        "当前专注人数待确认"
        if summary.get("presence_uncertain")
        else f"{int(summary.get('focus_count') or focus_count)} 人正在专注"
    )
    return (
        f"本房间 {int(summary.get('member_count') or member_count)} 人 · "
        f"{focus_text} · "
        f"今日共同专注 {format_work_duration(today_seconds)} · "
        f"累计共同专注 {format_work_duration(cumulative_seconds)}"
    )


def _presence_working(presence: dict[str, Any]) -> bool:
    """Read both the legacy boolean and the new explicit presence status.

    Older dashboard functions only returned ``working`` while the repaired
    function also returns ``status``.  Keeping this normalization in the UI
    prevents a mixed-version pair of clients from showing a false rest state.
    """

    status = str(presence.get("status") or "").strip().casefold()
    if status in {"focus", "working", "专注", "工作", "专注中", "正在工作"}:
        return True
    if status in {"rest", "idle", "offline", "休息", "休息中", "离线"}:
        return False
    value = presence.get("working")
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "focus", "working", "专注", "工作", "专注中", "正在工作"}
    return bool(value)


def _presence_status(presence: dict[str, Any]) -> str:
    """Return a stable user-facing status for old and new API payloads."""

    # A short dashboard outage is a transport problem, not a peer leave.  Keep
    # that state distinct so an old snapshot cannot be rendered as a false
    # “offline” result.
    if bool(presence.get("presence_uncertain")):
        return "unknown"
    if bool(presence.get("stale_presence")):
        return "offline"
    # Some older dashboard payloads can retain ``working`` or ``status``
    # after the server has already marked the user offline.  The explicit
    # online flag is authoritative in that case, otherwise the UI shows a
    # grey dot together with the contradictory “正在工作” label.
    if presence.get("online") is False:
        return "offline"
    status = str(presence.get("status") or "").strip().casefold()
    if status in {"offline", "离线"}:
        return "offline"
    if _presence_working(presence):
        return "focus"
    return "rest"


def _wealth_leaderboard_enabled(profile: dict[str, Any] | None) -> bool:
    """Keep the leaderboard opt-in default for legacy profiles.

    ``wealth_leaderboard_preference_set`` distinguishes an old row that has
    never made an explicit choice from a deliberate opt-out. This lets the
    UI remain enabled by default without undoing a user's saved opt-out.
    """

    if not isinstance(profile, dict):
        return True
    if not bool(profile.get("wealth_leaderboard_preference_set", False)):
        return True
    return bool(profile.get("wealth_leaderboard_enabled", True))


def _owner_nickname(record: dict[str, Any] | None) -> str:
    """Prefer the explicit social owner field while keeping old payloads usable."""

    if not isinstance(record, dict):
        return "搭子"
    return str(
        record.get("private_note_name")
        or record.get("owner_nickname")
        or record.get("nickname")
        or "搭子"
    ).strip() or "搭子"


def _owner_label(record: dict[str, Any] | None) -> str:
    return social_pet_label(_owner_nickname(record))


def _notification_sender_id(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return ""
    return str(
        record.get("sender_id")
        or record.get("requester_id")
        or record.get("peer_id")
        or record.get("actor_id")
        or record.get("user_id")
        or ""
    )


def _compare_buddies(left: dict[str, Any], right: dict[str, Any]) -> int:
    """在线优先、今日专注降序，最后按备注/姓名的中文拼音排序。"""

    def online(record: dict[str, Any]) -> int:
        return 0 if _presence_status(record) in {"focus", "rest"} else 1

    left_online, right_online = online(left), online(right)
    if left_online != right_online:
        return -1 if left_online < right_online else 1
    try:
        left_today = max(0, int(left.get("today_seconds") or 0))
    except (TypeError, ValueError):
        left_today = 0
    try:
        right_today = max(0, int(right.get("today_seconds") or 0))
    except (TypeError, ValueError):
        right_today = 0
    if left_today != right_today:
        return -1 if left_today > right_today else 1
    collator = QCollator(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    return collator.compare(_owner_nickname(left), _owner_nickname(right))


def _live_session_seconds(record: dict[str, Any]) -> int | None:
    """Calculate a peer's current round from the server start timestamp."""
    if _presence_status(record) != "focus":
        return 0
    started = str(record.get("session_started_at") or "")
    if not started:
        value = record.get("session_seconds")
        return int(value) if value is not None else None
    try:
        stamp = str(record.get("_server_timestamp") or "")
        now = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else datetime.now().astimezone()
        return max(0, int((now - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()))
    except (TypeError, ValueError):
        value = record.get("session_seconds")
        return int(value) if value is not None else None


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

    def __init__(self, client: SocialClient, presence: dict[str, Any], parent=None, *, send_heartbeat: bool = True) -> None:
        super().__init__(parent); self.client = client; self.presence = presence; self.send_heartbeat = send_heartbeat

    def run(self) -> None:
        try:
            heartbeat_error = ""
            focus_history_result = None
            personal_state = self.presence.get("personal_state")
            heartbeat_presence = _heartbeat_payload(self.presence)
            if self.send_heartbeat:
                try:
                    self.client.heartbeat(**heartbeat_presence)
                except (SocialError, TypeError) as exc:
                    # A transient write failure must not prevent the same
                    # cycle's dashboard read. Otherwise one platform can
                    # disappear from the other simply because its heartbeat
                    # proxy is briefly unavailable.
                    heartbeat_error = str(exc)
                    LOGGER.warning("social presence heartbeat failed: %s", exc)
            if isinstance(personal_state, dict):
                sync_rpc = getattr(self.client, "rpc", None)
                if callable(sync_rpc):
                    try:
                        sync_rpc(
                            "lili_sync_personal_state",
                            {
                                "p_focus_date": str(personal_state.get("focus_date") or ""),
                                "p_today_seconds": int(personal_state.get("today_seconds") or 0),
                                "p_lifetime_seconds": int(personal_state.get("lifetime_seconds") or 0),
                                "p_week_start": str(personal_state.get("week_start") or ""),
                                "p_week_seconds": int(personal_state.get("week_seconds") or 0),
                                "p_outfit_key": str(personal_state.get("outfit_key") or ""),
                                "p_outfit_set": bool(personal_state.get("outfit_set")),
                            },
                        )
                    except (SocialError, AttributeError, TypeError) as exc:
                        # Older relays can serve the room dashboard before the
                        # personal-state migration is deployed.  Keep the
                        # social room usable and retry on the next heartbeat.
                        LOGGER.info("personal state sync deferred: %s", exc)
                    try:
                        focus_history_result = sync_rpc(
                            "lili_sync_focus_history",
                            {"p_history": personal_state.get("focus_history") or []},
                        )
                    except (SocialError, AttributeError, TypeError) as exc:
                        # Daily history is additive.  If an older relay has not
                        # received this migration yet, the existing profile
                        # sync and local cache continue to work.
                        LOGGER.info("daily focus history sync deferred: %s", exc)
            room_id = self.presence.get("room_id")
            try:
                data = self.client.dashboard(room_id=room_id)
            except TypeError:
                # Keep third-party/test backends compatible while they adopt
                # the room-scoped dashboard argument.
                data = self.client.dashboard()
            leaderboard = getattr(self.client, "focus_leaderboard", None)
            if callable(leaderboard) and getattr(self.client, "signed_in", True):
                try:
                    data = dict(data or {})
                    data["leaderboard"] = leaderboard(period="week")
                except (SocialError, TypeError):
                    # A missing/temporarily unavailable economy RPC must not
                    # make the room heartbeat fail or clear the cached rows.
                    pass
            if heartbeat_error:
                data = dict(data or {})
                data["_presence_heartbeat_error"] = heartbeat_error
            if isinstance(focus_history_result, dict):
                data = dict(data or {})
                data["_focus_history"] = focus_history_result
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
    failed = Signal(object)

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
            leaderboard = getattr(self.client, "focus_leaderboard", None)
            if callable(leaderboard) and getattr(self.client, "signed_in", True):
                try:
                    data = dict(data or {})
                    data["leaderboard"] = leaderboard(period="week")
                except (SocialError, TypeError):
                    # Older deployments can run without the economy migration.
                    # The room dashboard remains usable while the migration is
                    # rolled out.
                    pass
            self.completed.emit(dict(data or {}))
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.room_id) if callable(cached_loader) else None
            if cached is not None:
                self.completed.emit(cached)
            else:
                self.failed.emit(exc)


class SocialHealthThread(QThread):
    """Probe the configured social endpoint without blocking the UI."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(self, client: SocialClient, room_id: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.room_id = room_id

    def run(self) -> None:
        try:
            checker = getattr(self.client, "diagnose_connection", None)
            if callable(checker):
                self.completed.emit(dict(checker(room_id=self.room_id) or {}))
                return
            checker = getattr(self.client, "health", None)
            if not callable(checker):
                raise SocialError("当前自习室后端未提供健康检查。", kind="config")
            self.completed.emit(dict(checker() or {}))
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            self.failed.emit(SocialError(f"健康检查失败：{exc}", kind="network"))


class SocialLoginThread(QThread):
    """Authenticate without blocking the Qt GUI thread."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._password = password

    def run(self) -> None:
        try:
            self.client.sign_in(self.email, self._password)
            streak = {}
            recorder = getattr(self.client, "record_login_streak", None)
            if callable(recorder):
                try:
                    streak = dict(recorder() or {})
                except SocialError as exc:
                    # Login is already valid. A rollout race or a temporary
                    # RPC outage must not turn a successful login into a
                    # failed one; the next app launch can record the day.
                    LOGGER.info("login streak record deferred: %s", exc)
                except Exception as exc:
                    LOGGER.info("login streak record deferred: %s", exc)
            self.completed.emit(streak)
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            # Never surface an unexpected transport exception or credentials
            # from the worker thread.  The UI can still offer a retry.
            self.failed.emit(
                SocialError("登录请求失败，请稍后重试。", kind="network", retryable=True)
            )
        finally:
            self._password = ""


class SocialLoginStreakThread(QThread):
    """Record a restored session's once-per-Beijing-day login."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(self, client: SocialClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            recorder = getattr(self.client, "record_login_streak", None)
            self.completed.emit(dict(recorder() or {}) if callable(recorder) else {})
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            self.failed.emit(SocialError(str(exc), kind="network", retryable=True))


class SocialSignupThread(QThread):
    """Create an account without blocking the Qt GUI thread on SMTP."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        client: SocialClient,
        email: str,
        password: str,
        nickname: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._password = password
        self.nickname = nickname

    def run(self) -> None:
        try:
            self.completed.emit(
                self.client.sign_up(self.email, self._password, self.nickname)
            )
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            # Keep unexpected transport/client failures user-safe and off the
            # GUI thread. Do not include credentials in the error text.
            self.failed.emit(
                SocialError("注册请求失败，请稍后重试。", kind="network", retryable=True)
            )
        finally:
            self._password = ""


class SocialResendConfirmationThread(QThread):
    """Resend a confirmation email without freezing the Qt GUI thread."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email

    def run(self) -> None:
        try:
            self.client.resend_confirmation(self.email)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(
                SocialError("确认邮件重发失败，请稍后重试。", kind="network", retryable=True)
            )


class SocialChangePasswordThread(QThread):
    """Change a password away from the Qt GUI thread."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, current_password: str, new_password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._current_password = current_password
        self._new_password = new_password

    def run(self) -> None:
        try:
            self.client.change_password(self._current_password, self._new_password)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("密码修改失败，请稍后重试。", kind="network", retryable=True))
        finally:
            self._current_password = ""
            self._new_password = ""


class SocialPasswordResetThread(QThread):
    """Request a recovery email without blocking the GUI thread."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email

    def run(self) -> None:
        try:
            self.client.request_password_reset(self.email)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("密码重置邮件发送失败，请稍后重试。", kind="network", retryable=True))


class SocialPasswordOtpResetThread(QThread):
    """Verify the in-memory recovery OTP and set the new password."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, otp: str, new_password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._otp = otp
        self._new_password = new_password

    def run(self) -> None:
        try:
            self.client.verify_password_reset_otp(self.email, self._otp)
            # Clear the code before the password update. The app never writes
            # the OTP to settings, logs, files, or the credential store.
            self._otp = ""
            self.client.set_password_after_reset(self._new_password)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("验证码验证或密码修改失败，请检查验证码后重试。", kind="network", retryable=True))
        finally:
            self._otp = ""
            self._new_password = ""


class PasswordResetDialog(QDialog):
    """Email OTP recovery flow; the server enforces the ten-minute expiry."""

    password_reset_completed = Signal(str)

    def __init__(self, client: SocialClient, email: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = str(email or "").strip()
        self._request_thread: SocialPasswordResetThread | None = None
        self._verify_thread: SocialPasswordOtpResetThread | None = None
        self._remaining_seconds = 0
        self.setWindowTitle("邮箱验证码重置密码 - Lili")
        self.setModal(True)
        self.resize(470, 430)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#title { font-size:22px; font-weight:700; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:8px; padding:7px 10px; }
            QLineEdit { background:white; border:1px solid #b8ccd6; border-radius:8px; padding:7px; }
            QPushButton { background:#d8efeb; color:#075d57; border:0; border-radius:8px; padding:8px 12px; }
            QPushButton:hover { background:#c7e5e1; }
            QPushButton#link { background:transparent; color:#087f74; text-align:left; padding:2px; }
        """)
        layout = QVBoxLayout(self); layout.setSpacing(10)
        title = QLabel("邮箱验证码重置密码"); title.setObjectName("title"); layout.addWidget(title)
        hint = QLabel("输入注册邮箱，发送 6 位验证码；验证码 10 分钟内有效，过期后必须重新发送。Lili 不保存验证码。")
        hint.setObjectName("muted"); hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout()
        self.email_edit = QLineEdit(self.email); self.email_edit.setPlaceholderText("注册邮箱")
        self.otp_edit = QLineEdit(); self.otp_edit.setPlaceholderText("邮件中的 6 位数字验证码"); self.otp_edit.setMaxLength(10)
        self.new_password = QLineEdit(); self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password = QLineEdit(); self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("邮箱", self.email_edit); layout.addLayout(form)
        otp_row = QHBoxLayout(); otp_row.addWidget(self.otp_edit, 1)
        self.send_button = QPushButton("发送验证码"); self.send_button.clicked.connect(self._send_code); otp_row.addWidget(self.send_button)
        layout.addLayout(otp_row)
        self.countdown = QLabel("尚未发送验证码"); self.countdown.setObjectName("muted"); layout.addWidget(self.countdown)
        password_form = QFormLayout(); password_form.addRow("新密码", self.new_password); password_form.addRow("确认新密码", self.confirm_password); layout.addLayout(password_form)
        self.status_label = QLabel(); self.status_label.setObjectName("status"); self.status_label.setWordWrap(True); self.status_label.hide(); layout.addWidget(self.status_label)
        self.verify_button = QPushButton("确认修改密码"); self.verify_button.setEnabled(False); self.verify_button.clicked.connect(self._verify_and_reset); layout.addWidget(self.verify_button)
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject); layout.addWidget(cancel)
        layout.addStretch(1)
        self._countdown_timer = QTimer(self); self._countdown_timer.setInterval(1000); self._countdown_timer.timeout.connect(self._tick)

    def _show_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"background:{'#f7e5e5' if error else '#e1efec'};color:{'#a33a3a' if error else '#087f74'};border-radius:8px;padding:7px 10px;"
        )
        self.status_label.show()

    def _send_code(self) -> None:
        email = self.email_edit.text().strip()
        if not email or "@" not in email:
            self._show_status("请输入有效的注册邮箱。", error=True); return
        if self._request_thread is not None and self._request_thread.isRunning():
            return
        self.email = email
        self.send_button.setEnabled(False); self.verify_button.setEnabled(False)
        self._show_status("正在发送验证码…")
        thread = SocialPasswordResetThread(self.client, email, self)
        self._request_thread = thread
        thread.completed.connect(self._code_sent)
        thread.failed.connect(self._code_failed)
        thread.finished.connect(lambda: self._request_finished(thread))
        thread.start()

    def _code_sent(self) -> None:
        self._remaining_seconds = 600
        self._countdown_timer.start()
        self.verify_button.setEnabled(True)
        self._show_status("如果该邮箱已注册，验证码已发送；请检查收件箱和垃圾邮件。")

    def _code_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._show_status(social_user_message(exc), error=True)

    def _request_finished(self, thread: SocialPasswordResetThread) -> None:
        if self._request_thread is thread:
            self._request_thread = None
        self.send_button.setEnabled(True); thread.deleteLater()

    def _tick(self) -> None:
        self._remaining_seconds = max(0, self._remaining_seconds - 1)
        minutes, seconds = divmod(self._remaining_seconds, 60)
        if self._remaining_seconds:
            self.countdown.setText(f"验证码剩余有效时间：{minutes:02d}:{seconds:02d}")
        else:
            self._countdown_timer.stop(); self.verify_button.setEnabled(False); self.countdown.setText("验证码已过期，请重新发送。")

    def _verify_and_reset(self) -> None:
        email = self.email_edit.text().strip(); otp = self.otp_edit.text().strip()
        new = self.new_password.text(); confirm = self.confirm_password.text()
        if self._remaining_seconds <= 0:
            self._show_status("验证码已过期，请重新发送。", error=True); return
        if not otp.isdigit() or len(otp) != 6:
            self._show_status("请输入邮件中的 6 位数字验证码。", error=True); return
        if len(new) < 8:
            self._show_status("新密码至少需要 8 位。", error=True); return
        if new != confirm:
            self._show_status("两次输入的新密码不一致。", error=True); return
        if self._verify_thread is not None and self._verify_thread.isRunning():
            return
        self.send_button.setEnabled(False); self.verify_button.setEnabled(False); self._show_status("正在验证验证码并修改密码…")
        thread = SocialPasswordOtpResetThread(self.client, email, otp, new, self)
        self._verify_thread = thread
        thread.completed.connect(self._reset_succeeded)
        thread.failed.connect(self._reset_failed)
        thread.finished.connect(lambda: self._verify_finished(thread))
        thread.start()

    def _reset_succeeded(self) -> None:
        self._countdown_timer.stop(); self.otp_edit.clear(); self.new_password.clear(); self.confirm_password.clear()
        self._show_status("密码已修改成功，现在可以使用新密码登录自习室。")
        self.password_reset_completed.emit(self.email)
        self.accept()

    def _reset_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self.verify_button.setEnabled(self._remaining_seconds > 0)
        self._show_status(social_user_message(exc), error=True)

    def _verify_finished(self, thread: SocialPasswordOtpResetThread) -> None:
        if self._verify_thread is thread:
            self._verify_thread = None
        self.send_button.setEnabled(True); thread.deleteLater()


class SocialDeleteAccountThread(QThread):
    """Re-authenticate and delete the current account away from the GUI."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, current_password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._current_password = current_password

    def run(self) -> None:
        try:
            # Require a fresh password check before the destructive RPC. The
            # RPC itself derives the user from auth.uid(), never from client
            # supplied IDs or email addresses.
            self.client.sign_in(self.email, self._current_password)
            self.client.delete_account()
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("注销账号失败，请稍后重试。", kind="network", retryable=True))
        finally:
            self._current_password = ""


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
            kind = str(self.event.get("kind") or "")
            sender = getattr(self.client, "send_interaction", None)
            if kind in {"poke", "cheer", "drink"} and callable(sender):
                sender(
                    target=str(self.event.get("target_id") or ""),
                    kind=kind,
                    room_id=str(self.event.get("room_id") or "") or None,
                )
            else:
                self.client.record_room_event(**self.event)
            self.completed.emit()
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class SocialVisitResponseThread(QThread):
    """Accept or reject one incoming visit/food request off the UI thread."""

    completed = Signal(dict, bool)
    failed = Signal(dict, str)

    def __init__(self, client: SocialClient, event: dict[str, Any], accept: bool, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.event = dict(event)
        self.accept = bool(accept)

    def run(self) -> None:
        try:
            self.client.rpc(
                "lili_respond_visit",
                {"event_id": str(self.event.get("id") or ""), "accept": self.accept},
            )
            self.completed.emit(self.event, self.accept)
        except (SocialError, AttributeError, TypeError) as exc:
            self.failed.emit(self.event, str(exc))


class SocialProfileThread(QThread):
    """Persist an owner nickname away from the Qt GUI thread."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, client: SocialClient, nickname: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.nickname = nickname

    def run(self) -> None:
        try:
            self.client.update_owner_nickname(self.nickname)
            self.completed.emit()
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class SocialBuddyRpcThread(QThread):
    """Run buddy lookup/request actions away from the Qt GUI thread."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, client: SocialClient, name: str, body: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.name = name
        self.body = dict(body)

    def run(self) -> None:
        try:
            self.completed.emit(self.client.rpc(self.name, self.body))
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(
                SocialError("搭子服务暂时没有响应，请稍后重试。", kind="network", retryable=True)
            )


class BuddyCodeDialog(QDialog):
    """仅负责输入搭子码；网络查找和发送申请由后台线程完成。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("查找搭子")
        self.setModal(True)
        self.setMinimumWidth(330)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入对方的 8 位搭子码"))
        hint = QLabel("先查找并确认资料，不会直接建立搭子关系。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(8)
        self.code_edit.setPlaceholderText("例如 AB12CD34")
        self.code_edit.setInputMethodHints(Qt.InputMethodHint.ImhUppercaseOnly)
        layout.addWidget(self.code_edit)
        buttons = QHBoxLayout()
        find = QPushButton("查找")
        cancel = QPushButton("取消")
        find.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self.code_edit.returnPressed.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(find)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.code_edit.setFocus()

    @property
    def code(self) -> str:
        return self.code_edit.text().strip().upper()


class BuddyProfileDialog(QDialog):
    """展示查找到的搭子资料，并把申请动作交给用户明确确认。"""

    def __init__(self, profile_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("搭子资料确认")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        title = QLabel("已找到搭子资料")
        title.setObjectName("title")
        layout.addWidget(title)

        profile = QLabel(profile_text)
        profile.setWordWrap(True)
        layout.addWidget(profile)

        hint = QLabel("确认资料后，点击“提交搭子申请”才会发送申请；点击“返回”不会发送。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.submit_button = QPushButton("提交搭子申请")
        self.return_button = QPushButton("返回")
        self.submit_button.clicked.connect(self.accept)
        self.return_button.clicked.connect(self.reject)
        buttons.addWidget(self.submit_button)
        buttons.addWidget(self.return_button)
        layout.addLayout(buttons)
        self.return_button.setFocus()


class BuddyCardWidget(QWidget):
    """把搭子的在线、工作和今日时长显示成一眼能看清的卡片。"""

    interaction_requested = Signal(dict, str)
    food_interaction_requested = Signal(dict, str)
    interaction_blocked = Signal(str)
    subscription_requested = Signal(dict, bool)

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self._cooldown_seconds = 15
        self._cooldown_until: dict[str, float] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._food_buttons: dict[str, QPushButton] = {}
        self.setObjectName("buddyCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(2)
        uncertain = bool(buddy.get("presence_uncertain"))
        status = _presence_status(buddy)
        online_flag = buddy.get("online")
        online = (
            status != "offline"
            and (online_flag is None or bool(online_flag))
            and not bool(buddy.get("stale_presence"))
            and not uncertain
        )
        nickname = _owner_nickname(buddy)
        is_self = bool(buddy.get("is_self"))
        if status == "unknown":
            if bool(buddy.get("online")) and bool(buddy.get("working")):
                status_text = "正在工作（同步恢复中）"
            elif bool(buddy.get("online")):
                status_text = "在线待确认"
            else:
                status_text = "状态待确认"
        else:
            status_text = {"focus": "正在工作", "rest": "正在休息", "offline": "已离线"}[status]
        headline = QLabel(
            f"{'🟡' if uncertain else '🟢' if online else '⚪'}  {social_pet_label(nickname)}"
            f"{status_text}{'（我）' if is_self else ''}"
        )
        headline.setWordWrap(False)
        headline.setStyleSheet("font-size:14px;font-weight:600;color:#203847;")
        root.addWidget(headline)
        duration = buddy.get("today_seconds")
        week_duration = buddy.get("week_seconds")
        if uncertain:
            age = int(buddy.get("presence_age_seconds") or 0)
            age_text = f"约 {max(1, age // 60)} 分钟前" if age else "刚才"
            time_text = f"实时状态暂无法确认（最后确认{age_text}），正在自动恢复"
        elif buddy.get("stale_presence"):
            time_text = "离线缓存；上次状态不计入当前专注"
        else:
            today_text = "今日专注时长已隐藏" if duration is None else f"今日已专注 {format_work_duration(duration)}"
            week_text = "本周专注时长已隐藏" if week_duration is None else f"本周已专注 {format_work_duration(week_duration)}"
            time_text = f"{today_text}　·　{week_text}"
        focus = QLabel(time_text)
        focus.setStyleSheet("font-size:14px;font-weight:700;color:#087f74;")
        root.addWidget(focus)
        quick_status = str(buddy.get("quick_status") or "").strip()
        expires = str(buddy.get("quick_status_expires_at") or "")
        if quick_status and (not expires or expires > datetime.now().astimezone().isoformat()):
            quick = QLabel(f"状态：{quick_status[:40]}")
            quick.setStyleSheet("color:#b36b2c;font-size:11px;font-weight:600;")
            root.addWidget(quick)
        outfit = str(buddy.get("outfit_key") or "经典六毛")
        footer = QLabel(f"娃衣：{outfit}")
        footer.setStyleSheet("color:#61727d;font-size:11px;")
        footer.setWordWrap(False)
        footer.setToolTip(f"当前娃衣：{outfit} · 可以直接对这位搭子串门、加油或送补给")
        root.addWidget(footer)
        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(4)
        actions.setVerticalSpacing(3)
        action_specs = (
            ("visit", "串门"),
            ("cheer", "加油"),
            ("food_coffee", "请咖啡"),
            ("food_milk_tea", "请奶茶"),
            ("food_tea", "敬茶"),
        )
        for index, (kind, label) in enumerate(action_specs):
            button = QPushButton(label)
            # Keep the compact two-row grid, but leave enough touch/trackpad
            # area for the supply actions on both Windows and macOS.
            button.setFixedHeight(32)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.setStyleSheet("font-size:11px;padding:2px 4px;border-radius:7px;")
            if kind.startswith("food_"):
                button.clicked.connect(lambda _checked=False, action_kind=kind: self._request_food(action_kind))
                if is_self:
                    button.setEnabled(False)
                    button.setToolTip("补给按钮只对房间里的其他搭子开放")
                self._food_buttons[kind] = button
            else:
                button.clicked.connect(lambda _checked=False, action=kind: self._request_interaction(action))
                if is_self:
                    button.setEnabled(False)
                    button.setToolTip("互动按钮只对房间里的其他搭子开放")
                self._buttons[kind] = button
            actions.addWidget(button, index // 3, index % 3)
        root.addLayout(actions)
        if not is_self:
            subscribe = QCheckBox("订阅开工/下班提醒")
            subscribe.setFixedHeight(18)
            subscribe.setChecked(bool(buddy.get("subscribed")))
            subscribe.stateChanged.connect(lambda state: self.subscription_requested.emit(self.buddy, bool(state)))
            root.addWidget(subscribe)

    def _request_food(self, kind: str) -> None:
        now = time.monotonic()
        key = f"food:{kind}"
        remaining = self._cooldown_until.get(key, 0.0) - now
        if remaining > 0:
            self.interaction_blocked.emit(f"互动冷却中，请 {int(remaining) + 1} 秒后再试。")
            return
        self._cooldown_until[key] = now + self._cooldown_seconds
        button = self._food_buttons.get(kind)
        if button is not None:
            button.setEnabled(False)
            button.setText(f"已发送 ({self._cooldown_seconds}s)")
            QTimer.singleShot(self._cooldown_seconds * 1000, lambda: self._restore_button(kind))
        self.food_interaction_requested.emit(self.buddy, kind)

    def _request_interaction(self, kind: str) -> None:
        now = time.monotonic()
        remaining = self._cooldown_until.get(kind, 0.0) - now
        if remaining > 0:
            self.interaction_blocked.emit(f"互动冷却中，请 {int(remaining) + 1} 秒后再试。")
            return
        self._cooldown_until[kind] = now + self._cooldown_seconds
        button = self._buttons.get(kind)
        if button is not None:
            button.setEnabled(False)
            button.setText(f"已发送 ({self._cooldown_seconds}s)")
            QTimer.singleShot(self._cooldown_seconds * 1000, lambda: self._restore_button(kind))
        self.interaction_requested.emit(self.buddy, kind)

    def _restore_button(self, kind: str) -> None:
        button = self._buttons.get(kind) or self._food_buttons.get(kind)
        if button is None:
            return
        labels = {
            "visit": "串门", "cheer": "加油",
            "food_coffee": "请咖啡", "food_milk_tea": "请奶茶",
            "food_tea": "敬茶", "food_cake": "请蛋糕",
        }
        button.setText(labels.get(kind, "互动"))
        if not bool(self.buddy.get("is_self")):
            button.setEnabled(True)


class RoomPetCardWidget(QWidget):
    """A compact room-stage card: pet first, metrics second, actions last."""

    interaction_requested = Signal(dict, str)
    food_interaction_requested = Signal(dict, str)

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self.setObjectName("roomPetCard")
        self.setMinimumHeight(112)
        self.setStyleSheet(
            "QWidget#roomPetCard{background:#f7fbfc;border:1px solid #c3d9df;border-radius:12px;}"
            "QPushButton{min-height:25px;padding:2px 7px;border-radius:7px;font-size:11px;"
            "background:#d8eeea;color:#245c59;}"
            "QPushButton:disabled{color:#9ba9ad;background:#e7eef0;}"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(9)
        details = QVBoxLayout()
        details.setSpacing(2)

        status = _presence_status(buddy)
        uncertain = bool(buddy.get("presence_uncertain"))
        online = status != "offline" and not bool(buddy.get("stale_presence")) and not uncertain
        if status == "unknown":
            status_text = "正在工作（同步恢复中）" if buddy.get("working") else "在线待确认"
        else:
            status_text = {"focus": "正在工作", "rest": "正在休息", "offline": "已离线"}[status]
        nickname = _owner_nickname(buddy)
        # Create the headline before the image so existing accessibility/tests
        # and screen readers encounter identity/state first.
        headline = QLabel(
            f"{'🟡' if uncertain else '🟢' if online else '⚪'}  {social_pet_label(nickname)}"
            f"{status_text}{'（我）' if buddy.get('is_self') else ''}"
        )
        headline.setStyleSheet("font-size:14px;font-weight:700;color:#203847;")
        headline.setWordWrap(False)
        details.addWidget(headline)
        session = format_work_duration(int(buddy.get("session_seconds") or 0))
        today = format_work_duration(int(buddy.get("today_seconds") or 0))
        week = format_work_duration(int(buddy.get("week_seconds") or 0))
        metrics = QLabel(f"本轮 {session}　·　今日 {today}　·　本周 {week}")
        metrics.setStyleSheet("font-size:11px;font-weight:600;color:#087f74;")
        details.addWidget(metrics)
        interruptions = int(buddy.get("today_interruptions") or 0)
        longest = format_work_duration(int(buddy.get("longest_continuous_seconds") or 0))
        continuity = f"今日中断 {interruptions} 次" if interruptions else "连续专注中"
        detail = QLabel(f"{continuity}　·　最长连续 {longest}")
        detail.setStyleSheet("font-size:11px;color:#61727d;")
        details.addWidget(detail)

        actions = QGridLayout()
        actions.setContentsMargins(0, 2, 0, 0)
        actions.setHorizontalSpacing(3)
        actions.setVerticalSpacing(3)
        is_self = bool(buddy.get("is_self"))
        specs = (("visit", "串门"), ("cheer", "加油"), ("food_coffee", "咖啡"), ("food_milk_tea", "奶茶"))
        for index, (kind, label) in enumerate(specs):
            button = QPushButton(label)
            button.setEnabled(not is_self)
            if kind.startswith("food_"):
                button.clicked.connect(lambda _checked=False, value=kind: self.food_interaction_requested.emit(self.buddy, value))
            else:
                button.clicked.connect(lambda _checked=False, value=kind: self.interaction_requested.emit(self.buddy, value))
            actions.addWidget(button, index // 2, index % 2)
        details.addLayout(actions)
        root.addLayout(details, 1)

        image = QLabel()
        image.setFixedSize(70, 82)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outfit = str(buddy.get("outfit_key") or "")
        relative = SPECIAL_OUTFIT_SPRITES.get(outfit, "assets/pet/daily-actions/39-work-study.png")
        pixmap = QPixmap(str(resource_path(relative)))
        if not pixmap.isNull():
            image.setPixmap(pixmap.scaled(70, 82, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        root.insertWidget(0, image)


class IncomingVisitNotice(QDialog):
    """Small non-modal prompt for a newly received visit or food interaction."""

    accept_requested = Signal(dict)
    reject_requested = Signal(dict)
    later_requested = Signal(dict)

    def __init__(self, event: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._event_payload = dict(event)
        self._closing = False
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("搭子互动")
        self.setMinimumWidth(360)
        self.setStyleSheet(
            "QDialog{background:#edf4f7;} QLabel{color:#263746;} "
            "QLabel#noticeTitle{font-size:18px;font-weight:700;} "
            "QLabel#noticeHint{color:#667984;} "
            "QPushButton{min-height:30px;padding:6px 12px;border:0;border-radius:9px;"
            "background:#d7ece8;color:#204c4a;font-weight:600;} "
            "QPushButton:hover{background:#c2e2dd;} QPushButton:disabled{color:#91a1a8;background:#e8eef0;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(9)
        title = QLabel(self._title_text())
        title.setObjectName("noticeTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        hint = QLabel("对方正在等你处理；也可以先选择“稍后处理”，稍后在待处理里继续。")
        hint.setObjectName("noticeHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        buttons = QHBoxLayout()
        buttons.setSpacing(7)
        self.accept_button = QPushButton("接受")
        self.reject_button = QPushButton("拒绝")
        self.later_button = QPushButton("稍后处理")
        self.accept_button.clicked.connect(lambda: self.accept_requested.emit(self._event_payload))
        self.reject_button.clicked.connect(lambda: self.reject_requested.emit(self._event_payload))
        self.later_button.clicked.connect(lambda: self.later_requested.emit(self._event_payload))
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.reject_button)
        buttons.addWidget(self.later_button)
        root.addLayout(buttons)

    def _title_text(self) -> str:
        nickname = _owner_label(self._event_payload)
        labels = {
            "food_coffee": "☕ 请你喝咖啡",
            "food_milk_tea": "🧋 请你喝奶茶",
            "food_tea": "🍵 请你喝茶",
            "food_cake": "🍰 请你吃蛋糕",
            "food_cake_share": "🍰 请你一起吃蛋糕",
        }
        return f"{nickname}{labels.get(str(self._event_payload.get('kind') or ''), '来串门了')}"

    def set_busy(self, busy: bool, message: str = "正在处理…") -> None:
        for button in (self.accept_button, self.reject_button, self.later_button):
            button.setEnabled(not busy)
        if busy:
            self.later_button.setText(message)
        else:
            self.later_button.setText("稍后处理")

    def close_without_notice(self) -> None:
        self._closing = True
        self.close()
        self._closing = False

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._closing:
            self.later_requested.emit(self._event_payload)
        super().closeEvent(event)


class BuddyVisitWindow(QWidget):
    """完全由本地素材绘制的双六毛陪伴窗口。"""

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
        self.title = QLabel("两只六毛一起工作中"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter); self.title.setStyleSheet("font-size:18px;font-weight:700;")
        self.subtitle = QLabel("💻 六毛　　六毛 📖\n一起工作中"); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock = QLabel("00:00:00"); self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter); self.clock.setStyleSheet("font-size:30px;font-weight:700;color:#087f74;")
        self.today = QLabel(); self.today.setAlignment(Qt.AlignmentFlag.AlignCenter); self.today.setStyleSheet("color:#61727d;")
        layout.addWidget(self.title); layout.addWidget(self.subtitle); layout.addWidget(self.clock); layout.addWidget(self.today)
        close = QPushButton("结束这次串门"); close.clicked.connect(self.hide_visit); layout.addWidget(close)
        self.elapsed = 0
        self._phase = 0
        self._mine_outfit = ""
        self._peer_outfit = ""
        self._mine_actions = ("02-office.png", "22-thermos.png", "04-guitar.png")
        self._peer_actions = ("09-night-reading.png", "03-headphones.png", "42-daydream.png")
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
        nickname = _owner_label(peer)
        self.title.setText(f"{nickname}来串门了")
        self.subtitle.setText(f"💻 {PET_NAME}　　{nickname} 📖\n一起工作中")
        peer_today = peer.get("today_seconds")
        peer_text = "时长隐藏" if peer_today is None else format_work_duration(peer_today)
        self.today.setText(f"你今日 {format_work_duration(mine_today)}　·　{nickname} 今日 {peer_text}")
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
        # 第一幕展示双方当前娃衣，后续动作均由本地轮换，不同步动画帧。
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


class AccountSecurityDialog(QDialog):
    """Small, explicit account-security surface for the desktop app."""

    logout_requested = Signal()
    account_deleted = Signal()

    def __init__(self, client: SocialClient, email: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = str(email or getattr(client, "account_email", "") or "").strip()
        self._change_thread: SocialChangePasswordThread | None = None
        self._reset_thread: SocialPasswordResetThread | None = None
        self._delete_thread: SocialDeleteAccountThread | None = None
        self.setWindowTitle("账号与安全 - Lili")
        self.setModal(True)
        self.resize(480, 520)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#title { font-size:22px; font-weight:700; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:8px; padding:7px 10px; }
            QLineEdit { background:white; border:1px solid #b8ccd6; border-radius:8px; padding:7px; }
            QPushButton { background:#d8efeb; color:#075d57; border:0; border-radius:8px; padding:8px 12px; }
            QPushButton:hover { background:#c7e5e1; }
            QPushButton#danger { background:#f4dddd; color:#9f3030; }
            QPushButton#link { background:transparent; color:#087f74; text-align:left; padding:2px; }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel("账号与安全"); title.setObjectName("title"); layout.addWidget(title)
        account_text = self.email or "当前已登录账号"
        account = QLabel(f"当前账号：{account_text}"); account.setObjectName("muted"); account.setWordWrap(True); layout.addWidget(account)
        form = QFormLayout()
        self.current_password = QLineEdit(); self.current_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password = QLineEdit(); self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password = QLineEdit(); self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("当前密码", self.current_password)
        form.addRow("新密码", self.new_password)
        form.addRow("确认新密码", self.confirm_password)
        layout.addLayout(form)
        hint = QLabel("至少 8 位，建议包含字母和数字。密码不会保存在 Lili。")
        hint.setObjectName("muted"); hint.setWordWrap(True); layout.addWidget(hint)
        self.status_label = QLabel(); self.status_label.setObjectName("status"); self.status_label.setWordWrap(True); self.status_label.hide(); layout.addWidget(self.status_label)
        self.change_button = QPushButton("确认修改"); self.change_button.clicked.connect(self._change_password); layout.addWidget(self.change_button)
        self.reset_button = QPushButton("忘记当前密码？通过邮箱重置"); self.reset_button.setObjectName("link"); self.reset_button.clicked.connect(self._request_reset); layout.addWidget(self.reset_button)
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine); line.setStyleSheet("color:#d6e1e6;"); layout.addWidget(line)
        self.logout_button = QPushButton("退出登录"); self.logout_button.clicked.connect(self._logout); layout.addWidget(self.logout_button)
        self.delete_button = QPushButton("注销账号"); self.delete_button.setObjectName("danger"); self.delete_button.clicked.connect(self._delete_account); layout.addWidget(self.delete_button)
        close = QPushButton("关闭"); close.clicked.connect(self.reject); layout.addWidget(close)
        layout.addStretch()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.current_password, self.new_password, self.confirm_password, self.change_button, self.reset_button, self.logout_button, self.delete_button):
            widget.setEnabled(not busy)

    def _show_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"background:{'#f7e5e5' if error else '#e1efec'};color:{'#a33a3a' if error else '#087f74'};border-radius:8px;padding:7px 10px;"
        )
        self.status_label.show()

    def _change_password(self) -> None:
        current = self.current_password.text()
        new = self.new_password.text()
        confirm = self.confirm_password.text()
        if not current:
            self._show_status("请输入当前密码。", error=True); return
        if len(new) < 8:
            self._show_status("新密码至少需要 8 位。", error=True); return
        if new != confirm:
            self._show_status("两次输入的新密码不一致。", error=True); return
        if self._change_thread is not None and self._change_thread.isRunning():
            return
        self._set_busy(True); self._show_status("正在修改密码…")
        thread = SocialChangePasswordThread(self.client, current, new, self)
        self._change_thread = thread
        thread.completed.connect(self._password_changed)
        thread.failed.connect(self._password_change_failed)
        thread.finished.connect(lambda: self._password_thread_finished(thread))
        thread.start()

    def _password_changed(self) -> None:
        self.current_password.clear(); self.new_password.clear(); self.confirm_password.clear()
        self._show_status("密码已修改成功；为保护账号安全，其他设备可能需要重新登录。")
        QMessageBox.information(self, "密码已修改", "密码已修改成功。其他设备可能需要重新登录。")

    def _password_change_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._show_status(social_user_message(exc), error=True)

    def _password_thread_finished(self, thread: SocialChangePasswordThread) -> None:
        if self._change_thread is thread:
            self._change_thread = None
        self._set_busy(False); thread.deleteLater()

    def _request_reset(self) -> None:
        email = self.email
        if not email:
            email, accepted = QInputDialog.getText(self, "邮箱重置密码", "请输入注册邮箱：")
            if not accepted:
                return
        email = email.strip()
        if not email or "@" not in email:
            self._show_status("请输入有效的邮箱地址。", error=True); return
        dialog = PasswordResetDialog(self.client, email, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._show_status("密码已通过邮箱验证码修改成功；现在可以使用新密码登录。")

    def _reset_requested(self) -> None:
        # Keep this response neutral even when the email is not registered.
        self._show_status("如果该邮箱已注册，我们会向其发送密码重置邮件；请检查收件箱和垃圾邮件。")
        QMessageBox.information(self, "密码重置邮件已提交", "如果该邮箱已注册，我们会向其发送密码重置邮件。请检查收件箱和垃圾邮件。")

    def _reset_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._show_status(social_user_message(exc), error=True)

    def _reset_thread_finished(self, thread: SocialPasswordResetThread) -> None:
        if self._reset_thread is thread:
            self._reset_thread = None
        self._set_busy(False); thread.deleteLater()

    def _logout(self) -> None:
        if self._change_thread is not None and self._change_thread.isRunning():
            return
        self.client.sign_out()
        self.logout_requested.emit()
        self.accept()

    def _delete_account(self) -> None:
        if not self.email:
            self._show_status("当前登录会话没有可用邮箱，暂时不能安全注销。", error=True); return
        answer = QMessageBox.warning(
            self,
            "注销账号",
            "注销会永久删除 Supabase 账号及六毛云端数据，不能恢复。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        confirmation, accepted = QInputDialog.getText(self, "确认注销账号", "请输入 DELETE 以确认：")
        if not accepted or confirmation.strip() != "DELETE":
            self._show_status("未完成注销确认。", error=True); return
        password, accepted = QInputDialog.getText(self, "验证当前密码", "请输入当前密码：", QLineEdit.EchoMode.Password)
        if not accepted or not password:
            return
        if self._delete_thread is not None and self._delete_thread.isRunning():
            return
        self._set_busy(True); self._show_status("正在验证并注销账号…")
        thread = SocialDeleteAccountThread(self.client, self.email, password, self)
        self._delete_thread = thread
        thread.completed.connect(self._account_deleted)
        thread.failed.connect(self._delete_failed)
        thread.finished.connect(lambda: self._delete_thread_finished(thread))
        thread.start()

    def _account_deleted(self) -> None:
        self.account_deleted.emit()
        QMessageBox.information(self, "账号已注销", "账号和六毛云端数据已删除。")
        self.accept()

    def _delete_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._show_status(social_user_message(exc), error=True)

    def _delete_thread_finished(self, thread: SocialDeleteAccountThread) -> None:
        if self._delete_thread is thread:
            self._delete_thread = None
        self._set_busy(False)
        thread.deleteLater()
        self._set_busy(False); thread.deleteLater()


class SocialHubDialog(QDialog):
    """提供首页、互动、专注、我的四个清晰页面及统一操作反馈。"""

    active_visit = Signal(dict)
    focus_start_requested = Signal()
    focus_pause_requested = Signal()
    focus_finish_requested = Signal()
    work_report_requested = Signal()
    focus_task_requested = Signal(str, int)
    tomorrow_review_requested = Signal(str)
    room_changed = Signal(object)
    room_event_received = Signal(dict)
    room_ritual_due = Signal(str)
    buddy_subscription_notice = Signal(str)
    quick_action_requested = Signal(str)
    food_interaction_requested = Signal(dict, str)
    food_interaction_accepted = Signal(dict)
    buddy_request_received = Signal(dict)
    account_state_changed = Signal(bool)
    login_streak_updated = Signal(dict)

    def __init__(self, client: SocialClient, outfit_key: str = "", owner_nickname: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.outfit_key = outfit_key
        self.owner_nickname = owner_nickname.strip()[:24]
        self.data: dict[str, Any] = {}
        self.current_room_id: str | None = None
        self._room_selection_explicit = False
        self._focus_snapshot: Any = None
        self._leaderboard_rows: list[Any] = []
        self._leaderboard_loaded = False
        self._leaderboard_error = False
        self._applying_dashboard = False
        self._room_goal_state: dict[str, Any] = {}
        self._room_schedule_state: dict[str, Any] = {}
        self._room_challenge_state: dict[str, Any] = {}
        self._seen_room_event_ids: set[str] = set()
        self._seen_buddy_request_ids: set[str] = set()
        self._muted_buddy_ids: set[str] = set()
        self._auto_accepting_food: set[str] = set()
        self._focus_analytics: dict[str, Any] = {}
        self._last_ritual_notice = ""
        self._pending_signup_email = ""
        self._account_email = str(getattr(client, "account_email", "") or "").strip()
        self._initial_refresh_timer = QTimer(self)
        self._initial_refresh_timer.setSingleShot(True)
        self._initial_refresh_timer.timeout.connect(self.refresh)
        self._room_refresh_timer = QTimer(self)
        self._room_refresh_timer.setSingleShot(True)
        self._room_refresh_timer.timeout.connect(self._refresh_selected_room)
        self._dashboard_thread: SocialDashboardThread | None = None
        self._room_refresh_pending = False
        self._health_thread: SocialHealthThread | None = None
        self._login_thread: SocialLoginThread | None = None
        self._login_streak_thread: SocialLoginStreakThread | None = None
        self._signup_thread: SocialSignupThread | None = None
        self._resend_thread: SocialResendConfirmationThread | None = None
        self._password_reset_thread: SocialPasswordResetThread | None = None
        self._event_threads: list[SocialEventThread] = []
        self._buddy_rpc_threads: list[SocialBuddyRpcThread] = []
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
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("六毛搭子自习室")
        self.resize(760, 760)
        self.setMinimumSize(520, 480)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#pageTitle { font-size:24px; font-weight:700; }
            QLabel#sectionTitle { font-size:17px; font-weight:650; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:9px; padding:7px 10px; }
            QFrame#card, QWidget#buddyCard { background:#ffffff; border:1px solid #d6e1e6; border-radius:14px; }
            QLineEdit, QListWidget { background:#ffffff; border:1px solid #b9c8d0; border-radius:10px; padding:7px; }
            QScrollArea#pageScroll { background:transparent; border:0; }
            QTabWidget::pane { border:0; }
            QTabBar::tab { min-width:0px; padding:10px 12px; color:#526872; }
            QTabBar::tab:selected { color:#087f74; font-weight:700; border-bottom:3px solid #38a397; }
            QPushButton { min-width:0px; max-width:16777215px; min-height:20px; padding:8px 14px; border:0; border-radius:9px; background:#d7ece8; color:#204c4a; font-weight:600; text-align:center; }
            QPushButton:hover { background:#c2e2dd; }
            QPushButton:disabled { color:#91a1a8; background:#e8eef0; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 20)
        root.setSpacing(9)
        title = QLabel("六毛搭子自习室")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel("一起聊天、专注和串门；未登录时，六毛仍可完整离线陪伴。")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)
        status_row = QHBoxLayout()
        self.status_label = QLabel("页面已准备好")
        self.status_label.setObjectName("status")
        status_row.addWidget(self.status_label, 1)
        self.relogin_button = QPushButton("重新登录")
        self.relogin_button.setVisible(False)
        self.relogin_button.clicked.connect(self._open_relogin)
        status_row.addWidget(self.relogin_button)
        root.addLayout(status_row)
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tab_bar = self.tabs.tabBar()
        # The study-room tabs are the primary navigation, so they must share
        # the full available width instead of keeping their content width.
        # This also makes narrow-window resizing predictable on both Qt
        # platforms.
        tab_bar.setExpanding(True)
        tab_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.addTab(self._home_page(), "首页")
        self.tabs.addTab(self._chat_page(), "互动")
        self.tabs.addTab(self._focus_page(), "专注")
        self.tabs.addTab(self._mine_page(), "我的")
        root.addWidget(self.tabs, 1)
        self._update_account_state()
        if client.signed_in:
            self._initial_refresh_timer.start(50)
            QTimer.singleShot(180, self._record_login_streak)

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
        labels = {"focus": "专注中", "rest": "休息中", "idle": "尚未开始"}
        self.focus_status.setText(labels.get(str(status), "等待同步"))
        self.focus_clock.setText(format_work_duration(int(session_seconds)))
        self.focus_today.setText(f"今日累计 {format_work_duration(int(today_seconds))}")
        self.focus_start.setEnabled(str(status) != "focus")
        self.focus_pause.setEnabled(str(status) == "focus")
        self.focus_finish.setEnabled(int(session_seconds) > 0 or int(today_seconds) > 0)
        self._refresh_own_focus_labels()

    def _local_today_seconds(self) -> int | None:
        snapshot = self._focus_snapshot
        if snapshot is None:
            return None
        if isinstance(snapshot, dict):
            value = snapshot.get("today_seconds")
        else:
            value = getattr(snapshot, "today_seconds", None)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _refresh_own_focus_labels(self) -> None:
        """Refresh both focus surfaces from the shared local snapshot."""

        seconds = self._local_today_seconds()
        if seconds is None:
            return
        if hasattr(self, "focus_today"):
            self.focus_today.setText(f"今日累计 {format_work_duration(seconds)}")
        if hasattr(self, "study_summary"):
            current = self.study_summary.text()
            marker = "我的今日专注 "
            start = current.find(marker)
            end = current.find("　·　", start + len(marker)) if start >= 0 else -1
            if start >= 0 and end >= 0:
                self.study_summary.setText(
                    current[:start] + marker + format_work_duration(seconds) + current[end:]
                )

    def _effective_focus_analytics(self) -> dict[str, Any]:
        """Include today's local seconds that have not reached analytics yet.

        FocusSession publishes its pause/finish snapshot before the owning
        window writes the just-finished segment into FocusAnalyticsStore.  A
        render in that small ordering gap otherwise shows an old weekly value
        even though today's FocusSession total is already current.
        """

        summary = dict(self._focus_analytics)
        local_today = self._local_today_seconds()
        if local_today is None:
            return summary
        try:
            recorded_today = max(0, int(summary.get("today_seconds") or 0))
            weekly = max(0, int(summary.get("weekly_total_seconds") or 0))
        except (TypeError, ValueError):
            return summary
        unrecorded_today = max(0, int(local_today) - recorded_today)
        if unrecorded_today > 0:
            summary["weekly_total_seconds"] = weekly + unrecorded_today

        # This is a day-vs-day metric, never a weekly total.  Do not add the
        # live weekly supplement to a stale comparison value: doing so can
        # turn an invalid weekly snapshot into text such as “较昨天 多
        # 49小时2分钟”.  Recompute from the explicit yesterday total and
        # keep the comparison unavailable if that total is not trustworthy.
        yesterday = summary.get("yesterday_seconds")
        try:
            yesterday_seconds = int(yesterday)
        except (TypeError, ValueError):
            yesterday_seconds = None
        if yesterday_seconds is not None and 0 <= yesterday_seconds <= MAX_ANALYTICS_DAY_SECONDS:
            summary["difference_vs_yesterday_seconds"] = int(local_today) - yesterday_seconds
        else:
            summary["difference_vs_yesterday_seconds"] = None
        return summary

    def set_room_quick_status(self, status: str, expires_at: datetime | None = None) -> None:
        """Render the local room action immediately, before the next heartbeat."""

        clean = str(status or "").strip()[:40]
        expiry = expires_at.isoformat() if expires_at is not None else None
        for person in getattr(self, "_room_people", []):
            if person.get("is_self"):
                person["quick_status"] = clean
                person["quick_status_expires_at"] = expiry
        if hasattr(self, "room_members") and getattr(self, "_room_people", None):
            self._render_room_people(self._room_people)

    def set_owner_nickname(self, nickname: str) -> None:
        self.owner_nickname = str(nickname or "").strip()[:24]
        if self.data:
            me = self.data.get("me") or {}
            own_label = social_pet_label(self.owner_nickname or me.get("nickname"))
            self.identity.setText(f"{own_label} · 我的搭子码：{me.get('invite_code','--------')}")

    def set_focus_analytics(self, snapshot: dict[str, Any] | None) -> None:
        """Render local continuity metrics and the one-task countdown."""

        self._focus_analytics = dict(snapshot or {})
        if not hasattr(self, "focus_insights"):
            return
        summary = self._effective_focus_analytics()
        task = summary.get("current_task") or {}
        task_text = "当前任务：未设置"
        if isinstance(task, dict) and task.get("title"):
            task_text = f"当前任务：{task['title']}"
            due = str(task.get("due_at") or "")
            if due:
                try:
                    deadline = datetime.fromisoformat(due.replace("Z", "+00:00"))
                    if deadline.tzinfo is None:
                        deadline = deadline.astimezone()
                    remaining = max(0, int((deadline - datetime.now().astimezone()).total_seconds()))
                    task_text += f" · 剩余 {format_work_duration(remaining)}"
                except ValueError:
                    pass
        first_task = str(summary.get("first_task_today") or "")
        if first_task:
            task_text += f"\n今天第一件事：{first_task}"
        self.focus_task.setText(task_text)
        difference = summary.get("difference_vs_yesterday_seconds")
        if difference is None:
            comparison = "较昨天 暂无可靠数据"
        else:
            difference = int(difference)
            comparison = (
                f"较昨天 {'多' if difference >= 0 else '少'} "
                f"{format_work_duration(abs(difference))}"
            )
        self.focus_insights.setText(
            f"今天第 {int(summary.get('today_rounds') or 0)} 轮 · 连续 {int(summary.get('current_streak_days') or 0)} 天 · "
            f"本周 {format_work_duration(int(summary.get('weekly_total_seconds') or 0))}\n"
            f"最长连续 {int(summary.get('longest_streak_days') or 0)} 天 · "
            f"最长专注 {format_work_duration(int(summary.get('longest_continuous_seconds') or 0))} · "
            f"今日中断 {int(summary.get('today_interruptions') or 0)} 次 · "
            f"{comparison} · "
            f"{summary.get('quality_label') or '暂无质量数据'}"
        )

    @staticmethod
    def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(0)
        # Ignore child size hints here. A long label or form control must not
        # make the page wider than the scroll viewport.
        card.setMaximumWidth(16777215)
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        if description:
            detail = QLabel(description)
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        return card, layout

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        """Keep dense pages usable when the utility window is made smaller."""

        page.setMinimumSize(0, 0)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumSize(0, 0)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _fit_list_height(widget: QListWidget, minimum: int, maximum: int) -> None:
        """Size short lists to their contents while retaining scrolling for long lists."""

        widget.setMinimumHeight(minimum)
        widget.setMaximumHeight(maximum)
        widget.ensurePolished()
        total = max(0, widget.frameWidth() * 2)
        for index in range(widget.count()):
            row_height = widget.sizeHintForRow(index)
            total += row_height if row_height > 0 else widget.fontMetrics().lineSpacing() + 14
        desired = min(maximum, max(minimum, total + 8))
        widget.setFixedHeight(desired)

    @staticmethod
    def _set_buddy_item_height(item: QListWidgetItem, widget: QWidget) -> None:
        widget.ensurePolished()
        # Keep a dense, repeatable row so a room with many buddies remains
        # scannable. The widget still determines the font/DPI-aware height;
        # only a small platform safety margin is added.
        item.setSizeHint(QSize(0, max(96, widget.sizeHint().height() + 6)))

    def _home_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        welcome, welcome_layout = self._card("今天也一起往前一点", "查看搭子动态、待处理邀请和当前专注状态。")
        self.study_summary = QLabel("登录后可查看搭子今天和本周的专注时间；本地工作计时不受影响。")
        self.study_summary.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        self.study_summary.setWordWrap(True)
        welcome_layout.addWidget(self.study_summary)
        refresh = QPushButton("刷新首页")
        refresh.clicked.connect(self.refresh)
        welcome_layout.addWidget(refresh)
        network_row = QHBoxLayout()
        self.network_hint = QLabel(self._backend_hint())
        self.network_hint.setObjectName("muted")
        self.network_hint.setWordWrap(True)
        network_row.addWidget(self.network_hint, 1)
        network_check = QPushButton("检测自习室网络")
        network_check.clicked.connect(self._check_network)
        network_row.addWidget(network_check)
        welcome_layout.addLayout(network_row)
        layout.addWidget(welcome)
        buddies_card, buddies_layout = self._card("我的搭子", "在线搭子优先，其次按今天专注时间，最后按备注/姓名拼音排序。绿色表示最近两分钟内有心跳；灰色表示已离线。右键搭子卡片可设置消息免打扰或删除搭子。")
        buddy_tools = QHBoxLayout()
        add_buddy = QPushButton("用搭子码添加")
        add_buddy.clicked.connect(self._add_buddy)
        buddy_tools.addWidget(add_buddy)
        buddy_tools.addStretch()
        buddies_layout.addLayout(buddy_tools)
        self.buddies = QListWidget(); self.buddies.setSpacing(5)
        self.buddies.setMinimumHeight(46); self.buddies.setMaximumHeight(360)
        self.buddies.itemDoubleClicked.connect(lambda _item: self._send_visit())
        self.buddies.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.buddies.customContextMenuRequested.connect(self._buddy_context_menu)
        buddies_layout.addWidget(self.buddies)
        layout.addWidget(buddies_card)
        wealth_card, wealth_layout = self._card(
            "本周专注排行榜",
            "只展示已接受搭子且主动参与的好友；按本周专注时间排名。默认参与，可在“我的”中关闭。",
        )
        self.wealth_leaderboard = QListWidget()
        self.wealth_leaderboard.setMinimumHeight(52)
        self.wealth_leaderboard.setMaximumHeight(210)
        wealth_layout.addWidget(self.wealth_leaderboard)
        layout.addWidget(wealth_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _chat_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        inbox_card, inbox_layout = self._card("待处理", "搭子申请、成果见证和串门都会在这里等待你的明确决定。添加搭子请回到首页“我的搭子”。")
        self.inbox = QListWidget(); self.inbox.setMinimumHeight(125); self.inbox.setMaximumHeight(360)
        self.inbox.currentItemChanged.connect(self._update_inbox_actions)
        inbox_layout.addWidget(self.inbox)
        inbox_buttons = QHBoxLayout()
        self.inbox_accept_button = QPushButton("接受"); self.inbox_accept_button.clicked.connect(self._accept_inbox)
        self.inbox_reject_button = QPushButton("拒绝"); self.inbox_reject_button.clicked.connect(self._reject_inbox)
        self.inbox_cancel_button = QPushButton("撤回申请"); self.inbox_cancel_button.clicked.connect(self._cancel_buddy_request)
        inbox_buttons.addWidget(self.inbox_accept_button)
        inbox_buttons.addWidget(self.inbox_reject_button)
        inbox_buttons.addWidget(self.inbox_cancel_button)
        inbox_layout.addLayout(inbox_buttons)
        self._update_inbox_actions(None, None)
        layout.addWidget(inbox_card)
        recent_card, recent_layout = self._card("最近互动", "已处理的事件会保留一条轻量记录。")
        self.recent_interactions = QListWidget(); self.recent_interactions.setMaximumHeight(180)
        recent_layout.addWidget(self.recent_interactions)
        layout.addWidget(recent_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _focus_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        focus_card, focus_layout = self._card(
            "我的专注",
            "桌面六毛与自习室共用同一个 FocusSession；这里不会再启动第二套计时器。",
        )
        self.focus_status = QLabel("等待同步")
        self.focus_status.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        self.focus_clock = QLabel("0分钟")
        self.focus_clock.setStyleSheet("font-size:28px;font-weight:700;color:#203847;")
        self.focus_today = QLabel("今日累计 0分钟")
        self.focus_today.setObjectName("muted")
        focus_layout.addWidget(self.focus_status)
        focus_layout.addWidget(self.focus_clock)
        focus_layout.addWidget(self.focus_today)
        self.focus_task = QLabel("当前任务：未设置")
        self.focus_task.setObjectName("muted")
        self.focus_task.setWordWrap(True)
        focus_layout.addWidget(self.focus_task)
        self.focus_insights = QLabel("今天第 0 轮 · 连续 0 天 · 本周 0分钟")
        self.focus_insights.setObjectName("muted")
        self.focus_insights.setWordWrap(True)
        focus_layout.addWidget(self.focus_insights)
        controls = QGridLayout()
        self.focus_start = QPushButton("开始专注")
        self.focus_pause = QPushButton("暂停休息")
        self.focus_finish = QPushButton("结束本轮")
        self.focus_start.clicked.connect(self.focus_start_requested.emit)
        self.focus_pause.clicked.connect(self.focus_pause_requested.emit)
        self.focus_finish.clicked.connect(self.focus_finish_requested.emit)
        for column, button in enumerate((self.focus_start, self.focus_pause, self.focus_finish)):
            controls.addWidget(button, 0, column)
            controls.setColumnStretch(column, 1)
        focus_layout.addLayout(controls)
        report_button = QPushButton("查看工作报告")
        report_button.setObjectName("focusReportButton")
        report_button.clicked.connect(self.work_report_requested.emit)
        focus_layout.addWidget(report_button)
        task_button = QPushButton("设置一次只盯一件事")
        task_button.clicked.connect(self._set_focus_task)
        review_button = QPushButton("写下明天第一件事")
        review_button.clicked.connect(self._set_tomorrow_review)
        task_row = QGridLayout(); task_row.addWidget(task_button, 0, 0); task_row.addWidget(review_button, 0, 1)
        task_row.setColumnStretch(0, 1); task_row.setColumnStretch(1, 1)
        focus_layout.addLayout(task_row)
        layout.addWidget(focus_card)

        room_card, room_layout = self._card(
            "共同专注房间",
            "只显示专注/休息和累计时长；不会上传正在使用的软件、窗口标题或任务内容。",
        )
        self.room_goal = QLabel("尚未选择房间目标")
        self.room_goal.setObjectName("muted")
        room_layout.addWidget(self.room_goal)
        self.room_summary = QLabel("选择一个房间后，这里会显示共同专注人数和累计时长。")
        self.room_summary.setObjectName("muted")
        self.room_summary.setWordWrap(True)
        room_layout.addWidget(self.room_summary)
        room_exclusive_note = QLabel("同一时间一个账号只会出现在一个自习室；加入或创建新房间会自动离开旧房间。")
        room_exclusive_note.setObjectName("muted")
        room_exclusive_note.setWordWrap(True)
        room_layout.addWidget(room_exclusive_note)
        self.room_members = QListWidget(); self.room_members.setSpacing(6)
        self.room_members.setObjectName("roomStage")
        self.room_members.setStyleSheet(
            "QListWidget#roomStage{background:#eaf4f5;border:1px solid #bfd6dc;border-radius:12px;padding:5px;}"
            "QListWidget#roomStage::item{background:transparent;border:0;}"
        )
        self.room_members.setMinimumHeight(120); self.room_members.setMaximumHeight(360)
        room_layout.addWidget(self.room_members)
        self.room_activity_label = QLabel("房间动态（北京时间）")
        self.room_activity_label.setObjectName("muted")
        room_layout.addWidget(self.room_activity_label)
        self.room_activity = QListWidget(); self.room_activity.setMinimumHeight(90); self.room_activity.setMaximumHeight(180)
        room_layout.addWidget(self.room_activity)
        self.room_ritual = QLabel("共同开工/收工：未设置")
        self.room_ritual.setObjectName("muted")
        self.room_ritual.setWordWrap(True)
        room_layout.addWidget(self.room_ritual)
        self.room_challenge = QLabel("共同挑战：未设置")
        self.room_challenge.setObjectName("muted")
        self.room_challenge.setWordWrap(True)
        room_layout.addWidget(self.room_challenge)
        self.rooms = QListWidget(); self.rooms.setMinimumHeight(52); self.rooms.setMaximumHeight(140)
        self.rooms.currentItemChanged.connect(self._room_selected)
        room_layout.addWidget(self.rooms)
        row = QGridLayout(); create = QPushButton("创建自习室"); join = QPushButton("使用房间码加入")
        create.clicked.connect(self._create_room); join.clicked.connect(self._join_room)
        row.addWidget(create, 0, 0); row.addWidget(join, 0, 1)
        row.setColumnStretch(0, 1); row.setColumnStretch(1, 1); room_layout.addLayout(row)
        invite_row = QHBoxLayout()
        self.room_invite_button = QPushButton("邀请好友")
        self.room_invite_button.clicked.connect(self._show_room_invite)
        self.room_invite_button.setEnabled(False)
        invite_row.addWidget(self.room_invite_button)
        invite_row.addStretch()
        room_layout.addLayout(invite_row)
        room_actions = QGridLayout()
        self.room_goal_button = QPushButton("设置共同目标")
        self.room_schedule_button = QPushButton("一起开工/收工")
        self.room_challenge_button = QPushButton("设置共同挑战")
        self.room_leave_button = QPushButton("离开当前房间")
        self.room_start_prompt_button = QPushButton("喊大家开工")
        self.room_goal_button.clicked.connect(self._set_room_goal)
        self.room_schedule_button.clicked.connect(self._set_room_schedule)
        self.room_challenge_button.clicked.connect(self._set_room_challenge)
        self.room_leave_button.clicked.connect(self._leave_room)
        self.room_start_prompt_button.clicked.connect(lambda: self._quick_action_clicked("一起开工"))
        for index, button in enumerate((self.room_goal_button, self.room_schedule_button, self.room_challenge_button, self.room_start_prompt_button, self.room_leave_button)):
            room_actions.addWidget(button, index // 2, index % 2)
            room_actions.setColumnStretch(index % 2, 1)
        room_layout.addLayout(room_actions)
        phrase_row = QGridLayout()
        for index, phrase in enumerate(("我也开工了", "再卷 30 分钟", "去喝水", "下班没？")):
            button = QPushButton(phrase)
            button.setToolTip("发送给当前房间成员，短时间内不会重复骚扰同一人")
            button.clicked.connect(lambda _checked=False, value=phrase: self._quick_action_clicked(value))
            phrase_row.addWidget(button, index // 2, index % 2)
            phrase_row.setColumnStretch(index % 2, 1)
        room_layout.addLayout(phrase_row)
        self.room_goal_timer = QTimer(self)
        self.room_goal_timer.setInterval(1000)
        self.room_goal_timer.timeout.connect(self._refresh_room_goal_text)
        self.room_goal_timer.start()
        layout.addWidget(room_card)
        layout.addStretch()
        self.set_focus_snapshot(self._focus_snapshot or {"status": "idle", "session_seconds": 0, "today_seconds": 0})
        self.set_focus_analytics(self._focus_analytics)
        return self._scroll_page(page)

    def _quick_action_clicked(self, action: str) -> None:
        if action in {"下班没？", "一起开工"}:
            if action == "一起开工":
                self._send_phrase("一起开工？")
                return
            self._send_phrase(action)
            return
        if not self._require_login() or not self.current_room_id:
            self._set_status("请先加入一个共同房间，再改变房间状态。", error=True)
            return
        self.quick_action_requested.emit(action)
        self._set_status(f"正在把“{action}”同步给当前房间…")

    def _room_id_from_payload(self, room: dict[str, Any]) -> str | None:
        room_id = str(room.get("id") or room.get("room_id") or "")
        if not room_id and not isinstance(self.client, SocialClient):
            # Lightweight offline clients often only expose the invite code.
            # Real room payloads carry the ID used by the API.
            room_id = str(room.get("invite_code") or "")
        return room_id or None

    def _room_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        room = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.current_room_id = self._room_id_from_payload(room) if isinstance(room, dict) else None
        self._room_selection_explicit = bool(self.current_room_id)
        if self.current_room_id:
            self.room_changed.emit(self.current_room_id)
            self._set_status("已切换房间；正在同步这个房间的成员、目标和动态。")
            if not self._applying_dashboard:
                self._room_refresh_timer.start(0)

    def _show_room_invite(self) -> None:
        """Reveal the invite code only when the user explicitly asks for it."""

        if not self.current_room_id:
            self._set_status("请先选择一个自习室。", error=True)
            return
        current = self.rooms.currentItem()
        room = current.data(Qt.ItemDataRole.UserRole) if current is not None else {}
        code = str(room.get("invite_code") or "") if isinstance(room, dict) else ""
        if code:
            QMessageBox.information(self, "邀请好友", f"把这个房间码发给搭子：\n\n{code}")
        else:
            self._set_status("当前房间暂时没有可用房间码。", error=True)

    def _backend_hint(self) -> str:
        backend = str(getattr(self.client, "backend_name", "unknown") or "unknown")
        endpoint = str(getattr(self.client, "backend_endpoint", "") or "")
        if endpoint:
            return f"当前自习室后端：{backend} · {endpoint}"
        return f"当前自习室后端：{backend} · 未配置独立中转服务"

    def _check_network(self) -> None:
        if self._health_thread is not None and self._health_thread.isRunning():
            return
        self._begin_action("正在检测自习室网络…")
        if not isinstance(self.client, SocialClient):
            try:
                checker = getattr(self.client, "health", None)
                if not callable(checker):
                    raise SocialError("当前测试后端未提供健康检查。", kind="config")
                self._network_check_succeeded(dict(checker() or {}))
            except Exception as exc:
                self._network_check_failed(exc)
            return
        thread = SocialHealthThread(self.client, self.current_room_id, self)
        self._health_thread = thread
        thread.completed.connect(self._network_check_succeeded)
        thread.failed.connect(self._network_check_failed)
        thread.finished.connect(lambda: self._health_thread_finished(thread))
        thread.start()

    def _network_check_succeeded(self, data: dict[str, Any]) -> None:
        self._end_action()
        backend = str(data.get("backend") or getattr(self.client, "backend_name", "social"))
        service = str(data.get("service") or getattr(self.client, "backend_endpoint", "服务可达"))
        self.network_hint.setText(f"当前自习室后端：{backend} · {service}")
        state = str(data.get("connection_state") or "")
        snapshot = data.get("dashboard")
        if isinstance(snapshot, dict):
            snapshot = dict(snapshot)
            snapshot["_connection_state"] = state or snapshot.get("_connection_state")
            snapshot["data_source"] = data.get("data_source") or snapshot.get("data_source")
            self.apply_dashboard(snapshot)
            if state == "ONLINE":
                self._set_status("自习室已连接，房间状态已完整同步。")
            elif state == "DEGRADED":
                self._set_status("自习室已连接，但实时同步暂时不可用，继续重新连接。")
            else:
                self._set_status("当前显示离线缓存，等网络恢复后再同步。")
            return
        if state == "DEGRADED":
            self._set_status("自习室网络可达，但账号或房间数据还未验证。")
            return
        # Health is deliberately separate from business-data initialization.
        # The normal dashboard timer will refresh room data independently.
        if getattr(self.client, "signed_in", False):
            self._set_status("自习室网络正常；房间数据将按正常同步节奏更新。")
            return
        self._set_status("自习室网络可达，请登录后同步房间。")

    def _network_check_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, SocialError) else SocialError(str(error), kind="network")
        LOGGER.warning(
            "social health check failed kind=%s endpoint=%s status=%s: %s",
            exc.kind,
            exc.endpoint,
            exc.status,
            exc,
        )
        self._set_status(f"网络检查失败：{social_user_message(exc)}", error=True)

    def _health_thread_finished(self, thread: SocialHealthThread) -> None:
        if self._health_thread is thread:
            self._health_thread = None
        thread.deleteLater()

    def _start_dashboard_refresh(self, room_id: str | None, message: str) -> None:
        """Start one coalesced dashboard request away from the GUI thread."""

        if not self.client.signed_in:
            return
        if self._dashboard_thread is not None and self._dashboard_thread.isRunning():
            if room_id and room_id == self.current_room_id:
                self._room_refresh_pending = True
            return

        # The application uses SocialClient, whose dashboard call performs
        # network I/O and therefore must stay off the GUI thread.  The small
        # in-memory clients used by the desktop smoke tests and offline demo
        # are deliberately kept synchronous so a refresh remains immediately
        # observable to callers without needing a second event-loop turn.
        if not isinstance(self.client, SocialClient):
            self._begin_action(message)
            try:
                try:
                    data = self.client.dashboard(room_id=room_id)
                except TypeError:
                    data = self.client.dashboard()
            except SocialError as exc:
                self._dashboard_failed(str(exc))
                return
            self._end_action()
            self.apply_dashboard(data)
            return

        self._begin_action(message)
        thread = SocialDashboardThread(self.client, room_id, self)
        self._dashboard_thread = thread
        thread.completed.connect(
            lambda data, requested_room=room_id: self._dashboard_received(data, requested_room)
        )
        thread.failed.connect(self._dashboard_failed)
        thread.finished.connect(lambda: self._dashboard_thread_finished(thread))
        thread.start()

    def _dashboard_received(self, data: dict[str, Any], requested_room: str | None) -> None:
        self._end_action()
        # If the user changed rooms while an older request was in flight,
        # render the base snapshot but queue one request for the new room.
        if requested_room and requested_room != self.current_room_id:
            self._room_refresh_timer.start(0)
            return
        self.apply_dashboard(data)

    def _dashboard_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        kind = str(getattr(exc, "kind", "") or "")
        is_auth = kind.startswith("auth") or bool(getattr(exc, "error_code", ""))
        self._set_status(
            f"同步失败：{social_user_message(exc)}",
            error=True,
            relogin=is_auth,
        )

    def _dashboard_thread_finished(self, thread: SocialDashboardThread) -> None:
        if self._dashboard_thread is thread:
            self._dashboard_thread = None
        thread.deleteLater()
        if self._room_refresh_pending and self.current_room_id and self.client.signed_in:
            self._room_refresh_pending = False
            QTimer.singleShot(0, self._refresh_selected_room)

    def _refresh_selected_room(self) -> None:
        if not self.current_room_id or not self.client.signed_in:
            return
        self._start_dashboard_refresh(self.current_room_id, "正在同步当前自习室…")

    def _send_interaction(self, buddy: dict[str, Any], kind: str) -> None:
        if not self._require_login():
            return
        target = str(buddy.get("user_id") or buddy.get("id") or "")
        nickname = _owner_label(buddy)
        labels = {"visit": "发出串门邀请", "cheer": "送上加油"}
        if kind == "visit":
            try:
                self.client.rpc("lili_send_visit", {"target": target, "visit_kind": "visit"})
                self._interaction_sent(nickname, kind)
            except SocialError as exc:
                self._error(exc)
            return
        if not self.current_room_id:
            self._set_status("请先选择一个共同房间，再向房间成员互动。", error=True)
            return
        event = {"room_id": self.current_room_id, "kind": kind, "target_id": target, "message": ""}
        thread = SocialEventThread(self.client, event, self)
        self._event_threads.append(thread)
        thread.completed.connect(lambda: self._interaction_sent(nickname, kind))
        thread.failed.connect(lambda message: self._set_status(f"互动没有送出：{social_user_message(SocialError(str(message), kind='network'))}", error=True))
        thread.finished.connect(lambda: self._event_thread_finished(thread))
        self._set_status(f"正在向 {nickname} {labels.get(kind, '送出互动')}…")
        thread.start()

    def _auto_accept_light_food_interactions(self) -> None:
        """欢迎互动时自动接下茶/蛋糕；专注优先只在本地空闲时接下。"""
        me = self.data.get("me") or {}
        mode = str(me.get("buddy_interaction_mode") or "focus_priority")
        if mode == "do_not_disturb":
            return
        snapshot = self._focus_snapshot
        if isinstance(snapshot, dict):
            working = str(snapshot.get("status") or "") == "focus"
        else:
            working = bool(getattr(snapshot, "is_running", False))
        if mode == "focus_priority" and working:
            return
        for visit in self.data.get("visits") or []:
            kind = str(visit.get("kind") or "")
            if kind not in {"food_tea", "food_cake"}:
                continue
            event_id = str(visit.get("id") or "")
            if not event_id or event_id in self._auto_accepting_food:
                continue
            self._auto_accepting_food.add(event_id)
            try:
                self.client.rpc("lili_respond_visit", {"event_id": event_id, "accept": True})
                self.food_interaction_accepted.emit(visit)
            except SocialError as exc:
                self._auto_accepting_food.discard(event_id)
                self._error(exc)
                continue
            self.refresh()

    def _send_food_interaction(self, buddy: dict[str, Any], kind: str) -> None:
        if not self._require_login():
            return
        self.food_interaction_requested.emit(buddy, kind)

    def _send_phrase(self, phrase: str) -> None:
        """Send one short room phrase to a selected/first peer."""

        if not self._require_login() or not self.current_room_id:
            self._set_status("请先加入一个共同房间，再发送房间短语。", error=True)
            return
        people = getattr(self, "_room_people", [])
        target = next((person for person in people if not person.get("is_self")), None)
        if target is None:
            self._set_status("当前房间还没有可接收短语的搭子。", error=True)
            return
        nickname = _owner_nickname(target)
        event = {"room_id": self.current_room_id, "kind": "phrase", "target_id": str(target.get("user_id") or ""), "message": phrase[:80]}
        thread = SocialEventThread(self.client, event, self)
        self._event_threads.append(thread)
        thread.completed.connect(lambda: self._interaction_sent(nickname, "phrase"))
        thread.failed.connect(lambda message: self._set_status(f"短语没有送出：{social_user_message(SocialError(str(message), kind='network'))}", error=True))
        thread.finished.connect(lambda: self._event_thread_finished(thread))
        self._set_status(f"正在向 {nickname} 发送“{phrase}”…")
        thread.start()

    def _set_focus_task(self) -> None:
        title, ok = QInputDialog.getText(self, "一次只盯一件事", "目标：", text="完成当前最重要的一件事")
        if not ok or not title.strip():
            return
        minutes, ok = QInputDialog.getInt(self, "任务倒计时", "距离截止还有多少分钟（0 表示不倒计时）：", 60, 0, 7 * 24 * 60, 5)
        if not ok:
            return
        self.focus_task_requested.emit(title.strip()[:120], minutes)
        self._set_status("本轮任务已保存到本机，计时和倒计时会共用这一项目标。")

    def _set_tomorrow_review(self) -> None:
        title, ok = QInputDialog.getText(self, "轻量复盘", "明天打开时最先做什么？")
        if not ok:
            return
        self.tomorrow_review_requested.emit(title.strip()[:160])
        self._set_status("明天第一件事已记在本机。")

    def _set_room_schedule(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        start, ok = QInputDialog.getText(self, "一起开工/收工", "开工时间（HH:MM）：", text="21:00")
        if not ok:
            return
        end, ok = QInputDialog.getText(self, "一起开工/收工", "收工时间（HH:MM）：", text="23:00")
        if not ok:
            return
        try:
            datetime.strptime(start.strip(), "%H:%M")
            datetime.strptime(end.strip(), "%H:%M")
        except ValueError:
            self._set_status("时间请填写成 HH:MM，例如 21:00。", error=True)
            return
        try:
            setter = getattr(self.client, "set_room_schedule", None)
            if callable(setter):
                setter(room_id=self.current_room_id, start_at=start.strip(), end_at=end.strip(), enabled=True)
            else:
                self.client.rpc("lili_set_room_schedule", {"p_room_id": self.current_room_id, "p_start_at": start.strip(), "p_end_at": end.strip(), "p_enabled": True})
            self._set_status(f"已设定 {start.strip()} 一起开工，{end.strip()} 一起收工。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _set_room_challenge(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        title, ok = QInputDialog.getText(self, "共同挑战", "挑战名称：", text="今晚一起完成 4 小时")
        if not ok or not title.strip():
            return
        hours, ok = QInputDialog.getInt(self, "共同挑战", "共同专注小时数：", 4, 1, 72, 1)
        if not ok:
            return
        rounds, ok = QInputDialog.getInt(self, "共同挑战", "每位成员至少完成几轮：", 3, 1, 30, 1)
        if not ok:
            return
        try:
            setter = getattr(self.client, "set_room_challenge", None)
            if callable(setter):
                setter(room_id=self.current_room_id, title=title.strip()[:80], target_seconds=hours * 3600, target_rounds=rounds)
            else:
                self.client.rpc("lili_set_room_challenge", {"p_room_id": self.current_room_id, "p_title": title.strip()[:80], "p_target_seconds": hours * 3600, "p_target_rounds": rounds})
            self._set_status("共同挑战已保存，完成时会写入房间动态。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _interaction_sent(self, nickname: str, kind: str) -> None:
        labels = {"poke": "戳了一下", "cheer": "送上加油", "drink": "递了一杯奶茶", "phrase": "发送了快速短语"}
        self._set_status(f"{PET_NAME}已向 {nickname} {labels.get(kind, '送出互动')}；对方房间动态会显示这次互动。")
        QTimer.singleShot(0, self._refresh_selected_room)

    def _event_thread_finished(self, thread: SocialEventThread) -> None:
        if thread in self._event_threads:
            self._event_threads.remove(thread)
        thread.deleteLater()

    def _render_room_people(self, people: list[dict[str, Any]]) -> None:
        if not hasattr(self, "room_members"):
            return
        self._room_people = list(people)
        self.room_members.clear()
        for buddy in people:
            item = QListWidgetItem()
            widget = RoomPetCardWidget(buddy, self.room_members)
            widget.interaction_requested.connect(self._send_interaction)
            widget.food_interaction_requested.connect(self._send_food_interaction)
            item.setData(Qt.ItemDataRole.UserRole, buddy)
            self.room_members.addItem(item)
            self.room_members.setItemWidget(item, widget)
            self._set_buddy_item_height(item, widget)
        if not people:
            empty = QListWidgetItem("加入房间后，这里会显示一起专注的六毛和累计时长。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.room_members.addItem(empty)
        self._fit_list_height(self.room_members, 120, 360)

    def _render_room_activity(self, entries: list[Any]) -> None:
        if not hasattr(self, "room_activity"):
            return
        self.room_activity.clear()
        # A room is a stage, not an audit log. Keep only the newest three
        # lightweight events; older history remains available from the server.
        for entry in entries[:3]:
            if isinstance(entry, dict):
                stamp = _format_beijing_time(str(entry.get("created_at") or ""))
                text = str(entry.get("text") or entry.get("message") or "")
                if not text:
                    actor = social_pet_label(
                        entry.get("actor_private_note_name")
                        or entry.get("owner_nickname")
                        or entry.get("nickname")
                        or entry.get("actor_nickname")
                    )
                    target = entry.get("target_private_note_name") or entry.get("target_owner_nickname") or entry.get("target_nickname")
                    target_text = f" → {social_pet_label(target)}" if target else ""
                    kind_text = {
                        "join": "进入房间", "leave": "离开房间", "focus_start": "开始专注",
                        "focus_pause": "暂停休息", "focus_finish": "完成一轮",
                        "poke": "戳了一下", "cheer": "送上加油", "drink": "递了一杯奶茶",
                        "phrase": "发送了快速短语", "challenge_complete": "完成了共同挑战",
                        "schedule_start": "一起开工", "schedule_end": "一起收工",
                        "goal_set": "设置了共同目标",
                    }.get(str(entry.get("kind")), "更新了状态")
                    text = f"{actor}{target_text} {kind_text}"
                if stamp:
                    text = f"{stamp}  {text}"
            else:
                text = str(entry)
            if text:
                self.room_activity.addItem(text)
        if self.room_activity.count() == 0:
            self.room_activity.addItem("房间动态会显示开始专注、完成一轮和六毛互动。")

    def _render_wealth_leaderboard(self, rows: list[Any]) -> None:
        if not hasattr(self, "wealth_leaderboard"):
            return
        self.wealth_leaderboard.clear()
        for index, row in enumerate(rows[:20], 1):
            if not isinstance(row, dict):
                continue
            nickname = social_pet_label(
                row.get("private_note_name") or row.get("nickname") or row.get("owner_nickname") or "搭子"
            )
            try:
                week_seconds = max(0, int(row.get("week_seconds") or row.get("period_seconds") or 0))
            except (TypeError, ValueError):
                week_seconds = 0
            self.wealth_leaderboard.addItem(
                f"{index}. {nickname}　本周专注 {format_work_duration(week_seconds)}"
            )
        if self.wealth_leaderboard.count() == 0:
            if self._leaderboard_error:
                self.wealth_leaderboard.addItem("本周专注排行榜暂时没有同步成功，请稍后重试。")
            elif not self._leaderboard_loaded:
                self.wealth_leaderboard.addItem("正在加载本周专注排行榜…")
            else:
                self.wealth_leaderboard.addItem("暂无可展示的榜单成员。")

    def _refresh_room_goal_text(self) -> None:
        if not hasattr(self, "room_goal"):
            return
        schedule = self._room_schedule_state
        if schedule:
            now_text = _beijing_now().strftime("%H:%M")
            for key, label in (("start_at", "一起开工"), ("end_at", "一起收工")):
                marker = f"{key}:{now_text}"
                if str(schedule.get(key) or "") == now_text and marker != self._last_ritual_notice:
                    self._last_ritual_notice = marker
                    self.room_ritual_due.emit(label)
        goal = self._room_goal_state
        if not goal:
            self.room_goal.setText("尚未设置共同目标；房间成员可以在这里设定任务和倒计时。")
            return
        title = str(goal.get("title") or "一起专注")
        target = int(goal.get("target_seconds") or goal.get("target_minutes", 0) * 60)
        completed = int(goal.get("completed_seconds") or goal.get("current_seconds") or 0)
        due = str(goal.get("due_at") or "")
        remaining = ""
        if due:
            try:
                due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.astimezone()
                seconds = max(0, int((due_dt - _beijing_now()).total_seconds()))
                remaining = f" · 倒计时 {format_work_duration(seconds)}"
            except ValueError:
                pass
        progress = f"{format_work_duration(completed)} / {format_work_duration(target)}" if target else "共同进行中"
        self.room_goal.setText(f"共同任务：{title} · {progress}{remaining}")

    def _set_room_goal(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        title, ok = QInputDialog.getText(self, "设置共同目标", "共同任务名称：", text="完成这一轮专注")
        if not ok or not title.strip():
            return
        minutes, ok = QInputDialog.getInt(self, "设置倒计时", "共同专注分钟数：", 50, 1, 24 * 60, 5)
        if not ok:
            return
        self._begin_action("正在保存共同任务…")
        try:
            due_at = (_beijing_now() + timedelta(minutes=minutes)).isoformat()
            setter = getattr(self.client, "set_room_goal", None)
            if callable(setter):
                setter(room_id=self.current_room_id, title=title.strip()[:80], target_seconds=minutes * 60, due_at=due_at)
            else:
                self.client.rpc("lili_set_room_goal", {"p_room_id": self.current_room_id, "p_title": title.strip()[:80], "p_target_seconds": minutes * 60, "p_due_at": due_at})
            self._end_action()
            self._set_status("共同任务已更新，房间成员会看到同一个倒计时。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _leave_room(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        room_id = self.current_room_id
        summary = self.data.get("room_summary") or (self.data.get("current_room") or {}).get("room_summary") or {}
        room_name = str((self.data.get("current_room") or {}).get("name") or "当前自习室")
        try:
            leaver = getattr(self.client, "leave_room", None)
            if callable(leaver):
                leaver(room_id=room_id)
            else:
                self.client.rpc("lili_leave_room", {"p_room_id": room_id})
            self.current_room_id = None
            self._room_selection_explicit = False
            self.room_changed.emit(None)
            self._set_status("已离开当前自习室，本次共同专注已保留在房间动态中。")
            if isinstance(summary, dict) and summary:
                QMessageBox.information(
                    self,
                    "本次自习室总结",
                    f"{room_name}\n\n"
                    f"今日共同专注：{format_work_duration(int(summary.get('today_shared_focus_seconds') or 0))}\n"
                    f"累计共同专注：{format_work_duration(int(summary.get('cumulative_shared_focus_seconds') or summary.get('shared_focus_seconds') or 0))}\n"
                    f"参与成员：{int(summary.get('member_count') or 0)} 人\n"
                    f"离开后可再次用房间码加入。",
                )
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _mine_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        self.account_stack = QStackedWidget()
        self.account_stack.setMinimumSize(0, 0)
        self.account_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.account_stack.addWidget(self._auth_card())
        self.account_stack.addWidget(self._profile_card())
        layout.addWidget(self.account_stack)
        preview_card, preview_layout = self._card(
            "登录后可以做什么",
            "账号只用于搭子与私人自习室；聊天、计时、动作和离线陪伴不登录也能使用。",
        )
        preview_layout.addWidget(QLabel("• 添加搭子并查看在线状态\n• 创建私人专注房间\n• 接收串门邀请并一起计时"))
        layout.addWidget(preview_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _auth_card(self) -> QWidget:
        card, layout = self._card(
            "账号",
            "邮箱只用于登录；密码不会保存在 Lili。网络暂时不可达时会显示最近状态，恢复后自动同步。",
        )
        auth_tabs = QTabWidget()
        auth_tabs.setMinimumSize(0, 0)
        auth_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        auth_tabs.tabBar().setExpanding(False)
        login = QWidget(); login_layout = QVBoxLayout(login); login_form = QFormLayout()
        login.setMinimumSize(0, 0)
        login.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.login_email = QLineEdit(); self.login_password = QLineEdit(); self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        login_form.addRow("邮箱", self.login_email); login_form.addRow("密码", self.login_password)
        login_layout.addLayout(login_form); self.login_button = QPushButton("登录")
        self.login_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.login_button.clicked.connect(self._login); login_layout.addWidget(self.login_button); login_layout.addStretch()
        self.forgot_password_button = QPushButton("忘记密码？")
        self.forgot_password_button.setObjectName("link")
        self.forgot_password_button.clicked.connect(self._request_password_reset)
        login_layout.addWidget(self.forgot_password_button)
        register = QWidget(); register_layout = QVBoxLayout(register); register_form = QFormLayout()
        register.setMinimumSize(0, 0)
        register.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.signup_nickname = QLineEdit(self.owner_nickname or "搭子"); self.signup_email = QLineEdit(); self.signup_password = QLineEdit(); self.signup_password.setEchoMode(QLineEdit.EchoMode.Password)
        register_form.addRow("主人称呼", self.signup_nickname); register_form.addRow("邮箱", self.signup_email); register_form.addRow("密码", self.signup_password)
        register_layout.addLayout(register_form); self.signup_button = QPushButton("注册")
        self.signup_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.signup_button.clicked.connect(self._signup); register_layout.addWidget(self.signup_button)
        self.signup_resend_button = QPushButton("重新发送确认邮件")
        self.signup_resend_button.setVisible(False)
        self.signup_resend_button.clicked.connect(self._resend_confirmation)
        register_layout.addWidget(self.signup_resend_button)
        register_layout.addWidget(QLabel("163、学校邮箱未收到时，请先查垃圾邮件/广告邮件。若邮箱已经注册，重复注册不会再次创建账号或覆盖密码，请切换到登录，或使用“忘记密码？”重置。"))
        register_layout.addStretch()
        auth_tabs.addTab(login, "登录"); auth_tabs.addTab(register, "注册")
        layout.addWidget(auth_tabs)
        return card

    def _profile_card(self) -> QWidget:
        card, layout = self._card("我的账号", "管理搭子码、可见性和串门权限。")
        self.identity = QLabel(); self.identity.setStyleSheet("font-size:18px;font-weight:650;"); self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        self.hidden = QCheckBox("隐身")
        self.exact = QCheckBox("显示准确时长")
        self.visits_allowed = QCheckBox("允许搭子串门")
        self.wealth_opt_in = QCheckBox("参加本周专注排行榜")
        self.wealth_opt_in.setChecked(True)
        self.wealth_opt_in.setToolTip("默认参加；仅已接受的搭子可见，可随时关闭。")
        layout.addWidget(self.hidden); layout.addWidget(self.exact); layout.addWidget(self.visits_allowed); layout.addWidget(self.wealth_opt_in)
        layout.addWidget(QLabel("搭子互动："))
        self.interaction_mode = QComboBox()
        self.interaction_mode.addItem("欢迎互动", "welcome")
        self.interaction_mode.addItem("专注优先（推荐）", "focus_priority")
        self.interaction_mode.addItem("免打扰", "do_not_disturb")
        self.interaction_mode.setMinimumWidth(0)
        self.interaction_mode.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.interaction_mode.setToolTip("决定好友敬茶、请吃蛋糕、请奶茶或邀请开工时如何到达你的六毛。")
        layout.addWidget(self.interaction_mode)
        save = QPushButton("保存隐私设置"); save.clicked.connect(self._save_profile)
        security = QPushButton("账号与安全…"); security.clicked.connect(self._open_account_security)
        logout = QPushButton("退出账号"); logout.clicked.connect(self._logout)
        for button in (save, security, logout):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout.addWidget(button)
        layout.addStretch()
        return card

    def _open_account_security(self) -> None:
        if not self._require_login():
            return
        email = self._account_email or str(getattr(self.client, "account_email", "") or "").strip()
        dialog = AccountSecurityDialog(self.client, email, self)
        dialog.logout_requested.connect(self._logout)
        dialog.account_deleted.connect(self._account_deleted_from_security)
        dialog.exec()

    def _account_deleted_from_security(self) -> None:
        self.client.sign_out()
        self.data = {}
        self._muted_buddy_ids.clear()
        self._account_email = ""
        self._update_account_state()
        self.account_state_changed.emit(False)
        self._set_status("账号已注销，六毛继续离线陪伴。")

    def _request_password_reset(self) -> None:
        initial = self.login_email.text().strip()
        dialog = PasswordResetDialog(self.client, initial, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._account_email = dialog.email
            self._update_account_state()
            self.account_state_changed.emit(True)
            self._set_status("密码已修改成功，现在可以使用新密码进入自习室。")
            self.refresh()

    def _password_reset_completed(self) -> None:
        self._end_action()
        self._set_status("如果该邮箱已注册，我们会向其发送密码重置邮件；请检查收件箱和垃圾邮件。")
        QMessageBox.information(self, "密码重置邮件已提交", "如果该邮箱已注册，我们会向其发送密码重置邮件。请检查收件箱和垃圾邮件。")

    def _password_reset_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _password_reset_thread_finished(self, thread: SocialPasswordResetThread) -> None:
        if self._password_reset_thread is thread:
            self._password_reset_thread = None
        if hasattr(self, "forgot_password_button"):
            self.forgot_password_button.setEnabled(True)
        thread.deleteLater()

    def _open_relogin(self) -> None:
        self.tabs.setCurrentIndex(3)
        if hasattr(self, "login_email"):
            self.login_email.setFocus()

    def _set_status(self, message: str, *, error: bool = False, relogin: bool = False) -> None:
        self.status_label.setText(message)
        color = "#a33a3a" if error else "#087f74"
        background = "#f7e5e5" if error else "#e1efec"
        self.status_label.setStyleSheet(f"background:{background};color:{color};border-radius:9px;padding:7px 10px;")
        self.relogin_button.setVisible(bool(relogin))

    def _begin_action(self, message: str) -> None:
        self._set_status(message)
        if QApplication.overrideCursor() is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    @staticmethod
    def _end_action() -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def _require_login(self) -> bool:
        if self.client.signed_in:
            return True
        self.tabs.setCurrentIndex(3)
        self._set_status("请先在“我的”页面登录；其他离线功能仍可正常使用。", error=True)
        return False

    def _update_account_state(self) -> None:
        self.account_stack.setCurrentIndex(1 if self.client.signed_in else 0)
        if not self.client.signed_in:
            self._fill_signed_out_placeholders()

    def _fill_signed_out_placeholders(self) -> None:
        self.buddies.clear(); self.buddies.addItem("登录后，这里会显示搭子的在线与专注状态。")
        self.inbox.clear(); self.inbox.addItem("登录后可接收搭子申请与串门邀请。")
        if hasattr(self, "inbox_accept_button"):
            self._update_inbox_actions(None, None)
        self.rooms.clear(); self.rooms.addItem("登录后可创建或加入私人自习室。")
        self._fit_list_height(self.buddies, 46, 360)
        self._fit_list_height(self.rooms, 52, 140)
        if hasattr(self, "room_members"):
            self._render_room_people([])
        if hasattr(self, "room_activity"):
            self._render_room_activity([])
        if hasattr(self, "wealth_leaderboard"):
            self._leaderboard_rows = []
            self._leaderboard_loaded = False
            self._leaderboard_error = False
            self._render_wealth_leaderboard(self._leaderboard_rows)

    def _update_inbox_actions(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """Only show actions that match the selected notification state."""

        if not hasattr(self, "inbox_accept_button"):
            return
        kind = ""
        if current is not None:
            payload = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(payload, tuple) and payload:
                kind = str(payload[0])
        incoming_buddy = kind == "buddy"
        outgoing_buddy = kind == "buddy_outgoing"
        self.inbox_accept_button.setVisible(incoming_buddy or kind in {"food", "visit", "achievement_witness"})
        self.inbox_reject_button.setVisible(incoming_buddy or kind in {"food", "visit", "achievement_witness"})
        self.inbox_cancel_button.setVisible(outgoing_buddy)

    def _error(self, exc: Exception) -> None:
        self._end_action()
        raw = str(exc)
        kind = str(getattr(exc, "kind", "") or "").casefold()
        error_code = str(getattr(exc, "error_code", "") or "").casefold()
        retryable = bool(getattr(exc, "retryable", False))
        is_auth = kind.startswith("auth") or error_code in {
            "refresh_token_already_used",
            "invalid_refresh_token",
            "invalid_grant",
            "email_not_confirmed",
        }
        LOGGER.warning(
            "social room operation failed kind=%s endpoint=%s status=%s retryable=%s: %s",
            getattr(exc, "kind", "unknown"),
            getattr(exc, "endpoint", ""),
            getattr(exc, "status", None),
            retryable,
            raw,
        )
        message = "共同房间状态保存失败，请稍后重试。" if "ambiguous" in raw.lower() or "room_id" in raw.lower() else social_user_message(exc)
        self._set_status(message, error=True, relogin=is_auth)
        if is_auth or retryable:
            return
        QMessageBox.warning(self, "六毛搭子自习室", message)

    def _signup(self) -> None:
        if self._signup_thread is not None and self._signup_thread.isRunning():
            return
        email = self.signup_email.text().strip()
        password = self.signup_password.text()
        nickname = self.signup_nickname.text().strip()
        self._begin_action("正在创建账号…")
        self.signup_button.setEnabled(False)
        thread = SocialSignupThread(self.client, email, password, nickname, self)
        self._signup_thread = thread
        thread.completed.connect(self._signup_completed)
        thread.failed.connect(self._signup_failed)
        thread.finished.connect(lambda: self._signup_thread_finished(thread))
        thread.start()

    def _signup_completed(self, result: object) -> None:
        self._end_action()
        if isinstance(result, SignupResult):
            self._pending_signup_email = result.email
            self.signup_resend_button.setVisible(
                result.confirmation_pending or (result.existing_account and not result.email_confirmed)
            )
            self.login_email.setText(result.email)
        if isinstance(result, SignupResult) and result.session_active:
            self._update_account_state()
            self.refresh()
            self.account_state_changed.emit(True)
            self._set_status("注册并登录成功，六毛自习室已准备好。")
            self._record_login_streak()
        elif isinstance(result, SignupResult) and result.existing_account:
            if result.email_confirmed:
                message = (
                    f"账号 {result.email} 已经注册并完成验证。\n\n"
                    "重复注册不会修改原账号密码，请切换到“登录”页，使用该邮箱最初设置的密码登录。"
                )
                title = "账号已存在"
                status = "该邮箱已注册，请使用原密码登录；重复注册不会修改已有密码。"
            else:
                message = (
                    f"账号 {result.email} 可能已经注册。\n\n"
                    "为保护账号隐私，服务器不会在这里直接透露确认状态。重复注册不会修改原账号密码，"
                    "请先使用该邮箱原来设置的密码登录；如果之前没有完成确认，请检查收件箱和垃圾邮件，"
                    "或点击“重新发送确认邮件”，完成确认后再登录。"
                )
                title = "账号可能已存在"
                status = "该邮箱可能已注册，请不要重复注册；先使用原密码登录或重新发送确认邮件。"
            self._set_status(status)
            QMessageBox.information(self, title, message)
        elif isinstance(result, SignupResult) and result.confirmation_pending:
            self._set_status("注册成功，确认邮件已提交；请点击邮件后回到这里登录。")
            QMessageBox.information(
                self,
                "注册成功，请确认邮箱",
                f"账号 {result.email} 已创建。\n\n"
                "请打开确认邮件中的链接。链接会跳转到六毛项目页面；这表示邮箱确认已完成，"
                "不是失败。然后回到 Lili，在“登录”页输入邮箱和密码即可。\n\n"
                "如果 163 或学校邮箱暂时没有收到，请检查垃圾邮件/广告邮件，稍后点击“重新发送确认邮件”。",
            )
        elif result:
            # Compatibility path for legacy backends that still return a
            # plain truthy value. SignupResult itself is handled explicitly
            # above so a no-session response cannot log the user in accidentally.
            self._update_account_state()
            self.refresh()
            self.account_state_changed.emit(True)
            self._set_status("注册并登录成功，六毛自习室已准备好。")
        else:
            self._set_status("注册请求已提交，请到邮箱确认后回来登录。")
            QMessageBox.information(
                self,
                "请确认邮箱",
                "注册请求已提交。请到邮箱完成确认，然后回到这里登录。\n\n"
                "确认页会打开六毛项目页面，不需要启动 localhost 服务。",
            )

    def _signup_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        if str(getattr(exc, "kind", "") or "").casefold() == "signup_timeout":
            self._pending_signup_email = self.signup_email.text().strip()
            self.login_email.setText(self._pending_signup_email)
            self.signup_resend_button.setVisible(bool(self._pending_signup_email))
        self._error(exc)

    def _signup_thread_finished(self, thread: SocialSignupThread) -> None:
        if self._signup_thread is thread:
            self._signup_thread = None
        self.signup_button.setEnabled(True)
        thread.deleteLater()

    def _resend_confirmation(self) -> None:
        if self._resend_thread is not None and self._resend_thread.isRunning():
            return
        email = (self._pending_signup_email or self.signup_email.text()).strip()
        self._begin_action("正在重新发送确认邮件…")
        self.signup_resend_button.setEnabled(False)
        thread = SocialResendConfirmationThread(self.client, email, self)
        self._resend_thread = thread
        thread.completed.connect(self._resend_completed)
        thread.failed.connect(self._resend_failed)
        thread.finished.connect(lambda: self._resend_thread_finished(thread))
        thread.start()

    def _resend_completed(self) -> None:
        self._end_action()
        self._set_status("确认邮件已重新提交，请稍后检查收件箱和垃圾邮件。")
        QMessageBox.information(
            self,
            "确认邮件已重发",
            "邮件已重新提交。163、学校邮箱可能需要几分钟；如果仍未收到，需要管理员为 Supabase Auth 配置自定义 SMTP。",
        )

    def _resend_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _resend_thread_finished(self, thread: SocialResendConfirmationThread) -> None:
        if self._resend_thread is thread:
            self._resend_thread = None
        self.signup_resend_button.setEnabled(True)
        thread.deleteLater()

    def _record_login_streak(self) -> None:
        if not self.client.signed_in:
            return
        if self._login_streak_thread is not None and self._login_streak_thread.isRunning():
            return
        thread = SocialLoginStreakThread(self.client, self)
        self._login_streak_thread = thread
        thread.completed.connect(self._login_streak_completed)
        thread.failed.connect(self._login_streak_failed)
        thread.finished.connect(lambda: self._login_streak_thread_finished(thread))
        thread.start()

    def _login_streak_completed(self, result: dict) -> None:
        payload = dict(result or {})
        self.login_streak_updated.emit(payload)
        try:
            days = max(0, int(payload.get("streak_days") or 0))
        except (TypeError, ValueError):
            days = 0
        if payload.get("newly_unlocked"):
            self._set_status("连续登录 3 天，已解锁新娃衣「三日连登搭子」！")
        elif payload.get("reward_unlocked"):
            self._set_status("连续登录奖励已解锁；当前连续登录 %d 天。" % days)

    def _login_streak_failed(self, error: object) -> None:
        # This is an optional reward side effect. Do not show a red auth error
        # after the user has already logged in successfully.
        LOGGER.info("login streak unavailable: %s", error)

    def _login_streak_thread_finished(self, thread: SocialLoginStreakThread) -> None:
        if self._login_streak_thread is thread:
            self._login_streak_thread = None
        thread.deleteLater()

    def _login(self) -> None:
        if self._login_thread is not None and self._login_thread.isRunning():
            return
        email = self.login_email.text().strip()
        password = self.login_password.text()
        if not email or not password:
            self._error(SocialError("请输入邮箱和密码。", kind="validation"))
            return
        self._begin_action("正在登录搭子自习室…")
        self.login_button.setEnabled(False)
        thread = SocialLoginThread(self.client, email, password, self)
        self._login_thread = thread
        thread.completed.connect(self._login_completed)
        thread.failed.connect(self._login_failed)
        thread.finished.connect(lambda: self._login_thread_finished(thread))
        thread.start()

    def _login_completed(self, result: object = None) -> None:
        self._end_action()
        self._account_email = self.login_email.text().strip()
        self._update_account_state()
        self.tabs.setCurrentIndex(0)
        self.refresh()
        payload = dict(result or {}) if isinstance(result, dict) else {}
        self.login_streak_updated.emit(payload)
        if payload.get("newly_unlocked"):
            self._set_status("登录成功；连续登录 3 天，已解锁新娃衣「三日连登搭子」！")
        else:
            self._set_status("登录成功，邮箱确认已完成。")
        self.account_state_changed.emit(True)

    def _login_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _login_thread_finished(self, thread: SocialLoginThread) -> None:
        if self._login_thread is thread:
            self._login_thread = None
        self.login_button.setEnabled(True)
        thread.deleteLater()

    def _logout(self) -> None:
        self.client.sign_out(); self.data = {}; self._muted_buddy_ids.clear(); self._update_account_state(); self.account_state_changed.emit(False); self._set_status("已退出账号，六毛继续离线陪伴。")

    def refresh(self) -> None:
        if not self._require_login(): return
        self._start_dashboard_refresh(self.current_room_id, "正在刷新搭子与专注状态…")

    def apply_dashboard(self, data: dict[str, Any] | None) -> None:
        """Render a dashboard already fetched by the background sync thread.

        Heartbeats run off the UI thread.  Previously the completed payload was
        only consumed for visit notifications, leaving the visible room cards
        on the previous (often resting) state until the user clicked refresh.
        """

        # A direct render can happen immediately after construction (for
        # example when the owner restores a cached snapshot). Do not let the
        # one-shot 50 ms bootstrap refresh arrive afterward and overwrite that
        # newer view with its older in-flight result.
        self._initial_refresh_timer.stop()
        previous_data = self.data
        self.data = dict(data or {})
        self._muted_buddy_ids = {
            str(item).strip()
            for item in (self.data.get("muted_buddy_ids") or [])
            if str(item).strip()
        }
        # Missing is not empty: heartbeat payloads may omit this optional RPC
        # while the room dashboard remains healthy.  Preserve the last known
        # board until an explicit ``leaderboard=[]`` arrives.
        if "leaderboard" in self.data:
            self._leaderboard_rows = list(self.data.get("leaderboard") or [])
            self._leaderboard_loaded = True
            self._leaderboard_error = False
            self._render_wealth_leaderboard(self._leaderboard_rows)
        me=self.data.get("me") or {}
        if not self.owner_nickname:
            self.owner_nickname = clean_owner_nickname(me.get("owner_nickname") or me.get("nickname"))
        me_presence = self.data.get("me_presence") or {}
        own_label = social_pet_label(self.owner_nickname or me.get("nickname"))
        self.identity.setText(f"{own_label} · 我的搭子码：{me.get('invite_code','--------')}")
        for request in self.data.get("requests") or []:
            if not isinstance(request, dict) or _notification_sender_id(request) in self._muted_buddy_ids:
                continue
            request_id = str(request.get("id") or "")
            if request_id and request_id not in self._seen_buddy_request_ids:
                self._seen_buddy_request_ids.add(request_id)
                self.buddy_request_received.emit(dict(request))
        self.hidden.setChecked(me.get("visibility") == "hidden"); self.exact.setChecked(bool(me.get("show_exact_time",True))); self.visits_allowed.setChecked(bool(me.get("allow_visits",True))); self.wealth_opt_in.setChecked(_wealth_leaderboard_enabled(me))
        mode = str(me.get("buddy_interaction_mode") or "focus_priority")
        mode_index = self.interaction_mode.findData(mode)
        self.interaction_mode.setCurrentIndex(mode_index if mode_index >= 0 else 1)
        self.buddies.clear()
        people=(self.data.get("buddies") or [])+(self.data.get("room_people") or [])
        seen=set()
        unique_people = []
        for buddy in people:
            if not isinstance(buddy, dict):
                continue
            buddy = dict(buddy)
            buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
            if buddy_id in seen:
                continue
            buddy["notifications_muted"] = bool(
                buddy.get("notifications_muted") or buddy_id in self._muted_buddy_ids
            )
            seen.add(buddy_id)
            unique_people.append(buddy)
        unique_people.sort(key=cmp_to_key(_compare_buddies))
        working_count = 0
        visible_total = 0
        for buddy in unique_people:
            if buddy.get("subscribed") and not buddy.get("notifications_muted"):
                previous_buddies = {
                    str(item.get("user_id")): item
                    for item in (previous_data.get("buddies") or [])
                    if isinstance(item, dict)
                }
                previous = previous_buddies.get(str(buddy.get("user_id")))
                if previous is not None and _presence_status(previous) != _presence_status(buddy):
                    state_text = "开始专注" if _presence_status(buddy) == "focus" else "结束专注"
                    self.buddy_subscription_notice.emit(f"{_owner_label(buddy)} {state_text}了。")
            is_stale = bool(buddy.get("stale_presence"))
            is_uncertain = bool(buddy.get("presence_uncertain"))
            # A transport outage must not turn the last peer state into an
            # offline event, but it also must not inflate current room totals.
            working_count += int(_presence_status(buddy) == "focus")
            duration = None if is_stale or is_uncertain else buddy.get("today_seconds")
            if duration is not None: visible_total += max(0, int(duration))
            item=QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,buddy); self.buddies.addItem(item)
            buddy_widget = BuddyCardWidget(buddy, self.buddies)
            buddy_widget.interaction_requested.connect(self._send_interaction)
            buddy_widget.food_interaction_requested.connect(self._send_food_interaction)
            buddy_widget.interaction_blocked.connect(lambda message: self._set_status(message, error=True))
            buddy_widget.subscription_requested.connect(self._set_subscription)
            self.buddies.setItemWidget(item, buddy_widget)
            self._set_buddy_item_height(item, buddy_widget)
        local_today = self._local_today_seconds()
        me_seconds = (
            local_today
            if local_today is not None
            else int(me_presence.get("today_seconds") or me.get("today_seconds") or 0)
        )
        self.study_summary.setText(
            f"现在 {working_count} 位搭子正在专注　·　"
            f"我的今日专注 {format_work_duration(me_seconds)}　·　"
            f"房间可见合计 {format_work_duration(visible_total)}"
        )
        self._refresh_own_focus_labels()
        if not seen:
            empty = QListWidgetItem("还没有搭子。点击上面的“用搭子码添加”，一起工作时这里会显示今天和本周的专注时长。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.buddies.addItem(empty)
        self._fit_list_height(self.buddies, 46, 360)
        self.inbox.clear()
        for request in self.data.get("requests") or []:
            if _notification_sender_id(request) in self._muted_buddy_ids:
                continue
            item=QListWidgetItem(f"搭子申请：{_owner_label(request)}"); item.setData(Qt.ItemDataRole.UserRole,("buddy",request)); self.inbox.addItem(item)
        for request in self.data.get("outgoing_requests") or []:
            if not isinstance(request, dict):
                continue
            item = QListWidgetItem(f"我发出的搭子申请：{_owner_label(request)}\n等待对方回应")
            item.setData(Qt.ItemDataRole.UserRole, ("buddy_outgoing", request))
            self.inbox.addItem(item)
        for visit in self.data.get("visits") or []:
            if _notification_sender_id(visit) in self._muted_buddy_ids:
                continue
            visit_kind = str(visit.get("kind") or "visit")
            labels = {
                "food_coffee": "☕ 一起开工邀请",
                "food_milk_tea": "🧋 一起休息邀请",
                "food_tea": "🍵 敬茶",
                "food_cake": "🍰 庆祝邀请",
                "food_cake_share": "🍰 请你一起吃蛋糕",
            }
            label = labels.get(visit_kind, "串门邀请")
            inbox_kind = "food" if visit_kind.startswith("food_") else "visit"
            item=QListWidgetItem(f"{label}：{_owner_label(visit)}"); item.setData(Qt.ItemDataRole.UserRole,(inbox_kind,visit)); self.inbox.addItem(item)
        if hasattr(self, "recent_interactions"):
            self.recent_interactions.clear()
            for share in self.data.get("cake_shares") or []:
                if not isinstance(share, dict):
                    continue
                members = [item for item in (share.get("members") or []) if isinstance(item, dict)]
                accepted = sum(str(item.get("status") or "") == "accepted" for item in members)
                total = len(members)
                message = str(share.get("message") or "今天值得庆祝一下")[:80]
                self.recent_interactions.addItem(
                    f"🍰 今日蛋糕 · 已邀请 {total} 人 · 已接受 {accepted}/{total}\n{message}"
                )
        for request in self.data.get("achievement_witness_requests") or []:
            title = str(request.get("name") or "未命名成果")[:90]
            owner = _owner_label(request)
            item = QListWidgetItem(f"成果见证：{owner} · {title} · 固定奖励 200 吉他拨片")
            item.setData(Qt.ItemDataRole.UserRole, ("achievement_witness", request))
            self.inbox.addItem(item)
        if self.inbox.count() == 0:
            empty = QListWidgetItem("当前没有待处理申请或串门，新的邀请会显示在这里。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.inbox.addItem(empty)
        self._update_inbox_actions(self.inbox.currentItem(), None)
        QTimer.singleShot(0, self._auto_accept_light_food_interactions)
        rooms = list(self.data.get("rooms") or [])
        previous_room_id = self.current_room_id
        room_was_selected = bool(previous_room_id)
        self._applying_dashboard = True
        # Rebuilding the list is an internal render operation.  Suppress the
        # transient "selection cleared" and "selection restored" signals;
        # otherwise each dashboard response schedules another network sync.
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for room in rooms:
            room_item = QListWidgetItem(
                f"{room.get('name')} · {room.get('members')} 人"
            )
            room_item.setData(Qt.ItemDataRole.UserRole, room)
            self.rooms.addItem(room_item)
        if self.rooms.count() == 0:
            empty_room = QListWidgetItem("还没有私人自习室；创建后可把房间码发给搭子。")
            empty_room.setFlags(Qt.ItemFlag.NoItemFlags); self.rooms.addItem(empty_room)
            self.current_room_id = None
            self._room_selection_explicit = False
        else:
            # A server membership is not an active desktop selection.  The
            # user must explicitly choose a room after opening the window.
            selected = -1
            if self._room_selection_explicit and previous_room_id:
                for index, room in enumerate(rooms):
                    if self._room_id_from_payload(room) == previous_room_id:
                        selected = index
                        break
            if selected >= 0:
                self.rooms.setCurrentRow(selected)
                self.current_room_id = self._room_id_from_payload(rooms[selected])
            else:
                self.rooms.setCurrentRow(-1)
                self.current_room_id = None
        self.rooms.blockSignals(False)
        self._fit_list_height(self.rooms, 52, 140)
        self._applying_dashboard = False
        if self.current_room_id != previous_room_id:
            self.room_changed.emit(self.current_room_id)
        # The room-scoped endpoint is authoritative for members and events.
        # Keep the legacy top-level fields as a compatibility fallback for
        # older proxy deployments and the offline UI tests.
        room_detail = self.data.get("current_room") or {}
        if not isinstance(room_detail, dict):
            room_detail = {}
        if self.current_room_id and self.current_room_id != previous_room_id and not room_detail:
            self._room_refresh_timer.start(0)
        room_people = list(room_detail.get("room_people") or self.data.get("room_people") or []) if self.current_room_id else []
        server_timestamp = str(self.data.get("server_timestamp") or self.data.get("_server_timestamp") or "")
        if server_timestamp:
            room_people = [{**person, "_server_timestamp": server_timestamp} for person in room_people]
        # Always render the local member as well.  The old SQL function only
        # returned peers, which made the room look like everybody was resting
        # when the local timer was the only state visible in the UI.
        local_status = self._focus_snapshot
        local_presence = dict(me_presence)
        if isinstance(local_status, dict):
            local_presence = {**local_presence, **local_status}
        elif local_status is not None:
            local_presence = {
                **local_presence,
                "status": getattr(local_status, "status", "idle"),
                "working": bool(getattr(local_status, "is_running", False)),
                "session_seconds": int(getattr(local_status, "session_seconds", 0)),
                "today_seconds": int(getattr(local_status, "today_seconds", 0)),
            }
        if isinstance(self._focus_analytics, dict):
            local_presence.update({
                "today_interruptions": int(self._focus_analytics.get("today_interruptions") or 0),
                "longest_continuous_seconds": int(self._focus_analytics.get("longest_continuous_seconds") or 0),
            })
        local_presence.update({
            "user_id": str(me.get("user_id") or me.get("id") or "me"),
            "owner_nickname": self.owner_nickname or clean_owner_nickname(me.get("owner_nickname") or me.get("nickname")),
            "nickname": self.owner_nickname or str(me.get("nickname") or "搭子"),
            # The profile is the durable same-account outfit.  Presence is a
            # per-device heartbeat and can briefly belong to an older client.
            "outfit_key": str(me.get("outfit_key") or me_presence.get("outfit_key") or self.outfit_key or ""),
            "online": True,
            "is_self": True,
        })
        if self.current_room_id:
            room_people = [local_presence] + [p for p in room_people if str(p.get("user_id")) != str(local_presence.get("user_id"))]
        else:
            room_people = []
        self._render_room_people(room_people)
        goal = room_detail.get("room_goal") or self.data.get("room_goal") or {}
        summary = room_detail.get("room_summary") or self.data.get("room_summary") or {}
        if isinstance(summary, dict) and summary:
            self.room_summary.setText(_room_focus_summary_text(summary, len(room_people)))
        elif hasattr(self, "room_summary"):
            self.room_summary.setText("你当前没有加入工作间。创建工作间或输入房间码加入后，这里才会显示共同状态。")
        self._room_goal_state = dict(goal) if isinstance(goal, dict) else {}
        schedule = room_detail.get("room_schedule") or self.data.get("room_schedule") or {}
        challenge = room_detail.get("room_challenge") or self.data.get("room_challenge") or {}
        self._room_schedule_state = dict(schedule) if isinstance(schedule, dict) else {}
        self._room_challenge_state = dict(challenge) if isinstance(challenge, dict) else {}
        self.room_goal_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_schedule_button"):
            self.room_schedule_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_challenge_button"):
            self.room_challenge_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_start_prompt_button"):
            self.room_start_prompt_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_invite_button"):
            self.room_invite_button.setEnabled(bool(self.current_room_id))
        self.room_leave_button.setEnabled(bool(self.current_room_id))
        self._refresh_room_goal_text()
        if hasattr(self, "room_ritual"):
            if self._room_schedule_state:
                self.room_ritual.setText(
                    f"共同开工/收工：{self._room_schedule_state.get('start_at', '--:--')} 开工 · "
                    f"{self._room_schedule_state.get('end_at', '--:--')} 收工"
                )
            else:
                self.room_ritual.setText("共同开工/收工：未设置")
        if hasattr(self, "room_challenge"):
            if self._room_challenge_state:
                self.room_challenge.setText(
                    f"共同挑战：{self._room_challenge_state.get('title', '一起完成')} · "
                    f"{format_work_duration(int(self._room_challenge_state.get('target_seconds') or 0))} · "
                    f"每人 {int(self._room_challenge_state.get('target_rounds') or 0)} 轮"
                )
            else:
                self.room_challenge.setText("共同挑战：未设置")
        activity = list(room_detail.get("room_activity") or self.data.get("room_activity") or self.data.get("activity") or [])
        me_id = str(me.get("user_id") or me.get("id") or "")
        for event in activity:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            target_id = str(event.get("target_id") or "")
            if event_id and event_id not in self._seen_room_event_ids:
                self._seen_room_event_ids.add(event_id)
                is_target = target_id == me_id or (
                    not target_id and str(event.get("target_owner_nickname") or event.get("target_nickname") or "") == str(me.get("owner_nickname") or me.get("nickname") or "")
                )
                actor_id = str(event.get("actor_id") or "")
                if (
                    not self.data.get("_sync_offline")
                    and me_id
                    and is_target
                    and actor_id != me_id
                    and actor_id not in self._muted_buddy_ids
                ):
                    self.room_event_received.emit(dict(event))
        self._render_room_activity(activity)
        active = [
            item for item in (self.data.get("active_visits") or [])
            if _notification_sender_id(item) not in self._muted_buddy_ids
        ]
        if active and not self.data.get("_sync_offline"): self.active_visit.emit(active[0])
        state = str(self.data.get("_connection_state") or "")
        if self.data.get("_room_endpoint_unavailable"):
            self._set_status("账号与搭子已同步，但当前部署缺少自习室详情接口；隐私设置仍已保存。", error=False)
        elif self.data.get("_presence_grace_active") or state == "DEGRADED":
            self._set_status(
                "自习室连接暂时不稳定，搭子最近状态仍保留显示；正在自动恢复实时同步。"
            )
        elif self.data.get("_sync_offline") or state == "OFFLINE":
            age = int(self.data.get("_sync_age_minutes") or 0)
            age_text = f"约 {age} 分钟前" if age else "刚才"
            # Local focus is independent from room synchronization.  A focus
            # click can legitimately happen while the user has not selected a
            # room, so an unavailable dashboard must not make the local timer
            # look broken or claim that a room connection is required.
            if room_was_selected or self.current_room_id:
                self._set_status(
                    f"当前无法连接自习室，已显示{age_text}的本地状态；网络恢复后会自动同步。"
                )
            else:
                local_status = self._focus_snapshot
                if isinstance(local_status, dict):
                    local_focus_active = str(local_status.get("status") or "") == "focus"
                else:
                    local_focus_active = bool(getattr(local_status, "is_running", False))
                if local_focus_active:
                    self._set_status(
                        "本地专注已开始；你还没有加入自习室，搭子状态会在网络恢复后自动同步。"
                    )
                else:
                    self._set_status(
                        "你还没有加入自习室；本地功能不受影响，联网后搭子状态会自动同步。"
                    )
        elif state == "DEGRADED":
            self._set_status("自习室已连接，实时同步暂时不可用，继续重新连接。")
        elif state == "ONLINE":
            self._set_status("自习室已连接，房间状态已同步。")
        else:
            self._set_status("已刷新，页面内容是最新的。")

    def _save_profile(self) -> None:
        if not self._require_login(): return
        self._begin_action("正在保存隐私设置…")
        try:
            me=self.data.get("me") or {}
            self.client.update_profile(nickname=str(self.owner_nickname or me.get("nickname") or "搭子"),visibility="hidden" if self.hidden.isChecked() else "friends",show_exact_time=self.exact.isChecked(),allow_visits=self.visits_allowed.isChecked(),outfit_key=self.outfit_key,wealth_leaderboard_enabled=self.wealth_opt_in.isChecked(),wealth_leaderboard_preference_set=True)
            self.client.rpc("lili_set_buddy_interaction_mode", {"p_mode": str(self.interaction_mode.currentData() or "focus_priority")})
            self.refresh()
        except SocialError as exc: self._error(exc)

    def _set_subscription(self, buddy: dict[str, Any], enabled: bool) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        try:
            setter = getattr(self.client, "set_buddy_subscription", None)
            muted = bool(buddy.get("notifications_muted") or buddy_id in self._muted_buddy_ids)
            if callable(setter):
                setter(buddy_id=buddy_id, on_focus_start=enabled, on_focus_end=enabled, muted=muted)
            else:
                self.client.rpc("lili_set_buddy_subscription", {"p_buddy_id": buddy_id, "p_on_focus_start": enabled, "p_on_focus_end": enabled, "p_muted": muted})
            self._set_status("搭子状态订阅已开启。" if enabled else "搭子状态订阅已关闭。")
        except SocialError as exc:
            self._error(exc)

    def _set_buddy_muted(self, buddy: dict[str, Any], muted: bool) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        try:
            subscribed = bool(buddy.get("subscribed"))
            setter = getattr(self.client, "set_buddy_subscription", None)
            if callable(setter):
                setter(
                    buddy_id=buddy_id,
                    on_focus_start=subscribed,
                    on_focus_end=subscribed,
                    muted=bool(muted),
                )
            else:
                self.client.rpc(
                    "lili_set_buddy_subscription",
                    {
                        "p_buddy_id": buddy_id,
                        "p_on_focus_start": subscribed,
                        "p_on_focus_end": subscribed,
                        "p_muted": bool(muted),
                    },
                )
            if muted:
                self._muted_buddy_ids.add(buddy_id)
            else:
                self._muted_buddy_ids.discard(buddy_id)
            self._set_status("已开启消息免打扰。" if muted else "已关闭消息免打扰。")
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _remove_buddy(self, buddy: dict[str, Any]) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        answer = QMessageBox.question(
            self,
            "删除搭子",
            f"确定删除“{_owner_label(buddy)}”吗？\n\n双方的搭子关系和通知设置会删除，但不会删除你自己的待办、专注记录或聊天记录。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._begin_action("正在删除搭子关系…")
        try:
            self.client.rpc("lili_remove_buddy", {"p_buddy_id": buddy_id})
            self._muted_buddy_ids.discard(buddy_id)
            self._end_action()
            self._set_status("搭子已删除。")
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _buddy_context_menu(self, position) -> None:
        """Keep private labels in the buddy list, where their scope is clear."""

        item = self.buddies.itemAt(position)
        if item is None:
            return
        buddy = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(buddy, dict) or buddy.get("is_self"):
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        menu = QMenu(self)
        edit = menu.addAction("修改私人备注…")
        if str(buddy.get("private_note_name") or "").strip():
            clear = menu.addAction("清空私人备注")
        else:
            clear = None
        menu.addSeparator()
        muted = bool(buddy.get("notifications_muted") or buddy_id in self._muted_buddy_ids)
        mute = menu.addAction("关闭消息免打扰" if muted else "消息免打扰")
        menu.addSeparator()
        remove = menu.addAction("删除搭子")
        chosen = menu.exec(self.buddies.viewport().mapToGlobal(position))
        if chosen is edit:
            self._edit_buddy_private_note(buddy)
        elif clear is not None and chosen is clear:
            self._save_buddy_private_note(buddy, "")
        elif chosen is mute:
            self._set_buddy_muted(buddy, not muted)
        elif chosen is remove:
            self._remove_buddy(buddy)

    def _edit_buddy_private_note(self, buddy: dict[str, Any]) -> None:
        current = str(buddy.get("private_note_name") or "").strip()
        value, accepted = QInputDialog.getText(
            self,
            "修改搭子备注",
            "仅你可见的备注名：",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if accepted:
            self._save_buddy_private_note(buddy, value)

    def _update_private_note_snapshot(self, buddy_id: str, note: str) -> None:
        """Update every local projection so the label is immediately consistent."""

        def update(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                ids = {
                    str(item.get(field))
                    for field in ("user_id", "buddy_id", "peer_id", "sender_id", "receiver_id")
                    if item.get(field) is not None
                }
                if buddy_id in ids:
                    if note:
                        item["private_note_name"] = note
                    else:
                        item.pop("private_note_name", None)

        for key in ("buddies", "room_people", "active_visits", "visits", "requests"):
            update(self.data.get(key))
        current_room = self.data.get("current_room")
        if isinstance(current_room, dict):
            for key in ("room_people", "active_visits", "visits"):
                update(current_room.get(key))

    def _save_buddy_private_note(self, buddy: dict[str, Any], value: str) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        note = str(value or "").strip()[:40]
        self._begin_action("正在保存私人备注…")
        try:
            self.client.rpc(
                "lili_set_buddy_private_note",
                {"p_buddy_id": buddy_id, "p_private_note_name": note},
            )
            self._update_private_note_snapshot(buddy_id, note)
            self._end_action()
            self.apply_dashboard(self.data)
            self._set_status("私人备注已保存；只有你能看到。" if note else "私人备注已清空；对方昵称保持不变。")
        except SocialError as exc:
            self._error(exc)

    def _add_buddy(self) -> None:
        if not self._require_login():
            return
        dialog = BuddyCodeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        code = dialog.code
        if len(code) != 8:
            self._error(SocialError("请输入 8 位搭子码。", kind="validation"))
            return
        self._begin_action("正在查找搭子…")
        thread = SocialBuddyRpcThread(self.client, "lili_lookup_buddy_by_code", {"code": code}, self)
        self._buddy_rpc_threads.append(thread)
        thread.completed.connect(lambda payload, value=code: self._buddy_lookup_completed(value, payload))
        thread.failed.connect(self._buddy_rpc_failed)
        thread.finished.connect(lambda current=thread: self._buddy_rpc_finished(current))
        thread.start()

    def _buddy_lookup_completed(self, code: str, payload: object) -> None:
        self._end_action()
        if not isinstance(payload, dict):
            self._error(SocialError("搭子资料返回异常，请稍后重试。", kind="network", retryable=True))
            return
        state = str(payload.get("state") or "available")
        owner = _owner_label(payload)
        if state == "self":
            self._set_status("这是你自己的六毛，不能添加自己。", error=True)
            return
        if state == "accepted":
            self._set_status(f"{owner}已经是你的搭子。")
            return
        if state == "pending":
            self._set_status(
                f"查找完成：你之前已经向{owner}发送过申请；本次没有重复发送。"
                "请到“互动”页查看或撤回。"
            )
            return
        if state == "incoming":
            self.tabs.setCurrentIndex(1)
            self._set_status(f"{owner}已经向你发送申请，请到“互动”页处理。")
            return

        profile_text = f"找到：{owner}"
        nickname = _owner_nickname(payload)
        if nickname and nickname != owner.replace("家的六毛", ""):
            profile_text += f"\n昵称：{nickname}"
        outfit = str(payload.get("outfit_key") or "").strip()
        if outfit:
            profile_text += f"\n娃衣：{outfit[:60]}"
        profile_text += "\n\n确认后才会发送搭子申请。"
        confirm = BuddyProfileDialog(profile_text, self)
        if confirm.exec() == QDialog.DialogCode.Accepted:
            self._send_buddy_request(code)

    def _send_buddy_request(self, code: str) -> None:
        self._begin_action("正在发送搭子申请…")
        thread = SocialBuddyRpcThread(self.client, "lili_add_buddy_by_code", {"code": code}, self)
        self._buddy_rpc_threads.append(thread)
        thread.completed.connect(self._buddy_request_completed)
        thread.failed.connect(self._buddy_rpc_failed)
        thread.finished.connect(lambda current=thread: self._buddy_rpc_finished(current))
        thread.start()

    def _buddy_request_completed(self, payload: object) -> None:
        self._end_action()
        state = str(payload.get("state") or "pending") if isinstance(payload, dict) else "pending"
        owner = _owner_label(payload if isinstance(payload, dict) else {})
        if state == "accepted":
            message = f"{owner}已经是你的搭子，无需重复添加。"
        elif state == "incoming":
            self.tabs.setCurrentIndex(1)
            message = f"{owner}已经向你发送申请，请到“互动”页处理。"
        elif state == "pending":
            message = f"已向{owner}发送搭子申请，等待对方回应。"
        else:
            message = "搭子申请状态已更新，请刷新互动页。"
        self._set_status(message)
        self.refresh()

    def _buddy_rpc_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _buddy_rpc_finished(self, thread: SocialBuddyRpcThread) -> None:
        if thread in self._buddy_rpc_threads:
            self._buddy_rpc_threads.remove(thread)
        thread.deleteLater()

    def _send_visit(self) -> None:
        if not self._require_login(): return
        item=self.buddies.currentItem()
        if not item: return self._error(SocialError("请先选择一位搭子。"))
        buddy = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(buddy, dict): return self._error(SocialError("请先选择一位搭子。"))
        self._begin_action("六毛正在准备出发…")
        try:
            self.client.rpc("lili_send_visit",{"target":buddy["user_id"],"visit_kind":"visit"}); self._end_action(); self._set_status("六毛已经出发，等待对方接受串门。"); QMessageBox.information(self,"已出发","六毛已经出发，等待对方接受串门。")
        except SocialError as exc: self._error(exc)
    def _accept_inbox(self) -> None:
        if not self._require_login(): return
        item=self.inbox.currentItem()
        if not item: return self._error(SocialError("请先选择一项申请或串门。"))
        kind,data=item.data(Qt.ItemDataRole.UserRole)
        if kind == "buddy_outgoing":
            return self._set_status("这是你发出的申请，请等待对方回应或选择撤回。")
        self._begin_action("正在处理选中的申请…")
        try:
            if kind=="buddy":
                self.client.rpc("lili_respond_buddy",{"request_id":data["id"],"accept":True})
            elif kind == "achievement_witness":
                self.client.rpc("lili_respond_achievement_witness", {"p_achievement_id": data["achievement_id"], "p_accept": True})
            else:
                self.client.rpc("lili_respond_visit",{"event_id":data["id"],"accept":True})
                if kind == "food":
                    self.food_interaction_accepted.emit(data)
            self.refresh()
        except SocialError as exc: self._error(exc)

    def _reject_inbox(self) -> None:
        if not self._require_login():
            return
        item = self.inbox.currentItem()
        if item is None:
            return self._error(SocialError("请先选择一项申请或串门。"))
        kind, data = item.data(Qt.ItemDataRole.UserRole)
        if kind == "buddy_outgoing":
            return self._cancel_buddy_request()
        self._begin_action("正在处理选中的申请…")
        try:
            if kind == "buddy":
                self.client.rpc("lili_respond_buddy", {"request_id": data["id"], "accept": False})
            elif kind == "achievement_witness":
                self.client.rpc("lili_respond_achievement_witness", {"p_achievement_id": data["achievement_id"], "p_accept": False})
            else:
                self.client.rpc("lili_respond_visit", {"event_id": data["id"], "accept": False})
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _cancel_buddy_request(self) -> None:
        if not self._require_login():
            return
        item = self.inbox.currentItem()
        if item is None:
            return self._error(SocialError("请先选择一条我发出的搭子申请。", kind="validation"))
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 2 or payload[0] != "buddy_outgoing":
            return self._error(SocialError("当前选中项不是待撤回的搭子申请。", kind="validation"))
        data = payload[1] if isinstance(payload[1], dict) else {}
        request_id = str(data.get("id") or "")
        if not request_id:
            return self._error(SocialError("搭子申请编号缺失，请刷新互动页。", kind="validation"))
        self._begin_action("正在撤回搭子申请…")
        try:
            self.client.rpc("lili_cancel_buddy_request", {"request_id": request_id})
            self._end_action()
            self._set_status("搭子申请已撤回。")
            self.refresh()
        except SocialError as exc:
            self._error(exc)
    def _create_room(self) -> None:
        if not self._require_login(): return
        name,ok=QInputDialog.getText(self,"创建自习室","自习室名称：",text="安静工作间")
        if ok and name:
            self._begin_action("正在创建自习室…")
            try: self.client.rpc("lili_create_room",{"room_name":name}); self.refresh(); self._set_status("自习室已创建，可以分享房间码了。")
            except SocialError as exc: self._error(exc)
    def _join_room(self) -> None:
        if not self._require_login(): return
        code,ok=QInputDialog.getText(self,"加入自习室","输入 8 位房间码：")
        if ok and code:
            self._begin_action("正在加入自习室…")
            try: self.client.rpc("lili_join_room",{"code":code}); self.refresh(); self._set_status("已加入自习室。")
            except SocialError as exc: self._error(exc)

