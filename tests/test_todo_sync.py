"""Todo-only synchronization tests; no real account or network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import UUID

import pytest

from onepic_desktop_pet.todo_manager import TodoManager
from onepic_desktop_pet.todo_sync import TodoSyncService


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
DEVICE_ID = "test-device"
LOCAL_TZ = timezone(timedelta(hours=8))


class TodoTransportError(RuntimeError):
    """A transport failure with the same metadata used by SocialError."""

    kind = "timeout"
    retryable = True
    status = 504


class TodoAuthError(RuntimeError):
    """An auth rejection that must pause Todo writes, not clear the session."""

    kind = "auth"
    retryable = True
    status = 401


class FakeTodoTransport:
    """A tiny Direct-only Todo server model with per-row LWW semantics."""

    signed_in = True

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.pull_calls: list[tuple[str | None, str | None, int]] = []
        self.upsert_calls: list[dict] = []
        self.fail_pull = False
        self.fail_upsert = False

    @staticmethod
    def _stamp(value: object) -> float:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()

    def todo_pull(
        self,
        *,
        after_updated_at: str | None,
        after_id: str | None,
        limit: int,
    ) -> list[dict]:
        self.pull_calls.append((after_updated_at, after_id, limit))
        if self.fail_pull:
            raise TodoTransportError("pull timeout")
        rows = sorted(
            self.rows.values(),
            key=lambda row: (self._stamp(row["updated_at"]), row["id"]),
        )
        if after_updated_at is not None:
            after_key = (self._stamp(after_updated_at), str(after_id or ""))
            rows = [
                row
                for row in rows
                if (self._stamp(row["updated_at"]), row["id"]) > after_key
            ]
        return [dict(row) for row in rows[:limit]]

    def todo_upsert(self, payload: dict) -> list[dict]:
        self.upsert_calls.append(dict(payload))
        if self.fail_upsert:
            raise TodoTransportError("upsert timeout")
        incoming = dict(payload)
        current = self.rows.get(str(incoming["id"]))
        if current is None or self._stamp(incoming["updated_at"]) >= self._stamp(current["updated_at"]):
            self.rows[str(incoming["id"])] = incoming
        return [dict(self.rows[str(incoming["id"])])]


class AuthFailureTransport(FakeTodoTransport):
    def todo_pull(
        self,
        *,
        after_updated_at: str | None,
        after_id: str | None,
        limit: int,
    ) -> list[dict]:
        self.pull_calls.append((after_updated_at, after_id, limit))
        raise TodoAuthError("Todo token expired")


def _clock(hour: int = 9) -> datetime:
    return datetime(2026, 9, 15, hour, 0, tzinfo=LOCAL_TZ)


def _service(tmp_path: Path, *, clock: datetime | None = None, transport=None):
    manager = TodoManager(
        tmp_path / "todos.json",
        now_provider=lambda: clock or _clock(),
    )
    service = TodoSyncService(
        manager,
        account_id=ACCOUNT_ID,
        device_id=DEVICE_ID,
        transport=transport or FakeTodoTransport(),
        queue_path=tmp_path / "todo_sync_queue.json",
        state_path=tmp_path / "todo_sync_state.json",
    )
    return manager, service


def test_local_todo_change_only_adds_todo_queue_state(tmp_path) -> None:
    unrelated = tmp_path / "focus.json"
    unrelated.write_text('{"seconds": 123}', encoding="utf-8")
    manager, service = _service(tmp_path)

    item = manager.add("独立同步测试", content="备注")

    assert item.content == "备注"
    assert service.pending_count == 1
    assert json.loads(unrelated.read_text(encoding="utf-8")) == {"seconds": 123}
    assert (tmp_path / "todos.json").is_file()
    assert (tmp_path / "todo_sync_queue.json").is_file()
    assert not (tmp_path / "focus_sync_queue.json").exists()

    service.close()


def test_scheduled_todo_is_uploaded_with_an_explicit_local_offset(tmp_path) -> None:
    manager, service = _service(tmp_path)
    item = manager.add("党会", date="2026-09-16", time="12:30")

    payload = service._remote_payload_from_local(item.to_dict(), operation="upsert")

    assert payload is not None
    assert payload["due_at"] == "2026-09-16T12:30:00+08:00"
    assert payload["metadata"]["date"] == "2026-09-16"
    assert payload["metadata"]["time"] == "12:30"
    service.close()


def test_legacy_utc_schedule_is_repaired_through_todo_queue_only(tmp_path) -> None:
    path = tmp_path / "todos.json"
    path.write_text(
        json.dumps(
            [{
                "id": "2b34db03-465b-433a-9f40-34e07a9b584d",
                "title": "党会",
                "date": "2026-09-16",
                "date_explicit": True,
                "time": "12:30",
                "due_at": "2026-09-16T12:30:00+00:00",
                "reminder": True,
                "reminder_mode": "pet",
                "reminder_minutes_before": 10,
                "remind_at": "2026-09-16T12:20:00+00:00",
                "created_at": _clock().isoformat(),
                "updated_at": _clock().isoformat(),
            }],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manager, service = _service(tmp_path)

    item = manager.get("2b34db03-465b-433a-9f40-34e07a9b584d")
    assert item is not None
    assert item.due_at == "2026-09-16T12:30:00+08:00"
    assert item.remind_at == "2026-09-16T12:20:00+08:00"
    assert service.pending_count == 1
    payload = service._remote_payload_from_local(item.to_dict(), operation="upsert")
    assert payload is not None
    assert payload["due_at"] == "2026-09-16T12:30:00+08:00"
    service.close()


def test_todo_network_failure_keeps_local_todo_and_other_data_untouched(tmp_path) -> None:
    unrelated = tmp_path / "focus.json"
    unrelated.write_text('{"seconds": 456}', encoding="utf-8")
    transport = FakeTodoTransport()
    transport.fail_pull = True
    transport.fail_upsert = True
    manager, service = _service(tmp_path, transport=transport)
    item = manager.add("断网时仍可用")

    result = service.sync_once()

    assert manager.get(item.id) is not None
    assert service.pending_count == 1
    assert result.errors
    assert json.loads(unrelated.read_text(encoding="utf-8")) == {"seconds": 456}
    assert service.state_snapshot()["domain"] == "todo"
    assert service.state_snapshot()["last_error"]

    service.close()


def test_todo_auth_failure_pauses_only_todo_push(tmp_path) -> None:
    transport = AuthFailureTransport()
    manager, service = _service(tmp_path, transport=transport)
    item = manager.add("认证失败仍应保留")

    result = service.sync_once()

    assert manager.get(item.id) is not None
    assert transport.upsert_calls == []
    assert service.pending_count == 1
    assert any(error.kind == "auth" for error in result.errors)
    assert service.state_snapshot()["account_id"] == ACCOUNT_ID

    service.close()


def test_legacy_non_uuid_todo_id_is_migrated_once(tmp_path) -> None:
    path = tmp_path / "todos.json"
    path.write_text(
        json.dumps(
            [{"id": "old-local-key", "title": "旧待办", "date": "2026-09-15"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = TodoManager(path, now_provider=lambda: _clock())
    item = manager.items[0]

    UUID(item.id)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted[0]["id"] == item.id



def test_todo_upsert_is_idempotent_and_does_not_duplicate_rows(tmp_path) -> None:
    transport = FakeTodoTransport()
    manager, service = _service(tmp_path, transport=transport)
    item = manager.add("只应有一条")

    first = service.sync_once()
    second = service.sync_once()

    assert first.pushed_count == 1
    assert second.pushed_count == 0
    assert len(transport.rows) == 1
    assert service.pending_count == 0
    assert len(transport.upsert_calls) == 1

    service.close()


def test_newer_remote_todo_wins_only_for_that_todo(tmp_path) -> None:
    transport = FakeTodoTransport()
    manager, service = _service(tmp_path, clock=_clock(9), transport=transport)
    item = manager.add("本地旧标题")
    remote = service._remote_payload_from_local(item.to_dict(), operation="upsert")
    assert remote is not None
    remote.update(
        {
            "title": "云端新标题",
            "updated_at": datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc).isoformat(),
        }
    )
    transport.rows[remote["id"]] = remote

    result = service.sync_once()

    assert result.changed_count >= 1
    assert manager.get(item.id).title == "云端新标题"
    assert service.pending_count == 0

    service.close()


def test_local_newer_todo_is_pushed_without_replacing_other_rows(tmp_path) -> None:
    transport = FakeTodoTransport()
    manager, service = _service(tmp_path, clock=_clock(10), transport=transport)
    item = manager.add("本地新标题")
    remote = service._remote_payload_from_local(item.to_dict(), operation="upsert")
    assert remote is not None
    remote.update(
        {
            "title": "云端旧标题",
            "updated_at": datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc).isoformat(),
        }
    )
    transport.rows[remote["id"]] = remote

    service.sync_once()

    assert transport.rows[remote["id"]]["title"] == "本地新标题"
    assert manager.get(item.id).title == "本地新标题"
    assert service.pending_count == 0

    service.close()


def test_delete_queues_a_soft_delete_tombstone(tmp_path) -> None:
    transport = FakeTodoTransport()
    manager, service = _service(tmp_path, transport=transport)
    item = manager.add("待删除")
    service.sync_once()
    assert manager.delete(item.id) is True

    result = service.sync_once()

    assert result.pushed_count == 1
    remote = next(iter(transport.rows.values()))
    assert remote["status"] == "deleted"
    assert remote["deleted_at"]
    assert manager.get(item.id) is None
    assert service.pending_count == 0

    service.close()


def test_same_timestamp_rows_advance_composite_cursor(tmp_path) -> None:
    transport = FakeTodoTransport()
    manager, service = _service(tmp_path, transport=transport)
    stamp = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc).isoformat()
    for index, todo_id in enumerate(
        (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
    ):
        transport.rows[todo_id] = {
            "id": todo_id,
            "user_id": ACCOUNT_ID,
            "title": f"云端 {index}",
            "content": "",
            "status": "pending",
            "priority": None,
            "due_at": None,
            "created_at": stamp,
            "updated_at": stamp,
            "completed_at": None,
            "deleted_at": None,
            "updated_by_device_id": "remote",
            "metadata": {},
        }

    service.sync_once()
    state = service.state_snapshot()
    assert state["last_pull_updated_at"] == stamp
    assert state["last_pull_id"] == "00000000-0000-0000-0000-000000000002"

    service.sync_once()
    assert transport.pull_calls[-1][0] == stamp
    assert transport.pull_calls[-1][1] == "00000000-0000-0000-0000-000000000002"

    service.close()


def test_malformed_remote_todo_is_quarantined_without_clearing_local(tmp_path) -> None:
    transport = FakeTodoTransport()
    manager, service = _service(tmp_path, transport=transport)
    item = manager.add("本地保留")
    remote_id = "00000000-0000-0000-0000-000000000010"
    transport.rows[remote_id] = {
        "id": remote_id,
        "user_id": "22222222-2222-2222-2222-222222222222",
        "title": "不属于当前账号",
        "updated_at": datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc).isoformat(),
        "status": "pending",
    }

    result = service.sync_once()

    assert result.errors
    assert manager.get(item.id) is not None
    assert service.state_snapshot()["quarantined"][remote_id]["kind"] == "malformed"

    service.close()


def test_migration_owns_only_the_todo_table_and_rpc(tmp_path) -> None:
    migration = Path(__file__).parents[1] / "supabase" / "migrations" / "20260915120000_lili_todo_sync.sql"
    sql = migration.read_text(encoding="utf-8")
    normalized = re.sub(r"--[^\n]*", "", sql).lower()

    assert "create table if not exists public.todos" in normalized
    assert "alter table public.todos enable row level security" in normalized
    assert "(select auth.uid())" in normalized
    assert "lili_todo_pull" in normalized
    assert "lili_todo_upsert" in normalized
    public_refs = set(re.findall(r"public\.([a-z_]+)", normalized))
    assert public_refs <= {"todos", "lili_todo_pull", "lili_todo_upsert"}
    assert "auth.users" in normalized
