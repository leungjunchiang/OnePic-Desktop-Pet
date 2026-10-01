"""统一即时通知入口：首次成功读取建基线、稳定 ID 去重、最多一个无焦点窗口；瞬时互动复用六毛四秒气泡。"""
from __future__ import annotations

from .time_service import now_beijing, parse_server_datetime
from PySide6.QtCore import QObject, QTimer
from .buddy_reminder_toast import BuddyReminderToast


class NotificationManager(QObject):
    def __init__(self, parent, *, blocked=lambda: False, foreground=lambda: False, inline=None):
        super().__init__(parent)
        self.blocked, self.foreground, self.inline = blocked, foreground, inline
        self.baselines = {}
        self.shown_event_ids = set()
        self.pending = {}
        self.current = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.flush)

    def observe(self, channel, rows, *, timestamp="created_at", notify=None):
        """缺字段/离线失败不建立基线；首批全部静默，以后只处理基线后的事件。"""
        if not isinstance(rows, list):
            return
        observed = now_beijing()
        baseline = self.baselines.setdefault(channel, observed)
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("event_id") or row.get("id") or "").strip()
            created = parse_server_datetime(row.get(timestamp))
            if identifier and created and baseline < created <= observed and notify is not None:
                notify(row)

    def notify(self, event_id, title, detail, callback, *, duration_ms=3000, display=None):
        identifier = str(event_id or "").strip()
        if not identifier or identifier in self.shown_event_ids:
            return False
        # 勿扰/空正文也消耗本运行周期的展示资格，不在下一次刷新补弹。
        self.shown_event_ids.add(identifier)
        if not str(title or "").strip() or not str(detail or "").strip() or self.blocked():
            return False
        if display is not None:
            self.pending.clear()
            self.timer.stop()
            self.close_current()
            display()
            return True
        self.pending[identifier] = (str(title), str(detail), callback, duration_ms)
        if not self.timer.isActive():
            self.timer.start()
        return True

    def flush(self):
        self.timer.stop()
        entries = list(self.pending.values())
        self.pending.clear()
        if not entries or self.blocked():
            return
        title = entries[0][0] if len(entries) == 1 else f"六毛 · {len(entries)} 条新互动"
        detail = entries[0][1] if len(entries) == 1 else "\n".join(e[0] for e in entries[:3])
        callback = entries[-1][2]
        if self.foreground() and self.inline is not None:
            self.inline(title + "\n" + detail)
            return
        # 替换现有窗，任何渠道都不会堆叠。
        self.close_current()
        toast = BuddyReminderToast(title, detail, remaining_ms=max(e[3] for e in entries), parent=self.parent())
        self.current = toast
        toast.open_requested.connect(callback)
        toast.dismissed.connect(lambda: self._dismissed(toast))
        toast.show_passive()

    def _dismissed(self, toast):
        if self.current is toast:
            self.current = None
        toast.deleteLater()

    def close_current(self):
        toast, self.current = self.current, None
        if toast is not None:
            toast.close()

    def clear_all(self):
        self.timer.stop()
        self.pending.clear()
        self.close_current()
        self.baselines.clear()
        self.shown_event_ids.clear()
