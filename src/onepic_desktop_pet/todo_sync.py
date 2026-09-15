"""Isolated, incremental Todo synchronization for Lili.

Todo is intentionally a separate synchronization domain.  This module owns
only the local Todo queue/cursor and the ``todos`` RPCs; it never calls the
dashboard, heartbeat, focus, profile, economy, or authentication lifecycle
methods.  A failed Todo request therefore leaves the existing social and
focus synchronization untouched.

The application currently stores local data as small account-scoped JSON
files.  ``todos.json`` remains the existing local Todo store, while
``todo_sync_queue.json`` and ``todo_sync_state.json`` are its isolated sync
ledger.  Remote deletion is represented by a queued soft-delete payload; the
legacy local projection may still remove the row physically so existing Todo
UI semantics remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from PySide6.QtCore import QThread, Signal

from .local_data import read_json, write_json_atomic
from .todo_manager import TodoManager, scheduled_local_iso


LOGGER = logging.getLogger(__name__)

TODO_SYNC_VERSION = 2
TODO_SYNC_PAGE_SIZE = 100
TODO_SYNC_MAX_PULL_PAGES = 5
TODO_SYNC_MAX_PUSH_ITEMS = 20
TODO_SYNC_MAX_RETRY_SECONDS = 300
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _safe_int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse a bounded integer without letting malformed Todo data escape."""

    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


class TodoTransport(Protocol):
    """The deliberately narrow cloud contract used by ``TodoSyncService``."""

    def todo_upsert(self, payload: dict[str, Any]) -> Any: ...

    def todo_pull(
        self,
        *,
        after_updated_at: str | None,
        after_id: str | None,
        limit: int,
    ) -> Any: ...


def _canonical_uuid(value: Any) -> str:
    """Return a canonical UUID string, or an empty string for bad input."""

    try:
        return str(UUID(str(value).strip()))
    except (AttributeError, TypeError, ValueError):
        return ""


def _same_uuid(left: Any, right: Any) -> bool:
    """Compare UUIDs without allowing formatting differences to split a row."""

    left_id = _canonical_uuid(left)
    right_id = _canonical_uuid(right)
    return bool(left_id and right_id and left_id == right_id)


