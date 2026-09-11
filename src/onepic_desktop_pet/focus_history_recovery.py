"""Recovery of verifiable historical focus intervals.

The scanner never interprets daily or lifetime counters as work.  It accepts
only closed intervals with a stable local identity, then hands them to
``AccountFocusStore.commit_focus_segment`` so ordinary work and recovery share
the same WAL, pending upload, server upsert and ACK lifecycle.  Completed
scans are rechecked when an exact local history source changes, so a legacy
client that writes its interval after the first upgrade scan cannot strand the
fact permanently.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .focus_segments import (
    FocusSegment,
    deterministic_focus_segment_id,
    parse_focus_timestamp,
)
from .local_data import platform_app_data_root, read_json, write_json_atomic


FOCUS_HISTORY_RECOVERY_VERSION = 2
MAX_RECOVERY_LOG_RUNS = 20


@dataclass(frozen=True)
class FocusHistoryRecoveryReport:
    version: int
    started_at: str
    automatic: bool
    scanned: int
    recovered: int
    duplicates: int
    skipped: int
    upload_pending: int
    skip_reasons: dict[str, int]
    sources: dict[str, int]
    already_checked: bool = False


class FocusHistoryRecovery:
    """Bounded, idempotent scanner for exact local historical intervals."""

    def __init__(
        self,
        store: Any,
        *,
        account_id: str,
        device_id: str,
        account_dir: Path,
        diagnostics_dir: Path | None = None,
        now_provider: Any = None,
    ) -> None:
        self.store = store
        self.account_id = str(account_id or "").strip()
        self.device_id = str(device_id or "").strip()[:120]
        self.account_dir = Path(account_dir)
        self.diagnostics_dir = diagnostics_dir or (
            platform_app_data_root() / "Lili" / "diagnostics"
        )
        self.state_path = self.account_dir / "focus_history_recovery.json"
        self._now = now_provider or (lambda: datetime.now().astimezone())

    def run(self, *, force: bool = False) -> FocusHistoryRecoveryReport:
        state = read_json(self.state_path, {})
        if not isinstance(state, dict):
            state = {}
        source_signatures = self._source_signatures()
        if (
            not force
            and int(state.get("completed_version", 0) or 0) >= FOCUS_HISTORY_RECOVERY_VERSION
            and state.get("source_signatures") == source_signatures
        ):
            report = self._report(
                automatic=True,
                scanned=0,
                recovered=0,
                duplicates=0,
                skipped=0,
                reasons={},
                sources={},
                already_checked=True,
            )
            return report

        existing = list(self.store.focus_segments())
        candidates: list[tuple[str, FocusSegment]] = []
        reasons: Counter[str] = Counter()
        sources: Counter[str] = Counter()
        scanned = 0

        # The canonical store and its fsynced WAL are already replayed by the
        # store constructor. Count them for the audit report without creating
        # a second ingestion path.
        scanned += len(existing)
        sources["canonical_store"] += len(existing)
        wal_rows = self._count_wal_rows(self.account_dir / "focus_recovery.jsonl")
        scanned += wal_rows
        sources["recovery_wal"] += wal_rows

        work_candidates, work_scanned, work_reasons = self._work_session_candidates()
        candidates.extend(("work_session", item) for item in work_candidates)
        scanned += work_scanned
        sources["work_session"] += work_scanned
        reasons.update(work_reasons)

        lifecycle_candidates, lifecycle_scanned, lifecycle_reasons = self._lifecycle_candidates()
        candidates.extend(("lifecycle", item) for item in lifecycle_candidates)
        scanned += lifecycle_scanned
        sources["lifecycle"] += lifecycle_scanned
        reasons.update(lifecycle_reasons)

        scalar_count = self._count_unusable_scalars()
        scanned += scalar_count
        sources["scalar_only"] += scalar_count
        reasons["scalar_without_interval"] += scalar_count

        recovered = 0
        duplicates = 0
        for source, candidate in candidates:
            today = self._now().astimezone(candidate.start_at.tzinfo).date()
            if candidate.start_at.date() < today - timedelta(days=400) or candidate.start_at.date() > today:
                reasons["outside_server_retention"] += 1
                continue
            if self._equivalent_interval(candidate, existing):
                duplicates += 1
                continue
            try:
                changed = self.store.commit_focus_segment(
                    candidate,
                    source=f"history_recovery:{source}",
                    reason=f"history_recovery:{source}",
                )
            except (OSError, TypeError, ValueError, OverflowError):
                reasons["commit_failed"] += 1
                continue
            if changed:
                recovered += 1
                existing.append(candidate)
            else:
                duplicates += 1

        pending = len(self.store.focus_segments_payload())
        report = self._report(
            automatic=not force,
            scanned=scanned,
            recovered=recovered,
            duplicates=duplicates,
            skipped=sum(reasons.values()),
            reasons=dict(reasons),
            sources=dict(sources),
        )
        runs = state.get("runs") if isinstance(state.get("runs"), list) else []
        state.update(
            {
                "completed_version": FOCUS_HISTORY_RECOVERY_VERSION,
                "last_run_at": report.started_at,
                "last_report": asdict(report),
                "runs": [*runs[-(MAX_RECOVERY_LOG_RUNS - 1):], asdict(report)],
                "pending_after_scan": pending,
                "source_signatures": self._source_signatures(),
            }
        )
        write_json_atomic(self.state_path, state)
        return report

    def _source_paths(self) -> list[Path]:
        """Return exact local-history files that the scanner can interpret."""

        paths = [
            self.account_dir / "work_sessions.json",
            self.account_dir / "focus_recovery.jsonl",
        ]
        try:
            paths.extend(sorted(self.diagnostics_dir.glob("lifecycle.log*"))[-4:])
        except (OSError, ValueError):
            pass
        return list(dict.fromkeys(paths))

    def _source_signatures(self) -> dict[str, dict[str, int]]:
        """Return cheap change markers without reading private history content."""

        signatures: dict[str, dict[str, int]] = {}
        for path in self._source_paths():
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            signatures[str(path)] = {
                "size": max(0, int(stat.st_size)),
                "mtime_ns": max(0, int(stat.st_mtime_ns)),
            }
        return signatures

    def _report(
        self,
        *,
        automatic: bool,
        scanned: int,
        recovered: int,
        duplicates: int,
        skipped: int,
        reasons: dict[str, int],
        sources: dict[str, int],
        already_checked: bool = False,
    ) -> FocusHistoryRecoveryReport:
        moment = self._now()
        return FocusHistoryRecoveryReport(
            version=FOCUS_HISTORY_RECOVERY_VERSION,
            started_at=moment.isoformat(),
            automatic=automatic,
            scanned=max(0, int(scanned)),
            recovered=max(0, int(recovered)),
            duplicates=max(0, int(duplicates)),
            skipped=max(0, int(skipped)),
            upload_pending=len(self.store.focus_segments_payload()),
            skip_reasons={key: int(value) for key, value in sorted(reasons.items()) if value},
            sources={key: int(value) for key, value in sorted(sources.items()) if value},
            already_checked=already_checked,
        )

    @staticmethod
    def _count_wal_rows(path: Path) -> int:
        try:
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except (OSError, UnicodeError):
            return 0

    def _work_session_candidates(self) -> tuple[list[FocusSegment], int, Counter[str]]:
        raw = read_json(self.account_dir / "work_sessions.json", [])
        if not isinstance(raw, list):
            return [], 0, Counter({"work_session_invalid_file": 1})
        candidates: list[FocusSegment] = []
        reasons: Counter[str] = Counter()
        for row in raw[-500:]:
            if not isinstance(row, dict):
                reasons["work_session_invalid_row"] += 1
                continue
            start = parse_focus_timestamp(row.get("started_at"))
            end = parse_focus_timestamp(row.get("ended_at"))
            source_id = str(row.get("id") or "").strip()[:160]
            if start is None or end is None or not source_id:
                reasons["work_session_missing_interval"] += 1
                continue
            seconds = int((end - start).total_seconds())
            claimed = max(0, int(row.get("seconds", 0) or 0))
            if seconds <= 0 or seconds > 24 * 60 * 60 or abs(seconds - claimed) > 5:
                reasons["work_session_inconsistent_interval"] += 1
                continue
            session_id = f"history-work:{source_id}"
            candidates.append(
                FocusSegment(
                    segment_id=deterministic_focus_segment_id(
                        self.device_id or "legacy-device", session_id, start, end
                    ),
                    session_id=session_id,
                    device_id=self.device_id,
                    start_at=start,
                    end_at=end,
                    completed=bool(row.get("completed")),
                    task=str(row.get("task_id") or "")[:120],
                )
            )
        return candidates, len(raw[-500:]), reasons

    def _lifecycle_candidates(self) -> tuple[list[FocusSegment], int, Counter[str]]:
        candidates: list[FocusSegment] = []
        reasons: Counter[str] = Counter()
        scanned = 0
        paths = sorted(self.diagnostics_dir.glob("lifecycle.log*"))[-4:]
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[-5000:]
            except (OSError, UnicodeError):
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict) or row.get("event") != "focus.segment.sealed":
                    continue
                scanned += 1
                row_device = str(row.get("device_id") or "").strip()[:120]
                if not self.device_id or row_device != self.device_id:
                    reasons["lifecycle_other_account_or_device"] += 1
                    continue
                start = parse_focus_timestamp(row.get("start_at"))
                end = parse_focus_timestamp(row.get("end_at"))
                segment_id = str(row.get("segment_id") or "").strip()[:160]
                session_id = str(row.get("session_id") or "").strip()[:160]
                if start is None or end is None or not segment_id or not session_id:
                    reasons["lifecycle_missing_interval"] += 1
                    continue
                if end <= start or (end - start).total_seconds() > 24 * 60 * 60:
                    reasons["lifecycle_invalid_interval"] += 1
                    continue
                candidates.append(
                    FocusSegment(
                        segment_id=segment_id,
                        session_id=session_id,
                        device_id=row_device,
                        start_at=start,
                        end_at=end,
                        completed=bool(row.get("completed")),
                    )
                )
        return candidates, scanned, reasons

    def _count_unusable_scalars(self) -> int:
        raw = read_json(self.account_dir / "work_timer.json", {})
        if not isinstance(raw, dict):
            return 0
        count = 0
        for key in ("accumulated_seconds", "lifetime_seconds"):
            try:
                if int(raw.get(key, 0) or 0) > 0:
                    count += 1
            except (TypeError, ValueError, OverflowError):
                continue
        return count

    @staticmethod
    def _equivalent_interval(candidate: FocusSegment, existing: Iterable[FocusSegment]) -> bool:
        if candidate.end_at is None:
            return True
        for item in existing:
            if item.end_at is None:
                continue
            if item.segment_id == candidate.segment_id:
                return True
            overlap = min(item.end_at, candidate.end_at) - max(item.start_at, candidate.start_at)
            overlap_seconds = max(0, int(overlap.total_seconds()))
            candidate_seconds = max(1, int((candidate.end_at - candidate.start_at).total_seconds()))
            item_seconds = max(1, int((item.end_at - item.start_at).total_seconds()))
            if (
                overlap_seconds >= int(min(candidate_seconds, item_seconds) * 0.98)
                and abs(candidate_seconds - item_seconds) <= 60
            ):
                return True
        return False


__all__ = [
    "FOCUS_HISTORY_RECOVERY_VERSION",
    "FocusHistoryRecovery",
    "FocusHistoryRecoveryReport",
]
