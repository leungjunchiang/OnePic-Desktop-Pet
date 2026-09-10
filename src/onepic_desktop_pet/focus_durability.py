"""Crash-safe local durability helpers for sealed FocusSegment facts.

The active-session checkpoint remains owned by :mod:`work_timer`.  This module
owns the next boundary in the lifecycle: once a pause/finish transition has
an immutable interval snapshot, the recovery journal is appended before the
normal AccountFocusStore JSON is changed.  The journal is never a reporting
source; it only repairs a missing local row after a process crash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .focus_segments import FocusSegment, parse_focus_timestamp


WAL_SCHEMA_VERSION = 1


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def segment_payload(segment: FocusSegment) -> dict[str, Any]:
    """Return the immutable, non-sensitive fields covered by the WAL hash."""

    value = segment.normalized()
    if value.end_at is None:
        raise ValueError("a sealed FocusSegment must have end_at")
    if value.end_at <= value.start_at:
        raise ValueError("a sealed FocusSegment must have a positive duration")
    return {
        "segment_id": value.segment_id[:160],
        "session_id": value.session_id[:160],
        "device_id": value.device_id[:120],
        "start_at": value.start_at.isoformat(),
        "end_at": value.end_at.isoformat(),
        "completed": bool(value.completed),
        "quality": int(value.quality),
        "task": value.task[:120],
        "interruptions": int(value.interruptions),
    }


def segment_payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def segment_from_payload(payload: dict[str, Any]) -> FocusSegment:
    start = parse_focus_timestamp(payload.get("start_at"))
    end = parse_focus_timestamp(payload.get("end_at"))
    if start is None or end is None:
        raise ValueError("WAL segment timestamp is invalid")
    return FocusSegment(
        segment_id=str(payload.get("segment_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        device_id=str(payload.get("device_id") or ""),
        start_at=start,
        end_at=end,
        completed=bool(payload.get("completed")),
        quality=int(payload.get("quality", 0) or 0),
        task=str(payload.get("task") or ""),
        interruptions=int(payload.get("interruptions", 0) or 0),
    ).normalized()


@dataclass(frozen=True)
class JournalRecovery:
    segments: tuple[FocusSegment, ...]
    conflicts: tuple[str, ...]
    malformed_rows: int = 0


class FocusRecoveryJournal:
    """Append-only JSONL journal with idempotent replay and hash checks."""

    def __init__(self, base_dir: Path, *, now_provider: Any = None) -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "focus_recovery.jsonl"
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._known_hashes: dict[str, str] | None = None

    def _load_known_hashes(self) -> dict[str, str]:
        if self._known_hashes is not None:
            return self._known_hashes
        known: dict[str, str] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("event") == "segment_sealed":
                    segment_id = str(row.get("segment_id") or "").strip()
                    payload_hash = str(row.get("payload_hash") or "").strip()
                    if segment_id and payload_hash:
                        known[segment_id] = payload_hash
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        self._known_hashes = known
        return known

    def append_segment(self, segment: FocusSegment, *, reason: str) -> dict[str, Any]:
        payload = segment_payload(segment)
        if not payload["segment_id"] or not payload["session_id"]:
            raise ValueError("WAL segment identity is incomplete")
        payload_hash = segment_payload_hash(payload)
        known = self._load_known_hashes()
        previous_hash = known.get(payload["segment_id"])
        if previous_hash is not None:
            if previous_hash != payload_hash:
                raise ValueError("WAL segment identity has a conflicting payload")
            return {
                "schema_version": WAL_SCHEMA_VERSION,
                "event": "segment_sealed",
                **payload,
                "reason": str(reason or "duplicate")[:80],
                "payload_hash": payload_hash,
            }
        created = self._now()
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        row = {
            "schema_version": WAL_SCHEMA_VERSION,
            "event": "segment_sealed",
            "segment_id": payload["segment_id"],
            "session_id": payload["session_id"],
            "device_id": payload["device_id"],
            "start_at": payload["start_at"],
            "end_at": payload["end_at"],
            "completed": payload["completed"],
            "quality": payload["quality"],
            "task": payload["task"],
            "interruptions": payload["interruptions"],
            "reason": str(reason or "unknown")[:80],
            "created_at": created.isoformat(),
            "payload_hash": payload_hash,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = _canonical_json(row) + "\n"
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            # JSONL is intentionally small and append-only.  fsync is the
            # durability boundary before the local FocusSegment Store update.
            import os

            os.fsync(handle.fileno())
        known[payload["segment_id"]] = payload_hash
        return row

    def recover(self, existing: Iterable[FocusSegment]) -> JournalRecovery:
        by_id = {segment.segment_id: segment for segment in existing if segment.segment_id}
        recovered: dict[str, FocusSegment] = {}
        conflicts: list[str] = []
        malformed = 0
        if not self.path.is_file():
            return JournalRecovery((), (), 0)
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return JournalRecovery((), ("journal_unreadable",), 0)
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("event") != "segment_sealed":
                    raise ValueError("unsupported WAL event")
                if int(row.get("schema_version", 0)) != WAL_SCHEMA_VERSION:
                    raise ValueError("unsupported WAL schema")
                payload = {
                    key: row.get(key)
                    for key in (
                        "segment_id", "session_id", "device_id", "start_at",
                        "end_at", "completed", "quality", "task", "interruptions",
                    )
                }
                if str(row.get("payload_hash") or "") != segment_payload_hash(payload):
                    raise ValueError("WAL payload hash mismatch")
                segment = segment_from_payload(payload)
                if not segment.segment_id or not segment.session_id:
                    raise ValueError("WAL identity is incomplete")
            except (OSError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
                malformed += 1
                conflicts.append(f"line:{line_number}:{type(exc).__name__}")
                continue
            existing_segment = by_id.get(segment.segment_id)
            if existing_segment is not None:
                try:
                    if segment_payload(existing_segment) != segment_payload(segment):
                        conflicts.append(f"segment:{segment.segment_id}:store_conflict")
                except ValueError:
                    conflicts.append(f"segment:{segment.segment_id}:store_invalid")
                continue
            prior = recovered.get(segment.segment_id)
            if prior is not None and segment_payload(prior) != segment_payload(segment):
                conflicts.append(f"segment:{segment.segment_id}:journal_conflict")
                continue
            recovered[segment.segment_id] = segment
        return JournalRecovery(tuple(recovered.values()), tuple(conflicts), malformed)


def segment_as_store_record(segment: FocusSegment) -> dict[str, Any]:
    """Convert a recovered fact to the existing AccountFocusStore row shape."""

    value = segment.normalized()
    if value.end_at is None:
        raise ValueError("cannot store an open segment")
    return {
        "date": value.start_at.date().isoformat(),
        "started_at": value.start_at.isoformat(),
        "seconds": max(0, int((value.end_at - value.start_at).total_seconds())),
        "completed": bool(value.completed),
        "quality": value.quality,
        "task": value.task,
        "interruptions": value.interruptions,
        "record_id": value.segment_id,
        "session_id": value.session_id,
        "device_id": value.device_id,
        "end_at": value.end_at.isoformat(),
    }


__all__ = [
    "FocusRecoveryJournal",
    "JournalRecovery",
    "WAL_SCHEMA_VERSION",
    "segment_as_store_record",
    "segment_payload",
    "segment_payload_hash",
]