def _timestamp(value: Any) -> float:
    """Convert an ISO timestamp to a comparable UTC epoch, or return zero."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _iso_timestamp(value: Any, *, fallback: str) -> str:
    """Keep valid timestamps and replace malformed legacy values safely."""

    if _timestamp(value):
        return str(value)
    return fallback


def _now_iso(clock: Callable[[], datetime] | None = None) -> str:
    """Return an aware ISO timestamp for queue/state bookkeeping."""

    current = clock() if clock is not None else datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()


def _extract_rows(value: Any) -> list[dict[str, Any]]:
    """Unwrap direct RPC, relay, and test transport response shapes."""

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    if str(value.get("id") or "").strip():
        return [value]
    for key in ("data", "result", "rows", "items"):
        nested = value.get(key)
        rows = _extract_rows(nested)
        if rows:
            return rows
    return []


def _error_details(exc: BaseException) -> tuple[str, str, bool]:
    """Extract a safe Todo-only error category without importing social state."""

    kind = str(getattr(exc, "kind", "network") or "network")[:40]
    status = getattr(exc, "status", None)
    error_code = str(getattr(exc, "error_code", "") or "")[:80]
    message = str(exc)[:300] or "Todo 同步请求失败。"
    if error_code:
        message = f"{message} ({error_code})"
    retryable = bool(getattr(exc, "retryable", False))
    try:
        retryable = retryable or int(status or 0) >= 500
    except (TypeError, ValueError, OverflowError):
        pass
    return kind, message, retryable


@dataclass(frozen=True)
class TodoSyncError:
    """One failed Todo operation; it cannot represent a global sync failure."""

    message: str
    todo_id: str = ""
    kind: str = "network"
    retryable: bool = True


@dataclass(frozen=True)
class TodoPushAck:
    """A server response retained until the GUI thread safely applies it."""

    queue_id: str
    todo_id: str
    sent_payload: dict[str, Any]
    remote_row: dict[str, Any]


@dataclass(frozen=True)
class TodoSyncBatch:
    """Network results that are safe to apply to the local Todo store."""

    pulled_rows: tuple[dict[str, Any], ...] = ()
    pushed: tuple[TodoPushAck, ...] = ()
    pull_cursor: tuple[str, str] | None = None
    pull_succeeded: bool = False
    pull_complete: bool = False
    push_succeeded: bool = False
    errors: tuple[TodoSyncError, ...] = ()


@dataclass(frozen=True)
class TodoSyncApplyResult:
    """Local application summary used by the window for a cheap UI refresh."""

    pulled_count: int = 0
    pushed_count: int = 0
    changed_count: int = 0
    removed_count: int = 0
    pending_count: int = 0
    errors: tuple[TodoSyncError, ...] = ()


class TodoSyncService:
    """Own the Todo queue, composite cursor, LWW merge, and nothing else."""

    def __init__(
        self,
        todos: TodoManager,
        *,
        account_id: str,
        device_id: str = "",
        transport: TodoTransport | None = None,
        persist: bool = True,
        clock: Callable[[], datetime] | None = None,
        queue_path: str | Path | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        self.todos = todos
        self.account_id = _canonical_uuid(account_id)
        self.device_id = str(device_id or "")[:128]
        self.transport = transport
        self.persist = bool(persist)
        self._clock = clock
        root = Path(getattr(todos, "path", Path.cwd())).parent
        self.queue_path = Path(queue_path) if queue_path is not None else root / "todo_sync_queue.json"
        self.state_path = Path(state_path) if state_path is not None else root / "todo_sync_state.json"
        self._lock = threading.RLock()
        self._closed = False
        self._wakeup_callback: Callable[[], None] | None = None
        self._queue = self._load_queue()
        self._state = self._load_state()
        stored_device_id = str(self._state.get("device_id") or "").strip()
        self.device_id = (
            str(device_id or "").strip()[:128]
            or stored_device_id[:128]
            or f"todo-{uuid4()}"
        )
        if self._state.get("device_id") != self.device_id:
            self._state["device_id"] = self.device_id
            with self._lock:
                self._save_state_locked()
        self.todos.add_change_listener(self._on_local_change)
        # Version 1 sent local wall-clock strings without an offset. Postgres
        # correctly read those ambiguous values as UTC, which was not the
        # user's intended schedule. Repair this Todo store only after the
        # Todo-only listener is installed, so corrections converge through
        # the isolated queue and cannot affect another sync domain.
        if _safe_int(self._state.get("schedule_timezone_repair_version", 0), minimum=0) < 1:
            repaired = self.todos.repair_scheduled_instants()
            self._state["schedule_timezone_repair_version"] = 1
            if repaired:
                LOGGER.info("Todo schedule timezone repair queued count=%s", repaired)
            with self._lock:
                self._save_state_locked()

    def close(self) -> None:
        """Detach the observer; queued data remains available for next launch."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.todos.remove_change_listener(self._on_local_change)

    def set_wakeup_callback(self, callback: Callable[[], None] | None) -> None:
        """Set a non-blocking callback used to kick the independent worker."""

        with self._lock:
            self._wakeup_callback = callback

    def state_snapshot(self) -> dict[str, Any]:
        """Return diagnostic state without exposing mutable internal objects."""

        with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))

    @property
    def pending_count(self) -> int:
        """Return the number of Todo-only queued mutations."""

        with self._lock:
            return len(self._queue)

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": TODO_SYNC_VERSION,
            "domain": "todo",
            "account_id": self.account_id,
            "schedule_timezone_repair_version": 0,
            "last_pull_updated_at": None,
            "last_pull_id": None,
            "last_pull_at": None,
            "last_push_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_error_at": None,
            "initial_import_enqueued": False,
            "quarantined": {},
            "device_id": "",
        }

    def _load_state(self) -> dict[str, Any]:
        default = self._default_state()
        if not self.persist:
            return default
        raw = read_json(self.state_path, {})
        if not isinstance(raw, dict):
            return default
        stored_account = _canonical_uuid(raw.get("account_id"))
        if stored_account != self.account_id:
            # The file is normally account-scoped already.  If an old/manual
            # copy is found, reset only this Todo cursor, never another domain.
            return default
        state = dict(default)
        for key in default:
            if key in raw:
                state[key] = raw[key]
        state["domain"] = "todo"
        state["account_id"] = self.account_id
        state["version"] = TODO_SYNC_VERSION
        if not isinstance(state.get("quarantined"), dict):
            state["quarantined"] = {}
        return state

    def _load_queue(self) -> list[dict[str, Any]]:
        if not self.persist:
            return []
        raw = read_json(self.queue_path, [])
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            todo_id = _canonical_uuid(item.get("todo_id") or (payload or {}).get("id")) if isinstance(payload, dict) else _canonical_uuid(item.get("todo_id"))
            if not todo_id or not isinstance(payload, dict):
                continue
            payload = dict(payload)
            payload["id"] = todo_id
            if payload.get("user_id") and not _same_uuid(payload.get("user_id"), self.account_id):
                continue
            try:
                retry_count = max(0, int(item.get("retry_count", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                retry_count = 0
            try:
                next_attempt_at = max(0.0, float(item.get("next_attempt_at", 0.0) or 0.0))
            except (TypeError, ValueError, OverflowError):
                next_attempt_at = 0.0
            entry = {
                "queue_id": str(item.get("queue_id") or uuid4()),
                "todo_id": todo_id,
                "operation": "delete" if str(item.get("operation")) == "delete" else "upsert",
                "payload": payload,
                "created_at": str(item.get("created_at") or _now_iso(self._clock)),
                "retry_count": retry_count,
                "last_error": str(item.get("last_error") or "")[:300],
                "next_attempt_at": next_attempt_at,
            }
            result.append(entry)
        return result

    def _save_queue_locked(self) -> None:
        if self.persist:
            write_json_atomic(self.queue_path, self._queue)

    def _save_state_locked(self) -> None:
        if self.persist:
            write_json_atomic(self.state_path, self._state)

    def _on_local_change(self, operation: str, payload: dict[str, Any]) -> None:
        """Queue one local mutation; any observer failure remains local-only."""

        if self._closed or not self.account_id:
            return
        remote_payload = self._remote_payload_from_local(payload, operation=operation)
        if remote_payload is None:
            return
        self._enqueue_payload(
            "delete" if operation == "delete" else "upsert",
            remote_payload,
        )
        callback = None
        with self._lock:
            callback = self._wakeup_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                LOGGER.exception("Todo sync wakeup callback failed")

    def _remote_payload_from_local(
        self,
        value: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any] | None:
        """Map the existing local shape to the isolated remote Todo shape."""

        if not isinstance(value, dict):
            return None
        todo_id = _canonical_uuid(value.get("id"))
        if not todo_id or not self.account_id:
            LOGGER.warning("Todo mutation ignored because its UUID/account is invalid")
            return None
        now = _now_iso(self._clock)
        created_at = _iso_timestamp(value.get("created_at"), fallback=now)
        updated_at = _iso_timestamp(value.get("updated_at") or value.get("created_at"), fallback=created_at)
        deleted_at = str(value.get("deleted_at") or "").strip() or None
        if operation == "delete":
            deleted_at = deleted_at or now
        completed = bool(value.get("completed", False))
        status = "deleted" if deleted_at else "completed" if completed else "pending"
        try:
            priority = int(value.get("priority")) if value.get("priority") is not None else None
        except (TypeError, ValueError, OverflowError):
            priority = None
        metadata = {
            "date": str(value.get("date") or "")[:10],
            "date_explicit": bool(value.get("date_explicit", False)),
            "time": str(value.get("time") or "")[:5] or None,
            "important": bool(value.get("important", False)),
            "highlight": bool(value.get("highlight", False)),
            "read": bool(value.get("read", False)),
            "read_at": value.get("read_at"),
            "work_seconds": max(0, int(value.get("work_seconds", 0) or 0)),
            "queue_position": value.get("queue_position"),
            "reminder": bool(value.get("reminder", False)),
            "reminder_minutes_before": value.get("reminder_minutes_before", 10),
            "reminder_mode": str(value.get("reminder_mode") or "none")[:16],
            "alarm_sound_id": str(value.get("alarm_sound_id") or "system")[:40],
            "alarm_volume": value.get("alarm_volume", 60),
            "alarm_snooze_minutes": value.get("alarm_snooze_minutes", 10),
            "reminder_suppressed": bool(value.get("reminder_suppressed", False)),
            "source": str(value.get("source") or "local")[:40],
        }
        return {
            "id": todo_id,
            "user_id": self.account_id,
            "title": " ".join(str(value.get("title") or "未命名事项").split())[:240] or "未命名事项",
            "content": str(value.get("content") or "")[:4000],
            "status": status,
            "priority": priority,
            # Always derive the cloud instant from the explicit local schedule
            # when it exists. A bare ISO timestamp must never reach a
            # timestamptz column, where it would be interpreted as UTC.
            "due_at": scheduled_local_iso(
                metadata["date"],
                metadata["time"],
                self._clock,
            ) or str(value.get("due_at") or "").strip() or None,
            "created_at": created_at,
            "updated_at": updated_at if operation != "delete" else now,
            "completed_at": str(value.get("completed_at") or "").strip() or None,
            "deleted_at": deleted_at,
            "updated_by_device_id": self.device_id or None,
            "metadata": metadata,
        }

    def _enqueue_payload(self, operation: str, payload: dict[str, Any]) -> None:
        """Coalesce pending mutations by Todo UUID without touching other data."""

        todo_id = _canonical_uuid(payload.get("id"))
        if not todo_id or not self.account_id:
            return
        now = _now_iso(self._clock)
        with self._lock:
            existing = next((entry for entry in self._queue if entry["todo_id"] == todo_id), None)
            if existing is None:
                self._queue.append(
                    {
                        "queue_id": str(uuid4()),
                        "todo_id": todo_id,
                        "operation": operation,
                        "payload": dict(payload),
                        "created_at": now,
                        "retry_count": 0,
                        "last_error": "",
                        "next_attempt_at": 0.0,
                    }
                )
            else:
                existing.update(
                    {
                        "operation": operation,
                        "payload": dict(payload),
                        "retry_count": 0,
                        "last_error": "",
                        "next_attempt_at": 0.0,
                    }
                )
            self._save_queue_locked()

    def enqueue_local_item(self, value: dict[str, Any]) -> bool:
        """Enqueue one existing local Todo for the one-time account import."""

        payload = self._remote_payload_from_local(value, operation="upsert")
        if payload is None:
            return False
        self._enqueue_payload("upsert", payload)
        return True

    def ensure_initial_queue(self) -> int:
        """Queue existing local Todos once, without replacing remote rows."""

        if not self.account_id:
            return 0
        with self._lock:
            if bool(self._state.get("initial_import_enqueued")):
                return 0
        count = 0
        # The local store is the GUI-owned source.  This snapshot is taken
        # before the worker starts; later edits arrive through the listener.
        for item in self.todos.items:
            if self.enqueue_local_item(item.to_dict()):
                count += 1
        with self._lock:
            self._state["initial_import_enqueued"] = True
            self._save_state_locked()
        return count

    def _cursor_from_state_locked(self) -> tuple[str | None, str | None]:
        updated = str(self._state.get("last_pull_updated_at") or "").strip() or None
        todo_id = _canonical_uuid(self._state.get("last_pull_id")) or None
        if updated and _timestamp(updated) and todo_id:
            return updated, todo_id
        if updated or todo_id:
            # Only the Todo cursor is repaired.  A malformed Todo cursor must
            # never cause a global sync reset or touch Auth/Focus state.
            self._state["last_pull_updated_at"] = None
            self._state["last_pull_id"] = None
        return None, None

    def _validate_remote_row(self, row: dict[str, Any]) -> tuple[bool, str, str]:
        """Validate ownership and cursor fields before any local merge."""

        remote_id = _canonical_uuid(row.get("id"))
        if not remote_id:
            return False, "", "remote Todo id 无效"
        remote_user = _canonical_uuid(row.get("user_id"))
        if not remote_user or remote_user != self.account_id:
            return False, remote_id, "remote Todo 所属账号不匹配"
        updated_at = str(row.get("updated_at") or "").strip()
        if not updated_at or not _timestamp(updated_at):
            return False, remote_id, "remote Todo updated_at 无效"
        status = str(row.get("status") or "pending").strip().lower()
        if status not in {"pending", "completed", "deleted"}:
            return False, remote_id, "remote Todo status 无效"
        if len(str(row.get("title") or "")) > 240:
            return False, remote_id, "remote Todo 标题过长"
        return True, remote_id, ""

    def _queue_failure(self, entry: dict[str, Any], error: TodoSyncError) -> None:
        """Persist retry metadata only in the Todo queue."""

        with self._lock:
            current = next((item for item in self._queue if item["queue_id"] == entry["queue_id"]), None)
            if current is None or current.get("payload") != entry.get("payload"):
                return
            retry_count = max(0, int(current.get("retry_count", 0) or 0)) + 1
            delay = min(TODO_SYNC_MAX_RETRY_SECONDS, 5 * (2 ** min(retry_count - 1, 6)))
            current["retry_count"] = retry_count
            current["last_error"] = error.message[:300]
            current["next_attempt_at"] = datetime.now().timestamp() + delay
            self._save_queue_locked()

    def _transport_available(self) -> bool:
        with self._lock:
            if self._closed or self.transport is None:
                return False
            transport = self.transport
        signed_in = getattr(transport, "signed_in", None)
        return signed_in is not False

    def _pull_remote(self, errors: list[TodoSyncError]) -> tuple[list[dict[str, Any]], tuple[str, str] | None, bool, bool]:
        """Pull only the Todo cursor range and return a bounded batch."""

        if not self._transport_available():
            return [], None, False, False
        with self._lock:
            after_updated_at, after_id = self._cursor_from_state_locked()
        rows: list[dict[str, Any]] = []
        cursor: tuple[str, str] | None = None
        succeeded = False
        complete = False
        for _page in range(TODO_SYNC_MAX_PULL_PAGES):
            if not self._transport_available():
                break
            try:
                raw = self.transport.todo_pull(
                    after_updated_at=after_updated_at,
                    after_id=after_id,
                    limit=TODO_SYNC_PAGE_SIZE,
                )
            except Exception as exc:
                kind, message, retryable = _error_details(exc)
                errors.append(TodoSyncError(message, kind=kind, retryable=retryable))
                break
            raw_rows = _extract_rows(raw)
            succeeded = True
            if not raw_rows:
                complete = True
                break
            valid_rows = 0
            for raw_row in raw_rows:
                valid, remote_id, reason = self._validate_remote_row(raw_row)
                if not valid:
                    errors.append(TodoSyncError(reason, todo_id=remote_id, kind="malformed", retryable=False))
                    # A malformed row with a valid composite key is quarantined
                    # for diagnostics and skipped so one bad Todo cannot stall
                    # every other Todo forever.
                    updated = str(raw_row.get("updated_at") or "").strip()
                    if remote_id and _timestamp(updated):
                        cursor = (updated, remote_id)
                    else:
                        complete = False
                        break
                    continue
                normalized = dict(raw_row)
                normalized["id"] = remote_id
                rows.append(normalized)
                valid_rows += 1
                updated = str(normalized["updated_at"])
                cursor = (updated, remote_id)
            if not raw_rows:
                complete = True
                break
            if len(raw_rows) < TODO_SYNC_PAGE_SIZE:
                complete = True
                break
            if cursor is None:
                break
            after_updated_at, after_id = cursor
            if valid_rows == 0 and len(raw_rows) >= TODO_SYNC_PAGE_SIZE:
                # Continue by the valid cursor when possible, but never issue
                # an unbounded loop for a malformed external response.
                continue
        return rows, cursor, succeeded, complete

    def _push_queue(self, errors: list[TodoSyncError]) -> tuple[list[TodoPushAck], bool]:
        """Push queued Todo rows one by one with idempotent UUID payloads."""

        if not self._transport_available():
            return [], False
        now = datetime.now().timestamp()
        with self._lock:
            entries = [
                dict(entry)
                for entry in self._queue
                if float(entry.get("next_attempt_at", 0.0) or 0.0) <= now
            ][:TODO_SYNC_MAX_PUSH_ITEMS]
        acknowledgements: list[TodoPushAck] = []
        succeeded = False
        for entry in entries:
            if not self._transport_available():
                break
            payload = dict(entry.get("payload") or {})
            todo_id = _canonical_uuid(entry.get("todo_id") or payload.get("id"))
            if not todo_id or not _same_uuid(payload.get("user_id"), self.account_id):
                error = TodoSyncError("Todo 队列所属账号或 UUID 无效。", todo_id=todo_id, kind="malformed", retryable=False)
                errors.append(error)
                self._queue_failure(entry, error)
                continue
            try:
                raw = self.transport.todo_upsert(payload)
            except Exception as exc:
                kind, message, retryable = _error_details(exc)
                error = TodoSyncError(message, todo_id=todo_id, kind=kind, retryable=retryable)
                errors.append(error)
                self._queue_failure(entry, error)
                # A 401/403 or equivalent auth response affects this Todo
                # request only. Stop this batch to avoid hammering the same
                # expired credential; do not clear the account session.
                if kind.startswith("auth") or getattr(exc, "status", None) in {401, 403}:
                    break
                continue
            response_rows = _extract_rows(raw)
            remote_row = next(
                (
                    dict(row)
                    for row in response_rows
                    if _same_uuid(row.get("id"), todo_id)
                    and _same_uuid(row.get("user_id"), self.account_id)
                ),
                None,
            )
            if remote_row is None:
                error = TodoSyncError(
                    "Todo 服务器未返回可确认的记录，稍后将幂等重试。",
                    todo_id=todo_id,
                    kind="malformed",
                    retryable=True,
                )
                errors.append(error)
                self._queue_failure(entry, error)
                continue
            valid, _, reason = self._validate_remote_row(remote_row)
            if not valid:
                error = TodoSyncError(reason, todo_id=todo_id, kind="malformed", retryable=False)
                errors.append(error)
                self._queue_failure(entry, error)
                continue
            succeeded = True
            acknowledgements.append(
                TodoPushAck(
                    queue_id=str(entry["queue_id"]),
                    todo_id=todo_id,
                    sent_payload=payload,
                    remote_row=remote_row,
                )
            )
        return acknowledgements, succeeded

    def perform_remote_sync(self) -> TodoSyncBatch:
        """Run Todo network I/O without mutating the GUI-owned Todo objects."""

        with self._lock:
            if self._closed:
                return TodoSyncBatch()
        if not self.account_id or self.transport is None:
            return TodoSyncBatch()
        errors: list[TodoSyncError] = []
        pulled_rows, cursor, pull_succeeded, pull_complete = self._pull_remote(errors)
        # A Todo auth failure pauses Todo upload for this batch only.  The
        # Todo transport deliberately does not own Auth lifecycle, so do not
        # keep retrying writes against a credential that the pull just
        # rejected; the isolated queue remains pending for a later recovery.
        if any(error.kind.startswith("auth") for error in errors):
            pushed, push_succeeded = [], False
        else:
            pushed, push_succeeded = self._push_queue(errors)
        return TodoSyncBatch(
            pulled_rows=tuple(pulled_rows),
            pushed=tuple(pushed),
            pull_cursor=cursor,
            pull_succeeded=pull_succeeded,
            pull_complete=pull_complete,
            push_succeeded=push_succeeded,
            errors=tuple(errors),
        )

    def _local_item_for_remote(self, remote_id: str):
        for item in self.todos.items:
            if _same_uuid(item.id, remote_id):
                return item
        return None

    def _remote_to_local(self, remote: dict[str, Any], existing: Any | None) -> dict[str, Any]:
        """Restore local display metadata while preserving the local UUID spelling."""

        metadata = remote.get("metadata") if isinstance(remote.get("metadata"), dict) else {}
        due_at = str(remote.get("due_at") or "").strip() or None
        if due_at and not _timestamp(due_at):
            due_at = None
        date_value = str(metadata.get("date") or "")[:10]
        time_value = str(metadata.get("time") or "")[:5] or None
        if not date_value and due_at:
            date_value = due_at[:10]
        if not time_value and due_at and len(due_at) >= 16:
            time_value = due_at[11:16]
        value = existing.to_dict() if existing is not None else {}
        priority = remote.get("priority")
        if priority is not None:
            priority = _safe_int(priority, 0)
            if priority not in {1, 2, 3}:
                priority = None
        completed_at = str(remote.get("completed_at") or "").strip() or None
        if completed_at and not _timestamp(completed_at):
            completed_at = None
        value.update(
            {
                "id": existing.id if existing is not None else str(remote["id"]),
                "title": str(remote.get("title") or "未命名事项")[:240],
                "content": str(remote.get("content") or "")[:4000],
                "date": date_value,
                "date_explicit": bool(metadata.get("date_explicit", bool(due_at))),
                "time": time_value,
                "important": bool(metadata.get("important", False)),
                "highlight": bool(metadata.get("highlight", False)),
                "completed": str(remote.get("status") or "pending") == "completed",
                "completed_at": completed_at,
                "work_seconds": _safe_int(metadata.get("work_seconds", 0), minimum=0),
                "due_at": due_at,
                "remind_at": value.get("remind_at"),
                "priority": priority,
                "queue_position": metadata.get("queue_position"),
                "read": bool(metadata.get("read", False)),
                "read_at": metadata.get("read_at"),
                "reminder": bool(metadata.get("reminder", False)),
                "reminder_minutes_before": _safe_int(
                    metadata.get("reminder_minutes_before", 10),
                    default=10,
                    minimum=0,
                    maximum=24 * 60,
                ),
                "reminder_mode": str(metadata.get("reminder_mode") or "none")[:16],
                "alarm_sound_id": str(metadata.get("alarm_sound_id") or "system")[:40],
                "alarm_volume": _safe_int(
                    metadata.get("alarm_volume", 60),
                    default=60,
                    minimum=0,
                    maximum=100,
                ),
                "alarm_snooze_minutes": _safe_int(
                    metadata.get("alarm_snooze_minutes", 10),
                    default=10,
                    minimum=1,
                    maximum=120,
                ),
                "reminder_suppressed": bool(metadata.get("reminder_suppressed", False)),
                "created_at": str(remote.get("created_at") or value.get("created_at") or _now_iso(self._clock)),
                "updated_at": str(remote.get("updated_at") or value.get("updated_at") or _now_iso(self._clock)),
                "source": str(metadata.get("source") or "sync")[:40],
            }
        )
        return value

    def _merge_remote_row(self, remote: dict[str, Any]) -> tuple[int, int]:
        """Apply one LWW row and return ``(changed, removed)`` counts."""

        valid, remote_id, reason = self._validate_remote_row(remote)
        if not valid:
            LOGGER.info("Todo remote row skipped kind=malformed id=%s reason=%s", remote_id, reason)
            return 0, 0
        remote_updated = _timestamp(remote.get("updated_at"))
        with self._lock:
            pending = next((entry for entry in self._queue if entry["todo_id"] == remote_id), None)
            pending_updated = _timestamp((pending or {}).get("payload", {}).get("updated_at")) if pending else 0.0
        existing = self._local_item_for_remote(remote_id)
        local_updated = _timestamp(getattr(existing, "updated_at", "") or getattr(existing, "created_at", "")) if existing is not None else 0.0
        if pending_updated > remote_updated or local_updated > remote_updated:
            return 0, 0
        if str(remote.get("status") or "pending") == "deleted" or remote.get("deleted_at"):
            removed = self.todos.remove_sync_snapshot(existing.id) if existing is not None else False
            return 0, int(removed)
        local_value = self._remote_to_local(remote, existing)
        changed = self.todos.apply_sync_snapshot(local_value)
        return int(changed is not None), 0

    def _drop_queue_if_remote_wins(self, remote: dict[str, Any]) -> None:
        remote_id = _canonical_uuid(remote.get("id"))
        remote_updated = _timestamp(remote.get("updated_at"))
        if not remote_id or not remote_updated:
            return
        with self._lock:
            self._queue = [
                entry
                for entry in self._queue
                if not (
                    entry["todo_id"] == remote_id
                    and _timestamp(entry.get("payload", {}).get("updated_at")) <= remote_updated
                )
            ]

    def _drop_acknowledged_queue(self, ack: TodoPushAck) -> None:
        with self._lock:
            current = next((entry for entry in self._queue if entry["queue_id"] == ack.queue_id), None)
            if current is not None and current.get("payload") == ack.sent_payload:
                self._queue = [entry for entry in self._queue if entry["queue_id"] != ack.queue_id]

    def apply_batch(self, batch: TodoSyncBatch) -> TodoSyncApplyResult:
        """Merge a completed worker batch on the GUI thread, Todo-only."""

        if not isinstance(batch, TodoSyncBatch):
            return TodoSyncApplyResult(pending_count=self.pending_count)
        changed = 0
        removed = 0
        with self.todos.suppress_change_notifications():
            for row in batch.pulled_rows:
                row_changed, row_removed = self._merge_remote_row(row)
                changed += row_changed
                removed += row_removed
                self._drop_queue_if_remote_wins(row)
            for ack in batch.pushed:
                row_changed, row_removed = self._merge_remote_row(ack.remote_row)
                changed += row_changed
                removed += row_removed
                self._drop_acknowledged_queue(ack)
        with self._lock:
            if batch.pull_cursor is not None and batch.pull_succeeded:
                self._state["last_pull_updated_at"] = batch.pull_cursor[0]
                self._state["last_pull_id"] = batch.pull_cursor[1]
            now = _now_iso(self._clock)
            if batch.pull_succeeded:
                self._state["last_pull_at"] = now
            if batch.push_succeeded:
                self._state["last_push_at"] = now
            if batch.pull_succeeded or batch.push_succeeded:
                self._state["last_success_at"] = now
            if batch.errors:
                first = batch.errors[0]
                self._state["last_error"] = first.message[:300]
                self._state["last_error_at"] = now
                for error in batch.errors:
                    if error.todo_id:
                        self._state.setdefault("quarantined", {})[error.todo_id] = {
                            "kind": error.kind,
                            "message": error.message[:300],
                            "at": now,
                        }
            else:
                self._state["last_error"] = None
                self._state["last_error_at"] = None
            self._save_queue_locked()
            self._save_state_locked()
            pending_count = len(self._queue)
        return TodoSyncApplyResult(
            pulled_count=len(batch.pulled_rows),
            pushed_count=len(batch.pushed),
            changed_count=changed,
            removed_count=removed,
            pending_count=pending_count,
            errors=batch.errors,
        )

    def sync_once(self) -> TodoSyncApplyResult:
        """Synchronous helper for tests and controlled non-Qt callers."""

        self.ensure_initial_queue()
        return self.apply_batch(self.perform_remote_sync())


class TodoSyncThread(QThread):
    """Run only Todo network I/O outside the GUI event loop."""

    completed = Signal(object)

    def __init__(self, service: TodoSyncService, parent=None) -> None:
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        try:
            batch = self.service.perform_remote_sync()
            if not self.isInterruptionRequested():
                self.completed.emit(batch)
        except Exception as exc:
            kind, message, retryable = _error_details(exc)
            LOGGER.exception("Todo sync worker crashed")
            if not self.isInterruptionRequested():
                self.completed.emit(
                    TodoSyncBatch(
                        errors=(TodoSyncError(message, kind=kind, retryable=retryable),)
                    )
                )
