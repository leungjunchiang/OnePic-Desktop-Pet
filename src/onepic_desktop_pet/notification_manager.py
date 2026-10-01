"""统一被动通知：主线程分发、启动/断线历史静默、显示前全屏拦截、单窗去重及本地诊断。"""
from __future__ import annotations

from datetime import timedelta
from .time_service import now_beijing, parse_server_datetime
from PySide6.QtCore import QObject, QTimer, QThread, Signal, Qt
from .buddy_reminder_toast import BuddyReminderToast
from .lifecycle_log import lifecycle_log

# 覆盖现有 90 秒低频 dashboard，同时拒绝断线积压的旧即时提示。
NOTIFICATION_FRESHNESS_SECONDS = 120


class NotificationManager(QObject):
    _queued_notify = Signal(object)
    _queued_observe = Signal(object)
    _queued_flush = Signal()

    def __init__(self, parent, *, blocked=lambda: False, foreground=lambda: False, inline=None):
        super().__init__(parent)
        self.blocked, self.foreground, self.inline = blocked, foreground, inline
        self.started_at = now_beijing()
        self.baselines = {}
        self.shown_event_ids = set()
        self.pending = {}
        self.current = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.flush)
        self._queued_notify.connect(lambda args: self.notify(**args), Qt.ConnectionType.QueuedConnection)
        self._queued_observe.connect(lambda args: self.observe(**args), Qt.ConnectionType.QueuedConnection)
        self._queued_flush.connect(self.flush, Qt.ConnectionType.QueuedConnection)

    def is_fresh(self, created_at, *, observed=None, baseline=None):
        created = parse_server_datetime(created_at)
        observed = observed or now_beijing()
        return bool(created and (baseline or self.started_at) < created <= observed
                    and observed - created <= timedelta(seconds=NOTIFICATION_FRESHNESS_SECONDS))

    def observe(self, channel, rows, *, timestamp="created_at", notify=None):
        """首次成功批次静默；过期积压只留收件箱，不在重连后重播。"""
        if QThread.currentThread() != self.thread():
            self._queued_observe.emit(dict(channel=channel, rows=rows, timestamp=timestamp, notify=notify))
            return
        if not isinstance(rows, list):
            return
        observed = now_beijing()
        initial = channel not in self.baselines
        baseline = self.baselines.setdefault(channel, observed)
        if initial:
            lifecycle_log("notification.baseline", channel=channel, count=len(rows))
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("event_id") or row.get("id") or "").strip()
            created = row.get(timestamp)
            if identifier and self.is_fresh(created, observed=observed, baseline=baseline) and notify is not None:
                notify(row)

    def notify(self, event_id, title, detail, callback, *, duration_ms=3000, display=None, created_at=None, event_type=None):
        if QThread.currentThread() != self.thread():
            self._queued_notify.emit(dict(event_id=event_id, title=title, detail=detail, callback=callback,
                duration_ms=duration_ms, display=display, created_at=created_at, event_type=event_type))
            return True
        identifier = str(event_id or "").strip()
        event_type = str(event_type or identifier.split(":", 1)[0])
        reason = ""
        if not identifier or identifier.endswith(":"):
            reason = "missing_id"
        elif identifier in self.shown_event_ids:
            reason = "duplicate"
        else:
            # 勿扰、全屏、过期也消费展示资格；历史/未读状态由业务存储独立维护。
            self.shown_event_ids.add(identifier)
            if not str(title or "").strip() or not str(detail or "").strip():
                reason = "empty"
            elif created_at is not None and not self.is_fresh(created_at):
                reason = "history_or_expired"
            elif self.blocked():
                reason = "fullscreen_game_or_dnd"
        lifecycle_log("notification.received", event_id=identifier, event_type=event_type,
                      creator="NotificationManager.notify", suppressed=bool(reason), reason=reason)
        if reason:
            return False
        if display is not None:
            self.pending.clear()
            self.timer.stop()
            self.close_current()
            display()
            lifecycle_log("notification.inline", event_id=identifier, event_type=event_type)
            return True
        self.pending[identifier] = (str(title), str(detail), callback, duration_ms, event_type)
        if not self.timer.isActive():
            self.timer.start()
        return True

    def flush(self):
        if QThread.currentThread() != self.thread():
            self._queued_flush.emit()
            return
        self.timer.stop()
        identifiers = list(self.pending)
        entries = list(self.pending.values())
        self.pending.clear()
        if not entries:
            return
        if self.blocked():
            lifecycle_log("notification.suppressed", event_ids=identifiers, reason="fullscreen_game_or_dnd", creator="NotificationManager.flush")
            return
        title = entries[0][0] if len(entries) == 1 else f"六毛 · {len(entries)} 条新互动"
        detail = entries[0][1] if len(entries) == 1 else "\n".join(e[0] for e in entries[:3])
        callback = entries[-1][2]
        if self.foreground() and self.inline is not None:
            self.inline(title + "\n" + detail)
            lifecycle_log("notification.in_app", event_ids=identifiers)
            return
        self.close_current()
        # 必须先检查再构造：被拦截时连临时 QWidget 都不创建。
        toast = BuddyReminderToast(title, detail, remaining_ms=max(e[3] for e in entries), parent=self.parent())
        self.current = toast
        toast._notification_event_ids = identifiers
        toast._notification_event_types = [entry[4] for entry in entries]
        toast.open_requested.connect(callback)
        toast.dismissed.connect(lambda: self._dismissed(toast))
        toast.show_passive()
        if not toast.isVisible():
            self.current = None
            lifecycle_log("notification.suppressed", event_ids=identifiers, reason="late_preflight", creator="NotificationManager.flush")
            toast.deleteLater()
            return
        lifecycle_log("notification.create", toast, event_ids=identifiers, creator="NotificationManager.flush",
                      window_id=int(toast.effectiveWinId()), event_types=toast._notification_event_types, noactivate=True,
                      width=toast.width(), height=toast.height())

    def _dismissed(self, toast):
        if self.current is toast:
            self.current = None
        lifecycle_log("notification.destroy", toast, event_ids=getattr(toast, "_notification_event_ids", []),
                      window_id=int(toast.effectiveWinId()))
        toast.deleteLater()

    def close_current(self):
        toast, self.current = self.current, None
        if toast is not None:
            toast.close()

    def clear_all(self):
        self.suspend_display()
        self.baselines.clear()
        self.shown_event_ids.clear()

    def suspend_display(self):
        """只销毁展示；保留基线和已消费 ID，退出全屏不补弹。"""
        self.timer.stop()
        self.pending.clear()
        self.close_current()
