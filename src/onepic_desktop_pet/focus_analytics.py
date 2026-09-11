"""Local focus continuity, quality and lightweight review data.

Only coarse metrics are stored: duration, round count, away count and
application *categories*.  Window titles, document names, keystrokes and
mouse coordinates never enter this file.  The module is intentionally
transport-free so the desktop pet remains useful when the social backend is
offline.  Period summaries are calculated on demand from the account-scoped
history; no report images or extra server-side report rows are created.  Raw
focus intervals are the canonical source for a timeline; legacy daily
counters are explicit compatibility evidence for calendar totals and never
become synthetic intervals or override a canonical timeline.
Successful sealed-fact uploads are remembered only as compact local digests,
so unchanged rows and facts downloaded from other devices are not re-uploaded.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from .focus_segments import (
    BEIJING_TIMEZONE as FOCUS_BEIJING_TIMEZONE,
    FocusAggregate,
    FocusSegment,
    aggregate_focus_time,
    as_beijing,
    calendar_window,
    deterministic_focus_segment_id,
    parse_focus_timestamp,
    segment_from_record,
)
from .focus_durability import (
    FocusRecoveryJournal,
    segment_as_store_record,
    segment_payload,
)
from .local_data import account_local_data_path, adopt_legacy_account_file


# A day may legitimately contain a very long work period.  The only hard
# upper bound is an impossible full 24-hour day; older releases incorrectly
# treated anything above eight hours as anomalous.
MAX_ANALYTICS_DAY_SECONDS = 24 * 60 * 60 - 1
INTERRUPTION_GRACE_SECONDS = 10 * 60
BEIJING_TIMEZONE = FOCUS_BEIJING_TIMEZONE
# Version 2 still trusted an RPC-wide success response.  The production RPC
# could swallow a per-row permission error and return success without having
# inserted that row, which stranded a sealed fact behind a local fingerprint.
# Version 3 requires an explicit server acknowledgement for every segment id
# and performs one bounded, idempotent recovery upload for older fingerprints.
FOCUS_SEGMENT_UPLOAD_ACK_VERSION = 3
# A compact integrity manifest is checked at most once per day.  It contains
# only stable ids for locally-owned sealed facts that this device previously
# persisted as server-acknowledged; it never downloads or uploads history.
FOCUS_SEGMENT_INTEGRITY_AUDIT_VERSION = 1
FOCUS_SEGMENT_INTEGRITY_AUDIT_INTERVAL = timedelta(hours=24)
# A failed audit is retried on the next daily window, not on every passive
# dashboard tick.  Missing facts are still repaired through the normal
# targeted segment-id backfill once an audit succeeds.
FOCUS_SEGMENT_INTEGRITY_RETRY_SECONDS = 24 * 60 * 60
# A malformed/partial acknowledgement must remain retryable, but retrying a
# bounded batch on every 30-second social tick can create an egress storm when
# a relay is unhealthy.  The normal delta read continues during this cooldown;
# only the same dirty upload batch is held back.
FOCUS_SEGMENT_UPLOAD_RETRY_SECONDS = 60
# Recovery can discover many historical rows at once. Keep each network
# transaction small; acknowledged rows stay quiet and remaining rows continue
# on later coalesced sync ticks.
FOCUS_SEGMENT_UPLOAD_BATCH_SIZE = 100


def _as_beijing(value: datetime) -> datetime:
    """Compatibility wrapper around the shared focus timestamp normalizer."""

    return as_beijing(value)


def focus_analytics_path(account_id: str | None = None) -> Path:
    """Return the native, account-scoped focus ledger path on every OS."""

    return account_local_data_path("focus_analytics.json", account_id)


@dataclass(frozen=True)
class FocusQuality:
    score: int
    label: str


@dataclass(frozen=True)
class FocusAnalyticsSummary:
    date: str
    today_rounds: int
    current_streak_days: int
    longest_streak_days: int
    weekly_total_seconds: int
    yesterday_seconds: int | None
    difference_vs_yesterday_seconds: int | None
    average_quality: int
    quality_label: str
    high_efficiency_window: str
    late_night_average_seconds: int
    today_interruptions: int = 0
    current_interruptions: int = 0
    longest_continuous_seconds: int = 0
    current_continuous_seconds: int = 0
    # The server-confirmed daily value is kept separately from the local
    # record history so a new computer can render the same account totals.
    today_seconds: int | None = None


class FocusQualityTracker:
    """Collect session-local quality signals without collecting private text."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started_at = 0.0
        self.application_switches = 0
        self.away_count = 0
        self._last_category = ""

    def start(self, category: str = "other") -> None:
        self.reset()
        self.started_at = datetime.now().timestamp()
        self._last_category = str(category or "other")

    def note_application_switch(self, category: str) -> None:
        clean = str(category or "other")
        if self.started_at and self._last_category and clean != self._last_category:
            self.application_switches += 1
        self._last_category = clean

    def note_away(self) -> None:
        if self.started_at:
            self.away_count += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "application_switches": max(0, int(self.application_switches)),
            "away_count": max(0, int(self.away_count)),
        }


def score_focus_quality(seconds: int, application_switches: int = 0, away_count: int = 0) -> FocusQuality:
    """Return a stable, explainable quality score for one focus round."""

    duration = max(0, int(seconds))
    score = 48 + min(32, duration // 100) + min(20, duration // 600)
    score -= max(0, int(application_switches)) * 4
    score -= max(0, int(away_count)) * 9
    score = max(0, min(100, score))
    if score >= 82 and duration >= 25 * 60:
        label = "很深的一轮"
    elif score >= 62:
        label = "这一轮比较稳"
    else:
        label = "切换有点多"
    return FocusQuality(score, label)


class FocusAnalyticsStore:
    """Persist a bounded local history and derive continuity summaries."""

    def __init__(
        self,
        path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
        device_id: str = "",
    ) -> None:
        self._uses_explicit_path = path is not None
        self.path = path or focus_analytics_path()
        if path is None:
            # The old macOS/Linux path lived below ~/.desktop_pet.  Adopt only
        # this account's exact structured ledger and WAL; runtime log text
            # and aggregate counters are never converted into FocusSegments.
            adopt_legacy_account_file(
                "focus_recovery.jsonl",
                None,
                destination=self.path.parent / "focus_recovery.jsonl",
            )
            legacy_focus_source = adopt_legacy_account_file(
                "focus_analytics.json",
                None,
                destination=self.path,
            )
        else:
            legacy_focus_source = None
        self._now = now_provider or (lambda: datetime.now(BEIJING_TIMEZONE))
        self._persist = bool(persist)
        self._device_id = str(device_id or "").strip()[:120]
        self._legacy_focus_store_adopted = legacy_focus_source is not None
        self._focus_segment_source_by_id: dict[str, str] = {}
        self._recovery_journal = FocusRecoveryJournal(self.path.parent, now_provider=self._now)
        self._durability_conflicts: tuple[str, ...] = ()
        self._state: dict[str, Any] = {
            "days": {},
            "legacy_daily": {},
            "records": [],
            "reviews": {},
            "current_task": None,
            "account_state": {},
        }
        # ``records`` are the durable account ledger.  These live intervals
        # are a read-only projection supplied by the per-device presence RPC;
        # they are intentionally never serialized or uploaded as facts.
        self._live_projection_segments: list[FocusSegment] = []
        self._live_projection_expires_at: float | None = None
        # The dashboard's effective day/week projection is a read-only
        # cross-device display floor. It is separate from ``records`` and
        # never becomes uploadable focus evidence.
        self._remote_effective_projection: dict[str, Any] | None = None
        self._focus_integrity_next_attempt_monotonic = 0.0
        self._focus_upload_retry_not_before_monotonic = 0.0
        self._focus_sync_metrics_date = ""
        self._focus_sync_metrics: dict[str, int] = {}
        self._live: dict[str, Any] = {
            "session_active": False,
            "running": False,
            "paused_at": None,
            "continuous_started_at": None,
            "current_interruptions": 0,
            "current_continuous_seconds": 0,
        }
        if self._persist:
            self._load()
            if self._legacy_focus_store_adopted:
                for raw in self._state.get("records", []):
                    if isinstance(raw, dict):
                        segment_id = str(
                            raw.get("record_id") or raw.get("segment_id") or ""
                        ).strip()[:160]
                        if segment_id:
                            self._focus_segment_source_by_id.setdefault(
                                segment_id, "legacy_migrated_store"
                            )
            journal_changed = self._recover_focus_journal()
        else:
            journal_changed = False
        upload_state_changed = self._ensure_focus_segment_upload_state()
        # ``days.seconds`` is derived data.  Older releases added cumulative
        # timer checkpoints as if they were independent sessions, which could
        # produce impossible values such as 38 hours in one calendar day.
        # Rebuild from the raw records on load while keeping those records for
        # diagnostics and future migrations.
        projection_changed = self._ensure_daily_focus_projection()
        if (
            self._rebuild_days_from_records()
            or self._trim_days()
            or self._trim_legacy_daily()
            or projection_changed
            or upload_state_changed
            or journal_changed
        ):
            self._save()

    def switch_account(self, account_id: str | None) -> bool:
        """切换本地专注分析命名空间，避免账号之间复用历史记录。"""

        if not self._persist or self._uses_explicit_path:
            return False
        target = focus_analytics_path(account_id)
        if self.path == target:
            return False
        self._save()
        self.path = target
        # Copy WAL first: if a process stops between these two atomic copies,
        # journal replay still reconstructs the exact sealed intervals.
        adopt_legacy_account_file(
            "focus_recovery.jsonl",
            account_id,
            destination=self.path.parent / "focus_recovery.jsonl",
        )
        legacy_focus_source = adopt_legacy_account_file(
            "focus_analytics.json",
            account_id,
            destination=self.path,
        )
        self._legacy_focus_store_adopted = legacy_focus_source is not None
        self._focus_segment_source_by_id = {}
        self._recovery_journal = FocusRecoveryJournal(self.path.parent, now_provider=self._now)
        # Device attribution is rebound by the account/session owner after the
        # account switch. Never carry an installation ID across accounts.
        self._device_id = ""
        self._state = {
            "days": {},
            "legacy_daily": {},
            "records": [],
            "reviews": {},
            "current_task": None,
            "account_state": {},
        }
        self._live = {
            "session_active": False,
            "running": False,
            "paused_at": None,
            "continuous_started_at": None,
            "current_interruptions": 0,
            "current_continuous_seconds": 0,
        }
        self._remote_effective_projection = None
        self._load()
        if self._legacy_focus_store_adopted:
            for raw in self._state.get("records", []):
                if isinstance(raw, dict):
                    segment_id = str(
                        raw.get("record_id") or raw.get("segment_id") or ""
                    ).strip()[:160]
                    if segment_id:
                        self._focus_segment_source_by_id.setdefault(
                            segment_id, "legacy_migrated_store"
                        )
        journal_changed = self._recover_focus_journal()
        # Account switching happens after the restored Supabase session is
        # known.  Run upload-ACK migration for the actual account, not only
        # for the anonymous store constructed during application startup.
        upload_state_changed = self._ensure_focus_segment_upload_state()
        self._focus_integrity_next_attempt_monotonic = 0.0
        self._focus_upload_retry_not_before_monotonic = 0.0
        self._focus_sync_metrics_date = ""
        self._focus_sync_metrics = {}
        projection_changed = self._ensure_daily_focus_projection()
        if (
            self._rebuild_days_from_records()
            or self._trim_days()
            or self._trim_legacy_daily()
            or projection_changed
            or journal_changed
            or upload_state_changed
        ):
            self._save()
        return True

    def set_device_id(self, device_id: str | None) -> None:
        """Attribute only newly observed facts to this installation."""

        self._device_id = str(device_id or "").strip()[:120]

    def current_time(self) -> datetime:
        """Return the clock used by this store's calendar projections."""

        return _as_beijing(self._now())

    def merge_remote_state(
        self,
        *,
        focus_date: str | None = None,
        today_seconds: int = 0,
        lifetime_seconds: int = 0,
        week_start: str | None = None,
        week_seconds: int = 0,
    ) -> bool:
        """Merge account totals received from Supabase without double counting.

        The detailed history remains local.  The scalar day/week totals are
        compatibility inputs only and are deliberately ignored for metrics:
        a cached aggregate is not evidence of a FocusSession and must never
        revive a stale day or week.
        """

        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        changed = False

        lifetime = max(0, int(lifetime_seconds or 0))
        if lifetime > max(0, int(state.get("focus_lifetime_seconds", 0) or 0)):
            state["focus_lifetime_seconds"] = lifetime
            changed = True

        # Remove counters written by older releases.  Keeping them in the
        # file is harmless, but leaving them available makes a future caller
        # accidentally reintroduce the stale-cache bug this store prevents.
        for key in (
            "focus_date",
            "focus_today_seconds",
            "focus_week_start",
            "focus_week_seconds",
        ):
            if key in state:
                state.pop(key, None)
                changed = True

        if changed:
            self._save()
        return changed

    def set_remote_effective_projection(
        self,
        *,
        focus_date: str | None,
        today_seconds: int,
        week_start: str | None,
        week_seconds: int,
    ) -> bool:
        """Cache server effective totals for display, never as raw facts.

        The caller must obtain these values from a dashboard response marked
        as an effective projection. They never enter ``records``, daily raw
        aggregation, upload payloads, or legacy backfill.
        """

        current = self.current_time()
        current_date = current.date().isoformat()
        current_week = (
            current.date() - timedelta(days=current.date().weekday())
        ).isoformat()
        date_value = str(focus_date or current_date).strip()[:10]
        week_value = str(week_start or current_week).strip()[:10]
        if date_value != current_date or week_value != current_week:
            return False
        try:
            today_value = max(
                0, min(MAX_ANALYTICS_DAY_SECONDS, int(today_seconds or 0))
            )
            week_value_seconds = max(
                0, min(7 * 24 * 60 * 60, int(week_seconds or 0))
            )
        except (TypeError, ValueError, OverflowError):
            return False
        projection = {
            "focus_date": date_value,
            "today_seconds": today_value,
            "week_start": week_value,
            "week_seconds": week_value_seconds,
        }
        if projection == self._remote_effective_projection:
            return False
        self._remote_effective_projection = projection
        return True

    def remote_effective_projection(
        self, at: datetime | None = None
    ) -> dict[str, int] | None:
        """Return the current account-wide display projection, if available."""

        projection = self._remote_effective_projection
        if not isinstance(projection, dict):
            return None
        moment = _as_beijing(at or self._now())
        current_week = (
            moment.date() - timedelta(days=moment.date().weekday())
        ).isoformat()
        if (
            str(projection.get("focus_date") or "") != moment.date().isoformat()
            or str(projection.get("week_start") or "") != current_week
        ):
            return None
        return {
            "today_seconds": max(0, int(projection.get("today_seconds") or 0)),
            "week_seconds": max(0, int(projection.get("week_seconds") or 0)),
        }

    def merge_remote_history(self, payload: Any) -> bool:
        """Merge explicit server legacy evidence without creating intervals.

        ``seconds`` alone is intentionally not imported.  Only the new RPC's
        explicit ``legacy_seconds`` field is accepted, so an old effective
        projection cannot silently become a second local source of truth.
        """

        rows = payload.get("days") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return False
        ledger = self._state.setdefault("legacy_daily", {})
        if not isinstance(ledger, dict):
            ledger = {}
            self._state["legacy_daily"] = ledger
        changed = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            focus_date = str(item.get("focus_date") or item.get("date") or "")[:10]
            try:
                parsed = date.fromisoformat(focus_date)
                legacy_seconds = max(
                    0,
                    min(
                        MAX_ANALYTICS_DAY_SECONDS,
                        int(item.get("legacy_seconds", item.get("legacy_daily_seconds", 0)) or 0),
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                continue
            if parsed > self.current_time().date() or legacy_seconds <= 0:
                continue
            key = parsed.isoformat()
            previous = ledger.get(key)
            previous_seconds = 0
            if isinstance(previous, dict):
                try:
                    previous_seconds = max(0, int(previous.get("seconds", 0) or 0))
                except (TypeError, ValueError, OverflowError):
                    previous_seconds = 0
            if legacy_seconds < previous_seconds:
                continue
            try:
                canonical_seconds = max(0, int(item.get("canonical_seconds", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                canonical_seconds = 0
            entry = {
                "seconds": legacy_seconds,
                "source": str(
                    item.get("time_source")
                    or item.get("legacy_source")
                    or "server_legacy_compatibility"
                )[:80],
                "canonical_seconds": canonical_seconds,
            }
            if entry != previous:
                ledger[key] = entry
                changed = True
        if changed:
            self._save()
        return changed

    def focus_segments_payload(self, limit: int = 500) -> list[dict[str, Any]]:
        """Serialize only closed local facts not acknowledged by the server."""

        rows, _diagnostics = self.focus_segments_payload_with_diagnostics(limit)
        return rows

    def focus_segments_payload_with_diagnostics(
        self, limit: int = 500
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return pending facts plus diagnostic-only local provenance."""

        if monotonic() < self._focus_upload_retry_not_before_monotonic:
            # Keep the ordinary delta read alive, but do not resend an
            # unchanged dirty batch while a relay/backend is failing.  A
            # successful acknowledgement clears the cooldown immediately.
            return [], {
                "source_entries": [],
                "input_count": 0,
                "retry_cooldown": True,
            }
        rows: list[dict[str, Any]] = []
        state = self._state.get("account_state")
        acknowledgements = (
            state.get("focus_segment_upload_fingerprints", {})
            if isinstance(state, dict)
            else {}
        )
        if not isinstance(acknowledgements, dict):
            acknowledgements = {}
        repair_pending = bool(
            isinstance(state, dict)
            and state.get("focus_segment_upload_repair_pending")
        )
        targeted_repair_ids = {
            str(value).strip()[:160]
            for value in (
                state.get("focus_segment_repair_segment_ids", [])
                if isinstance(state, dict)
                else []
            )
            if isinstance(value, str) and str(value).strip()
        }
        max_rows = max(1, min(500, int(limit)))
        source_entries: list[dict[str, str]] = []
        for segment in self.focus_segments():
            if segment.end_at is None:
                # Active intervals are projected locally and sealed on pause;
                # never make a remote device guess an end timestamp.
                continue
            # A one-time repair is deliberately allowed to include the
            # account's bounded local ledger. It is needed for upgrades from
            # the release that could persist an acknowledgement before the
            # server transaction actually inserted the row. Existing rows are
            # idempotent and unchanged rows do not refresh updated_at. Once
            # this batch is acknowledged, the normal owner filter below again
            # prevents echoing downloaded facts from another device.
            targeted_repair = segment.segment_id in targeted_repair_ids
            if (
                not repair_pending
                and not targeted_repair
                and segment.device_id
                and segment.device_id != self._device_id
            ):
                continue
            payload = segment.to_dict()
            fingerprint = self._focus_segment_upload_fingerprint(payload)
            if str(acknowledgements.get(segment.segment_id) or "") == fingerprint:
                continue
            rows.append(payload)
            source_entries.append(
                {
                    "segment_id": segment.segment_id,
                    "source": self._focus_segment_source_by_id.get(
                        segment.segment_id, "canonical_focus_store"
                    ),
                }
            )
            if len(rows) >= max_rows:
                break
        return rows, {
            "source_entries": source_entries,
            "input_count": len(rows),
            "retry_cooldown": False,
        }

    def note_focus_segment_upload_result(
        self,
        *,
        attempted_count: int,
        success: bool,
    ) -> None:
        """Bound retries for a dirty upload batch without changing facts.

        This is deliberately process-local transport state.  It never changes
        segment identity, the delta cursor, acknowledgement fingerprints or
        AccountFocusStore merge semantics.
        """

        if int(attempted_count or 0) <= 0:
            return
        if success:
            self._focus_upload_retry_not_before_monotonic = 0.0
            return
        self._focus_upload_retry_not_before_monotonic = (
            monotonic() + FOCUS_SEGMENT_UPLOAD_RETRY_SECONDS
        )

    def _ensure_focus_sync_metrics_day(self) -> None:
        current = self.current_time().date().isoformat()
        if current == self._focus_sync_metrics_date:
            return
        self._focus_sync_metrics_date = current
        self._focus_sync_metrics = {
            "delta_rpc_calls": 0,
            "integrity_rpc_calls": 0,
            "reconciliation_rpc_calls": 0,
            "upload_rows": 0,
            "returned_segment_rows": 0,
            "manifest_rows": 0,
            "request_bytes": 0,
            "response_bytes": 0,
            "manifest_bytes": 0,
            "full_bootstrap_count": 0,
        }

    def record_focus_sync_metrics(self, metrics: Any) -> None:
        """Accumulate bounded transport counters for the current Beijing day.

        These counters are diagnostics only.  They are intentionally not
        persisted into the focus ledger, because egress accounting must never
        become a source of duration or synchronization truth.
        """

        if not isinstance(metrics, dict):
            return
        self._ensure_focus_sync_metrics_day()
        for key in self._focus_sync_metrics:
            try:
                value = max(0, int(metrics.get(key) or 0))
            except (TypeError, ValueError, OverflowError):
                value = 0
            self._focus_sync_metrics[key] += value

    def focus_sync_metrics_snapshot(self) -> dict[str, Any]:
        """Return per-device, per-Beijing-day sync/egress diagnostics."""

        self._ensure_focus_sync_metrics_day()
        return {
            "date": self._focus_sync_metrics_date,
            "account_scoped": True,
            "device_id": self._device_id,
            **dict(self._focus_sync_metrics),
            "upload_retry_cooldown_seconds": FOCUS_SEGMENT_UPLOAD_RETRY_SECONDS,
            "integrity_interval_seconds": int(
                FOCUS_SEGMENT_INTEGRITY_AUDIT_INTERVAL.total_seconds()
            ),
        }

    def focus_segments_sync_mode(self) -> str:
        """Return the bounded transport mode for diagnostics and UI sync."""

        state = self._state.get("account_state")
        if isinstance(state, dict) and state.get("focus_segment_repair_segment_ids"):
            return "reconciliation_backfill"
        if isinstance(state, dict) and state.get("focus_segment_upload_repair_pending"):
            return "recovery_backfill"
        return "delta"

    def _ensure_focus_segment_upload_state(self) -> bool:
        """Detect acknowledgements written before the production grant fix.

        This is intentionally a one-time local migration. It does not reset
        the delta cursor and never causes a historical download. A bounded
        upload of the local sealed ledger repairs rows that were stranded by
        the old permission failure; the server's conflict clause keeps
        unchanged rows from acquiring a new ``updated_at`` value.
        """

        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        version = state.get("focus_segment_upload_ack_version")
        fingerprints = state.get("focus_segment_upload_fingerprints")
        changed = False
        if fingerprints and version != FOCUS_SEGMENT_UPLOAD_ACK_VERSION:
            state["focus_segment_upload_ack_version"] = FOCUS_SEGMENT_UPLOAD_ACK_VERSION
            state["focus_segment_upload_repair_pending"] = True
            # Do not retain stale acknowledgements: they are precisely what
            # could have hidden a local sealed fact during the permission
            # incident. The repair is retried until its post-RPC ack persists.
            state.pop("focus_segment_upload_fingerprints", None)
            changed = True
        elif version == FOCUS_SEGMENT_UPLOAD_ACK_VERSION:
            # Keep the state compact and repair flags explicit across reloads.
            if "focus_segment_upload_repair_pending" not in state:
                state["focus_segment_upload_repair_pending"] = False
                changed = True
        return changed

    @staticmethod
    def _focus_segment_upload_fingerprint(payload: dict[str, Any]) -> str:
        """Return a task-safe digest for one canonical upload payload."""

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def acknowledge_focus_segments_upload(self, payload: Any) -> bool:
        """Persist successful upload fingerprints so unchanged facts stay quiet.

        A transport failure never calls this method. If persistence fails, the
        previous state is restored and the same small batch remains retryable.
        """

        if not isinstance(payload, list):
            return False
        acknowledgements: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            segment_id = str(item.get("segment_id") or "").strip()[:160]
            if not segment_id:
                continue
            acknowledgements[segment_id] = self._focus_segment_upload_fingerprint(item)
        if not acknowledgements:
            return False
        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        existing = state.get("focus_segment_upload_fingerprints")
        if not isinstance(existing, dict):
            existing = {}
        targeted_repair_ids = {
            str(value).strip()[:160]
            for value in state.get("focus_segment_repair_segment_ids", [])
            if isinstance(value, str) and str(value).strip()
        }
        updated = dict(existing)
        updated.update(acknowledgements)
        # The local ledger is bounded to 500 facts. Keep acknowledgement state
        # bounded as well, retaining only ids still represented in the ledger.
        valid_ids = {segment.segment_id for segment in self.focus_segments()}
        updated = {
            segment_id: fingerprint
            for segment_id, fingerprint in updated.items()
            if segment_id in valid_ids
        }
        uploaded_ids = {
            str(item.get("segment_id") or "").strip()[:160]
            for item in payload
            if isinstance(item, dict) and str(item.get("segment_id") or "").strip()
        }
        handoff_id = str(state.get("focus_handoff_segment_id") or "").strip()[:160]
        remaining_repair_ids = targeted_repair_ids - uploaded_ids
        handoff_acked = bool(handoff_id and handoff_id in uploaded_ids)
        if updated == existing and remaining_repair_ids == targeted_repair_ids and not handoff_acked:
            return False
        previous = copy.deepcopy(self._state)
        state["focus_segment_upload_fingerprints"] = updated
        state["focus_segment_upload_ack_version"] = FOCUS_SEGMENT_UPLOAD_ACK_VERSION
        if handoff_acked:
            state.pop("focus_handoff_segment_id", None)
        if state.get("focus_segment_upload_repair_pending"):
            closed_count = sum(
                1 for segment in self.focus_segments() if segment.end_at is not None
            )
            # The store is bounded to 500 rows and the transport batch is
            # bounded to the same size. If every closed local fact was in the
            # successful transaction, the one-time recovery is complete.
            if len(payload) >= closed_count:
                state["focus_segment_upload_repair_pending"] = False
        if targeted_repair_ids:
            state["focus_segment_repair_segment_ids"] = sorted(remaining_repair_ids)
        try:
            self._save()
        except Exception:
            self._state = previous
            raise
        return True

    def focus_segment_integrity_manifest(self, limit: int = 500) -> list[str]:
        """Return a bounded, low-frequency manifest of acknowledged local facts.

        The manifest repairs a narrow failure mode: an old client may have
        saved a SHA acknowledgement even though the corresponding sealed row
        never reached Supabase.  Only ids are sent, and only once per day (or
        once per process retry cooldown after transport failure), so this does
        not turn the delta protocol back into a historical full sync.
        """

        if monotonic() < self._focus_integrity_next_attempt_monotonic:
            return []
        state = self._state.get("account_state")
        if not isinstance(state, dict):
            return []
        last_success = parse_focus_timestamp(
            state.get("focus_segment_integrity_last_success_at")
        )
        current = self.current_time()
        audit_version = int(state.get("focus_segment_integrity_audit_version") or 0)
        if (
            audit_version == FOCUS_SEGMENT_INTEGRITY_AUDIT_VERSION
            and last_success is not None
            and current - last_success < FOCUS_SEGMENT_INTEGRITY_AUDIT_INTERVAL
        ):
            return []
        acknowledgements = state.get("focus_segment_upload_fingerprints")
        if not isinstance(acknowledgements, dict) or not acknowledgements:
            return []
        max_rows = max(1, min(500, int(limit)))
        manifest = sorted(
            {
                segment.segment_id
                for segment in self.focus_segments()
                if segment.end_at is not None
                and segment.segment_id in acknowledgements
                and (not segment.device_id or segment.device_id == self._device_id)
            }
        )[:max_rows]
        if manifest:
            # A missing/old relay must not make the ordinary 30-second social
            # cycle retry this optional check.  This timer is intentionally
            # transient: restarting the app remains an escape hatch.
            self._focus_integrity_next_attempt_monotonic = (
                monotonic() + FOCUS_SEGMENT_INTEGRITY_RETRY_SECONDS
            )
        return manifest

    def focus_segment_reconciliation_manifest(self, limit: int = 500) -> list[str]:
        """Return all local sealed ids for a low-frequency convergence audit.

        Unlike the legacy acknowledgement audit, this deliberately includes
        downloaded facts from other devices.  It is an id-only manifest and is
        rate-limited; it never downloads the history by itself.
        """

        if monotonic() < self._focus_integrity_next_attempt_monotonic:
            return []
        state = self._state.get("account_state")
        if not isinstance(state, dict):
            return []
        last_success = parse_focus_timestamp(
            state.get("focus_segment_integrity_last_success_at")
        )
        current = self.current_time()
        audit_version = int(state.get("focus_segment_integrity_audit_version") or 0)
        if (
            audit_version == FOCUS_SEGMENT_INTEGRITY_AUDIT_VERSION
            and last_success is not None
            and current - last_success < FOCUS_SEGMENT_INTEGRITY_AUDIT_INTERVAL
        ):
            return []
        max_rows = max(1, min(500, int(limit)))
        manifest = sorted(
            {
                segment.segment_id
                for segment in self.focus_segments()
                if segment.end_at is not None and segment.segment_id
            }
        )[:max_rows]
        if manifest:
            self._focus_integrity_next_attempt_monotonic = (
                monotonic() + FOCUS_SEGMENT_INTEGRITY_RETRY_SECONDS
            )
        return manifest

    def apply_focus_segment_reconciliation_audit(
        self,
        payload: Any,
    ) -> tuple[bool, int, int]:
        """Merge targeted cloud gaps and queue local gaps for backfill.

        Returns ``(success, local_requeued_count, recovered_remote_count)``.
        The normal delta path remains unchanged; this is a low-frequency
        convergence repair for a cursor that may already have passed a row.
        """

        if not isinstance(payload, dict):
            return False, 0, 0
        requested_raw = payload.get("_requested_segment_ids")
        missing_raw = payload.get("missing_segment_ids")
        recovery_raw = payload.get("missing_local_segments", [])
        if (
            not isinstance(requested_raw, list)
            or not isinstance(missing_raw, list)
            or not isinstance(recovery_raw, list)
            or not isinstance(payload.get("server_manifest"), list)
        ):
            return False, 0, 0
        if payload.get("server_manifest_complete") is False:
            return False, 0, 0
        requested = {
            str(value).strip()[:160]
            for value in requested_raw
            if isinstance(value, str) and str(value).strip()
        }
        missing = {
            str(value).strip()[:160]
            for value in missing_raw
            if isinstance(value, str) and str(value).strip()
        }
        if not requested or not missing.issubset(requested):
            return False, 0, 0
        try:
            checked_count = int(payload.get("checked_count"))
            present_count = int(payload.get("present_count"))
            missing_count = int(payload.get("missing_count"))
        except (TypeError, ValueError, OverflowError):
            return False, 0, 0
        if (
            checked_count != len(requested)
            or missing_count != len(missing)
            or present_count + missing_count != checked_count
        ):
            return False, 0, 0

        # The server only returns rows whose ids are absent from the local
        # manifest. Validate them before merging so an integrity reply cannot
        # inject an unrelated account row.
        recovery_ids: set[str] = set()
        for index, item in enumerate(recovery_raw):
            if not isinstance(item, dict):
                return False, 0, 0
            segment = segment_from_record(item, index)
            if segment is None or segment.end_at is None or not segment.segment_id:
                return False, 0, 0
            recovery_ids.add(segment.segment_id)
        previous = copy.deepcopy(self._state)
        recovered_count = 0
        if recovery_raw:
            try:
                _changed, recovered_count = self.merge_remote_segments_with_count(
                    {"segments": recovery_raw}
                )
            except Exception:
                self._state = previous
                return False, 0, 0

        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            self._state = previous
            return False, 0, 0
        existing = state.get("focus_segment_upload_fingerprints")
        if not isinstance(existing, dict):
            existing = {}
        updated = dict(existing)
        for segment_id in missing:
            updated.pop(segment_id, None)
        pending = {
            str(value).strip()[:160]
            for value in state.get("focus_segment_repair_segment_ids", [])
            if isinstance(value, str) and str(value).strip()
        }
        pending.update(missing)
        state["focus_segment_upload_fingerprints"] = updated
        state["focus_segment_repair_segment_ids"] = sorted(pending)
        state["focus_segment_integrity_audit_version"] = FOCUS_SEGMENT_INTEGRITY_AUDIT_VERSION
        state["focus_segment_integrity_last_success_at"] = self.current_time().isoformat()
        state["focus_segment_integrity_last_checked_count"] = checked_count
        state["focus_segment_integrity_last_present_count"] = present_count
        state["focus_segment_integrity_last_missing_count"] = missing_count
        state["focus_segment_integrity_last_recovered_count"] = recovered_count
        try:
            self._save()
        except Exception:
            self._state = previous
            raise
        return True, len(missing), recovered_count

    def apply_focus_segment_integrity_audit(
        self, payload: Any
    ) -> tuple[bool, int]:
        """Validate an audit reply and requeue only server-missing sealed ids.

        Returns ``(success, requeued_count)``.  A malformed response or local
        persistence failure cannot clear acknowledgements or mark the audit
        successful.  The normal idempotent delta upload performs the repair
        on the next coalesced social tick.
        """

        if not isinstance(payload, dict):
            return False, 0
        requested_raw = payload.get("_requested_segment_ids")
        missing_raw = payload.get("missing_segment_ids")
        if not isinstance(requested_raw, list) or not isinstance(missing_raw, list):
            return False, 0
        requested = {
            str(value).strip()[:160]
            for value in requested_raw
            if isinstance(value, str) and str(value).strip()
        }
        missing = {
            str(value).strip()[:160]
            for value in missing_raw
            if isinstance(value, str) and str(value).strip()
        }
        try:
            checked_count = int(payload.get("checked_count"))
            present_count = int(payload.get("present_count"))
            missing_count = int(payload.get("missing_count"))
        except (TypeError, ValueError, OverflowError):
            return False, 0
        if (
            not requested
            or checked_count != len(requested)
            or missing_count != len(missing)
            or present_count + missing_count != checked_count
            or not missing.issubset(requested)
        ):
            return False, 0
        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            return False, 0
        existing = state.get("focus_segment_upload_fingerprints")
        if not isinstance(existing, dict):
            existing = {}
        updated = dict(existing)
        requeued = 0
        for segment_id in missing:
            if segment_id in updated:
                updated.pop(segment_id, None)
                requeued += 1
        previous = copy.deepcopy(self._state)
        state["focus_segment_upload_fingerprints"] = updated
        state["focus_segment_integrity_audit_version"] = (
            FOCUS_SEGMENT_INTEGRITY_AUDIT_VERSION
        )
        state["focus_segment_integrity_last_success_at"] = (
            self.current_time().isoformat()
        )
        state["focus_segment_integrity_last_checked_count"] = checked_count
        state["focus_segment_integrity_last_present_count"] = present_count
        state["focus_segment_integrity_last_missing_count"] = missing_count
        try:
            self._save()
        except Exception:
            self._state = previous
            raise
        return True, requeued

    def focus_segments_sync_cursor(self) -> str | None:
        """Return the last server delta cursor for this account, if valid."""

        state = self._state.get("account_state")
        if not isinstance(state, dict):
            return None
        value = str(state.get("focus_segments_sync_cursor") or "").strip()
        # Composite cursors contain an updated_at value and a stable
        # segment_id.  Keep enough room for the full JSON cursor instead of
        # truncating it and silently losing the tie-breaker.
        return value[:320] or None

    def set_focus_segments_sync_cursor(self, cursor: Any) -> bool:
        """Persist a server delta cursor without touching focus facts."""

        value = str(cursor or "").strip()[:320]
        if not value:
            return False
        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        if state.get("focus_segments_sync_cursor") == value:
            return False
        previous = copy.deepcopy(self._state)
        state["focus_segments_sync_cursor"] = value
        try:
            self._save()
        except Exception:
            # A cursor is an acknowledgement, not a best-effort cache.  If
            # the local ledger cannot be persisted, keep the old cursor so a
            # later sync retries the server response instead of losing facts.
            self._state = previous
            raise
        return True

    def merge_remote_segments_checked(self, payload: Any) -> tuple[bool, bool, int]:
        """Validate and transactionally merge a delta response.

        Returns ``(success, changed, merge_count)``.  ``success`` is distinct
        from ``changed``: a valid duplicate/empty response is an acknowledged
        response and may advance the cursor, while malformed data or a local
        persistence failure must leave both facts and cursor retryable.
        """

        entries = payload.get("segments") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return False, False, 0
        today = _as_beijing(self._now()).date()
        cutoff = today - timedelta(days=400)
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                return False, False, 0
            segment = segment_from_record(item, index)
            if segment is None or segment.end_at is None:
                return False, False, 0
            if segment.start_at.date() < cutoff or segment.start_at.date() > today:
                return False, False, 0
            if int((segment.end_at - segment.start_at).total_seconds()) <= 0:
                return False, False, 0

        previous = copy.deepcopy(self._state)
        try:
            changed, merged_count = self.merge_remote_segments_with_count(payload)
        except Exception:
            self._state = previous
            return False, False, 0
        return True, changed, merged_count

    def merge_remote_segments_with_count(self, payload: Any) -> tuple[bool, int]:
        """Merge server interval facts and report how many rows changed.

        The count is transport diagnostics only.  It does not alter the raw
        FocusSession semantics or treat a server aggregate as a local fact.
        """

        entries = payload.get("segments") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return False, 0
        today = _as_beijing(self._now()).date()
        cutoff = today - timedelta(days=400)
        records = self._state.setdefault("records", [])
        by_id = {
            str(raw.get("record_id") or raw.get("segment_id")): index
            for index, raw in enumerate(records)
            if isinstance(raw, dict) and (raw.get("record_id") or raw.get("segment_id"))
        }
        changed = False
        merged_count = 0
        affected_dates: set[date] = set()
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            segment = segment_from_record(item, index)
            if segment is None or segment.end_at is None:
                continue
            if segment.start_at.date() < cutoff or segment.start_at.date() > today:
                continue
            duration = max(0, int((segment.end_at - segment.start_at).total_seconds()))
            if duration <= 0:
                continue
            record_id = segment.segment_id or f"remote:{index}"
            record = {
                "date": segment.start_at.date().isoformat(),
                "started_at": segment.start_at.isoformat(),
                "seconds": duration,
                "completed": segment.completed,
                "quality": segment.quality,
                "task": segment.task,
                "interruptions": segment.interruptions,
                "record_id": record_id[:160],
                "session_id": segment.session_id,
                "device_id": segment.device_id,
            }
            existing_index = by_id.get(record_id)
            row_changed = False
            if existing_index is None:
                records.append(record)
                by_id[record_id] = len(records) - 1
                changed = True
                row_changed = True
                merged_count += 1
            elif records[existing_index] != record:
                previous_segment = segment_from_record(records[existing_index], existing_index)
                if previous_segment is not None:
                    affected_dates.update(self._segment_dates(previous_segment))
                records[existing_index] = record
                changed = True
                row_changed = True
                merged_count += 1
            if row_changed:
                affected_dates.update(self._segment_dates(segment))
        if len(records) > 500:
            for removed in records[:-500]:
                removed_segment = segment_from_record(removed, 0)
                if removed_segment is not None:
                    affected_dates.update(self._segment_dates(removed_segment))
            del records[:-500]
            changed = True
        if changed:
            # Only dates touched by the inserted/replaced fact are rebuilt.
            # The durable account cache is still one ledger; this avoids
            # re-unioning months of history for a one-row delta.
            self._rebuild_daily_focus_projection(affected_dates)
            self._trim_days()
            self._save()
        # Do not run a full-ledger reconciliation here.  The affected-date
        # projection above is the authoritative local cache update.
        derived_changed = bool(changed and affected_dates)
        return changed or derived_changed, merged_count

    def merge_remote_segments(self, payload: Any) -> bool:
        """Merge server-returned interval facts without importing aggregates."""

        changed, _merged_count = self.merge_remote_segments_with_count(payload)
        return changed

    def reconcile_derived_totals(self, at: datetime | None = None) -> bool:
        """Rebuild local day/week caches from interval facts.

        ``days`` and ``account_state`` are compatibility projections.  They
        are rebuilt only when raw interval facts exist and are never used as
        a source of focus time.
        """

        segments = self.focus_segments()
        if not segments:
            state = self._state.setdefault("account_state", {})
            if not isinstance(state, dict):
                state = {}
                self._state["account_state"] = state
            changed = False
            for key in (
                "focus_date",
                "focus_today_seconds",
                "focus_week_start",
                "focus_week_seconds",
            ):
                if key in state:
                    state.pop(key, None)
                    changed = True
            if changed:
                self._save()
            return changed
        moment = _as_beijing(at or self._now())
        days = self._state.setdefault("days", {})
        changed = False
        for key, raw in list(days.items()):
            try:
                focus_date = date.fromisoformat(str(key)[:10])
            except (TypeError, ValueError, OverflowError):
                continue
            day_start = datetime.combine(focus_date, time.min, tzinfo=BEIJING_TIMEZONE)
            day_end = day_start + timedelta(days=1)
            aggregate = aggregate_focus_time(segments, day_start, day_end, now=moment)
            if not isinstance(raw, dict):
                raw = {}
                days[key] = raw
            seconds = max(0, int(aggregate.total_seconds))
            if int(raw.get("seconds", 0) or 0) != seconds:
                raw["seconds"] = seconds
                changed = True
            if raw.get("seconds_untrusted"):
                raw["seconds_untrusted"] = False
                changed = True

        today = moment.date()
        week_start = today - timedelta(days=today.weekday())
        today_total = self._raw_day_seconds(today, moment)
        week_total = self.focus_aggregate("week", moment).total_seconds
        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        updates = {
            "focus_date": today.isoformat(),
            "focus_today_seconds": today_total,
            "focus_week_start": week_start.isoformat(),
            "focus_week_seconds": max(0, int(week_total)),
        }
        for key, value in updates.items():
            if state.get(key) != value:
                state[key] = value
                changed = True
        if changed:
            self._save()
        return changed

    def commit_focus_segment(
        self,
        segment: FocusSegment,
        *,
        source: str = "canonical_focus_store",
        reason: str = "sealed",
        application_switches: int = 0,
        away_count: int = 0,
    ) -> bool:
        """Durably commit one sealed fact through the canonical pipeline.

        Normal pauses, periodic checkpoints, restart recovery and historical
        recovery all call this boundary.  It performs WAL -> local pending
        queue -> upload ACK state; it never writes Supabase directly.
        """

        value = segment.normalized()
        if not value.segment_id or not value.session_id or value.end_at is None:
            raise ValueError("sealed FocusSegment identity is incomplete")
        duration = int((value.end_at - value.start_at).total_seconds())
        if duration <= 0 or duration > 24 * 60 * 60:
            raise ValueError("sealed FocusSegment duration is invalid")
        for index, raw in enumerate(self._state.get("records", [])):
            if not isinstance(raw, dict):
                continue
            existing = segment_from_record(raw, index)
            if existing is None or existing.segment_id != value.segment_id:
                continue
            if segment_payload(existing) != segment_payload(value):
                raise ValueError("FocusSegment identity has a conflicting payload")
            return False

        record = segment_as_store_record(value)
        record["application_switches"] = max(0, int(application_switches))
        record["away_count"] = max(0, int(away_count))
        if self._persist:
            self._recovery_journal.append_segment(value, reason=reason)
            account_state = self._state.setdefault("account_state", {})
            if not isinstance(account_state, dict):
                account_state = {}
                self._state["account_state"] = account_state
            account_state["focus_handoff_segment_id"] = value.segment_id

        previous_state = copy.deepcopy(self._state)
        try:
            day_key = value.start_at.date().isoformat()
            days = self._state.setdefault("days", {})
            day = days.setdefault(day_key, self._empty_day())
            day["seconds"] = max(0, int(day.get("seconds", 0))) + duration
            day["rounds"] = max(0, int(day.get("rounds", 0))) + (1 if value.completed else 0)
            day["longest"] = max(max(0, int(day.get("longest", 0))), duration)
            day["quality"] = [*list(day.get("quality", []))[-49:], value.quality]
            day["switches"] = max(0, int(day.get("switches", 0))) + max(0, int(application_switches))
            day["away"] = max(0, int(day.get("away", 0))) + max(0, int(away_count))
            day["interruptions"] = max(0, int(day.get("interruptions", 0))) + value.interruptions
            day["longest_continuous"] = max(
                max(0, int(day.get("longest_continuous", 0))), duration
            )
            records = self._state.setdefault("records", [])
            records.append(record)
            self._focus_segment_source_by_id[value.segment_id] = str(source or "canonical_focus_store")[:80]
            removed_records = records[:-500] if len(records) > 500 else []
            self._state["records"] = records[-500:]
            affected_record_dates = self._segment_dates(value)
            for removed in removed_records:
                removed_segment = segment_from_record(removed, 0)
                if removed_segment is not None:
                    affected_record_dates.update(self._segment_dates(removed_segment))
            self._rebuild_daily_focus_projection(affected_record_dates)
            self._trim_days()
            self._save()
        except Exception:
            self._state = previous_state
            raise
        return True

    def record_session(
        self,
        seconds: int,
        *,
        started_at: datetime | None = None,
        completed: bool = False,
        application_switches: int = 0,
        away_count: int = 0,
        task: str = "",
        interruptions: int = 0,
        record_id: str | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
    ) -> FocusQuality:
        duration = max(0, int(seconds))
        started = _as_beijing(started_at or self._now())
        quality = score_focus_quality(duration, application_switches, away_count)
        clean_record_id = str(record_id or "").strip()[:160]
        clean_device_id = str(
            self._device_id if device_id is None else device_id
        ).strip()[:120]
        clean_session_id = str(session_id or "").strip()[:160]
        end_at = started + timedelta(seconds=duration)
        if duration > 0:
            effective_session_id = clean_session_id or (
                f"interval:{started.isoformat(timespec='microseconds')}"
            )
            effective_record_id = clean_record_id or deterministic_focus_segment_id(
                clean_device_id or "local",
                effective_session_id,
                started,
                end_at,
            )
            self.commit_focus_segment(
                FocusSegment(
                    segment_id=effective_record_id,
                    session_id=effective_session_id,
                    device_id=clean_device_id,
                    start_at=started,
                    end_at=end_at,
                    completed=bool(completed),
                    quality=quality.score,
                    task=str(task)[:120],
                    interruptions=max(0, int(interruptions)),
                ),
                source="canonical_focus_store",
                reason="completed" if completed else "sealed",
                application_switches=application_switches,
                away_count=away_count,
            )
        return quality

    def pending_focus_handoff_segment_id(self) -> str:
        """Return the bounded sealed fact awaiting its upload ACK."""

        state = self._state.get("account_state")
        if not isinstance(state, dict):
            return ""
        value = str(state.get("focus_handoff_segment_id") or "").strip()[:160]
        if not value:
            return ""
        segment = next(
            (item for item in self.focus_segments() if item.segment_id == value),
            None,
        )
        if segment is None or segment.end_at is None:
            return ""
        acknowledgements = state.get("focus_segment_upload_fingerprints")
        if isinstance(acknowledgements, dict):
            if str(acknowledgements.get(value) or "") == self._focus_segment_upload_fingerprint(segment.to_dict()):
                return ""
        return value

    def has_pending_focus_handoff(self) -> bool:
        return bool(self.pending_focus_handoff_segment_id())

    @staticmethod
    def _empty_day() -> dict[str, Any]:
        return {
            "seconds": 0, "rounds": 0, "longest": 0, "quality": [],
            "switches": 0, "away": 0, "interruptions": 0,
            "longest_continuous": 0,
        }

    def begin_focus_session(self, at: datetime | None = None) -> None:
        """Start/resume the live continuity tracker, without a second timer."""

        now = _as_beijing(at or self._now())
        live = self._live
        if not live.get("session_active"):
            live.update({
                "session_active": True,
                "current_interruptions": 0,
                "current_continuous_seconds": 0,
            })
        paused_at = live.get("paused_at")
        if paused_at:
            try:
                paused = parse_focus_timestamp(paused_at)
                if paused is None:
                    raise ValueError("invalid paused_at")
                if (now - paused).total_seconds() > INTERRUPTION_GRACE_SECONDS:
                    live["current_interruptions"] = max(0, int(live.get("current_interruptions", 0))) + 1
                    day = self._state.setdefault("days", {}).setdefault(now.date().isoformat(), self._empty_day())
                    day["interruptions"] = max(0, int(day.get("interruptions", 0))) + 1
            except (TypeError, ValueError, OverflowError):
                pass
        live["running"] = True
        live["paused_at"] = None
        live["continuous_started_at"] = now.isoformat()
        self._save()

    def pause_focus_session(self, at: datetime | None = None) -> None:
        """Mark a pause; only a pause longer than ten minutes is an interruption."""

        if not self._live.get("session_active"):
            return
        now = _as_beijing(at or self._now())
        self._update_live_continuous(now)
        self._live["running"] = False
        self._live["paused_at"] = now.isoformat()
        self._save()

    def finish_focus_session(self, *, completed: bool = True, at: datetime | None = None) -> None:
        """Close the live tracker after the timer has recorded its final segment."""

        if self._live.get("session_active"):
            self._update_live_continuous(_as_beijing(at or self._now()))
        self._live.update({
            "session_active": False,
            "running": False,
            "paused_at": None,
            "continuous_started_at": None,
            "current_continuous_seconds": 0,
            "current_interruptions": 0,
        })
        self._save()

    def _update_live_continuous(self, now: datetime) -> None:
        started = self._live.get("continuous_started_at")
        if not started:
            return
        try:
            started_at = parse_focus_timestamp(started)
            if started_at is None:
                raise ValueError("invalid continuous_started_at")
            value = max(0, int((now - started_at).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return
        self._live["current_continuous_seconds"] = max(
            max(0, int(self._live.get("current_continuous_seconds", 0))), value
        )
        day = self._state.setdefault("days", {}).setdefault(now.date().isoformat(), self._empty_day())
        day["longest_continuous"] = max(max(0, int(day.get("longest_continuous", 0))), value)

    def set_current_task(self, title: str, *, due_at: str | None = None, target_seconds: int = 0) -> None:
        clean = str(title).strip()[:120]
        self._state["current_task"] = {
            "title": clean,
            "due_at": str(due_at or ""),
            "target_seconds": max(0, int(target_seconds)),
            "progress_seconds": 0,
        } if clean else None
        self._save()

    def update_current_task_progress(self, seconds: int) -> None:
        task = self._state.get("current_task")
        if isinstance(task, dict):
            task["progress_seconds"] = max(0, int(task.get("progress_seconds", 0))) + max(0, int(seconds))
            self._save()

    def current_task(self) -> dict[str, Any] | None:
        task = self._state.get("current_task")
        return dict(task) if isinstance(task, dict) else None

    def set_tomorrow_task(self, title: str) -> None:
        tomorrow = (_as_beijing(self._now()).date() + timedelta(days=1)).isoformat()
        clean = str(title).strip()[:160]
        if clean:
            self._state.setdefault("reviews", {})[tomorrow] = clean
        else:
            self._state.setdefault("reviews", {}).pop(tomorrow, None)
        self._save()

    def tomorrow_task(self) -> str:
        tomorrow = (_as_beijing(self._now()).date() + timedelta(days=1)).isoformat()
        return str(self._state.setdefault("reviews", {}).get(tomorrow, ""))

    def today_first_task(self) -> str:
        today = _as_beijing(self._now()).date().isoformat()
        return str(self._state.setdefault("reviews", {}).get(today, ""))

    def summary(self, at: datetime | None = None) -> FocusAnalyticsSummary:
        moment = _as_beijing(at or self._now())
        today = moment.date()
        days = self._state.get("days", {})
        raw_segments = self.focus_segments()
        has_raw_facts = bool(raw_segments)

        def day_value(day: date, key: str) -> int:
            raw = days.get(day.isoformat(), {})
            try:
                return max(0, int(raw.get(key, 0)))
            except (AttributeError, TypeError, ValueError):
                return 0

        def day_seconds(day: date) -> int | None:
            raw = days.get(day.isoformat(), {})
            legacy_seconds = self._legacy_day_seconds(day)
            if isinstance(raw, dict) and bool(raw.get("seconds_untrusted")):
                # A quarantined local checkpoint cannot contribute, but a
                # server-confirmed legacy daily evidence row still can.
                return legacy_seconds or None
            canonical_seconds = self._raw_day_seconds(day, moment) if has_raw_facts else 0
            return max(canonical_seconds, legacy_seconds)

        weekly_total = sum(
            seconds or 0
            for i in range(7)
            for seconds in (day_seconds(today - timedelta(days=i)),)
        )
        yesterday = day_seconds(today - timedelta(days=1))
        today_seconds = day_seconds(today)
        # Re-read the same interval projection used by work reports.  This
        # prevents the compact timer, study room and report summary from
        # disagreeing after a checkpoint or a cross-midnight segment.
        day_projection = self.period_summary("day", moment)
        week_projection = self.period_summary("week", moment)
        if has_raw_facts or bool(day_projection.get("local_evidence")):
            today_seconds = max(0, int(day_projection.get("total_seconds", 0) or 0))
        if has_raw_facts or bool(week_projection.get("local_evidence")):
            weekly_total = max(0, int(week_projection.get("total_seconds", 0) or 0))
        # This separate display floor aligns local UI surfaces with a server
        # projection without creating uploadable evidence or adding another
        # copy of the same interval.
        remote_projection = self.remote_effective_projection(moment)
        if remote_projection is not None:
            today_seconds = max(today_seconds, remote_projection["today_seconds"])
            weekly_total = max(weekly_total, remote_projection["week_seconds"])
        streak_reference = today if (today_seconds or 0) > 0 else today - timedelta(days=1)
        streak = 0
        while (day_seconds(streak_reference - timedelta(days=streak)) or 0) > 0:
            streak += 1
        longest = 0
        run = 0
        for offset in range(366, -1, -1):
            if (day_seconds(today - timedelta(days=offset)) or 0) > 0:
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        quality_values: list[int] = []
        for raw in self._state.get("records", []):
            if raw.get("date") == today.isoformat():
                try:
                    quality_values.append(int(raw.get("quality", 0)))
                except (TypeError, ValueError):
                    pass
        average_quality = round(sum(quality_values) / len(quality_values)) if quality_values else 0
        quality_label = score_focus_quality(45 * 60, max(0, 24 - average_quality), 0).label if average_quality else "尚无本日质量数据"
        window = self._best_window(today)
        late_records = []
        for raw in self._state.get("records", []):
            try:
                started = parse_focus_timestamp(raw.get("started_at"))
                if started is None:
                    raise ValueError("invalid started_at")
            except ValueError:
                continue
            if today - timedelta(days=6) <= started.date() <= today and started.hour >= 23:
                late_records.append(max(0, int(raw.get("seconds", 0))))
        late_average = round(sum(late_records) / len(late_records)) if late_records else 0
        return FocusAnalyticsSummary(
            today.isoformat(), day_value(today, "rounds"), streak, longest, weekly_total,
            yesterday, (today_seconds - yesterday) if today_seconds is not None and yesterday is not None else None,
            average_quality,
            quality_label, window, late_average,
            max(
                day_value(today, "interruptions"),
                int(day_projection.get("interruptions", 0) or 0),
            ),
            max(0, int(self._live.get("current_interruptions", 0))),
            max(
                day_value(today, "longest_continuous"),
                int(day_projection.get("longest_focus_seconds", 0) or 0),
            ),
            max(0, int(self._live.get("current_continuous_seconds", 0))),
            today_seconds,
        )

    def snapshot(self) -> dict[str, Any]:
        if self._live.get("running"):
            self._update_live_continuous(_as_beijing(self._now()))
        summary = self.summary()
        return {
            **summary.__dict__,
            "current_task": self.current_task(),
            "tomorrow_task": self.tomorrow_task(),
            "first_task_today": self.today_first_task(),
        }

    def daily_history(self, days: int = 8) -> list[dict[str, Any]]:
        """Return recent trustworthy daily totals for server reconciliation."""

        count = max(1, min(31, int(days)))
        today = _as_beijing(self._now()).date()
        result: list[dict[str, Any]] = []
        stored = self._state.get("days", {})
        has_raw_facts = bool(self.focus_segments())
        for offset in range(count - 1, -1, -1):
            focus_date = today - timedelta(days=offset)
            key = focus_date.isoformat()
            legacy_seconds = self._legacy_day_seconds(focus_date)
            if key not in stored and legacy_seconds <= 0:
                # A new device must not send synthetic zeros for days it has
                # never observed; that would erase valid remote history.
                continue
            raw = stored.get(key, {})
            if not isinstance(raw, dict):
                raw = {}
            if bool(raw.get("seconds_untrusted")) and legacy_seconds <= 0:
                continue
            try:
                canonical_seconds = self._raw_day_seconds(focus_date) if has_raw_facts else 0
                seconds = max(canonical_seconds, legacy_seconds)
            except (TypeError, ValueError, OverflowError):
                continue
            # Include trustworthy zero days as well.  The exact reconciliation
            # RPC needs those rows to clear a previously inflated daily value;
            # omitting them would leave the old server maximum permanently.
            result.append({
                "focus_date": focus_date.isoformat(),
                "seconds": seconds,
                "legacy_seconds": legacy_seconds,
            })
        return result

    def focus_segments(self) -> list[FocusSegment]:
        """Return the durable interval facts represented by local records.

        ``days`` and the scalar account snapshot are deliberately excluded:
        they are caches/fallbacks from older releases, not work facts.
        """

        result: list[FocusSegment] = []
        for index, raw in enumerate(self._state.get("records", [])):
            segment = segment_from_record(raw, index)
            if segment is not None:
                result.append(segment)
        return result

    def set_live_projection_segments(self, segments: Any, *, renew_ttl: bool = True) -> bool:
        """Replace the transient per-device live projection.

        The projection is deliberately separate from ``focus_segments``:
        open presence rows never become FocusSession facts and therefore can
        never be uploaded by ``focus_segments_payload`` or affect the cursor.
        """

        # Expire the previous snapshot before comparing a new server result.
        # A fresh response with the same interval must renew its local TTL.
        previous_live = self.live_projection_segments()
        candidate: list[FocusSegment] = []
        if isinstance(segments, (list, tuple)):
            for item in segments:
                if not isinstance(item, FocusSegment):
                    continue
                normalized = item.normalized()
                if not normalized.segment_id or not normalized.session_id:
                    continue
                candidate.append(normalized)
        changed = candidate != previous_live
        if changed:
            self._live_projection_segments = candidate
            # The server RPC only returns fresh rows. If the network goes
            # offline, a last-known live row must stop contributing locally
            # instead of growing forever.
            self._live_projection_expires_at = monotonic() + 120.0 if candidate else None
        elif renew_ttl and candidate:
            self._live_projection_expires_at = monotonic() + 120.0
        return changed

    def live_projection_segments(self) -> list[FocusSegment]:
        """Return a copy of the current in-memory device projection."""

        if (
            self._live_projection_expires_at is not None
            and monotonic() >= self._live_projection_expires_at
        ):
            self._live_projection_segments = []
            self._live_projection_expires_at = None
        return list(self._live_projection_segments)

    def _projection_segments(
        self,
        extra_segments: list[FocusSegment] | None = None,
    ) -> list[FocusSegment]:
        """Return sealed account facts plus transient live intervals."""

        segments = self.focus_segments()
        segments.extend(self.live_projection_segments())
        if extra_segments:
            segments.extend(extra_segments)
        return segments

    def _raw_day_seconds(self, focus_date: date, moment: datetime | None = None) -> int:
        """Return the exact union of valid facts intersecting one Beijing day."""

        start = datetime.combine(focus_date, time.min, tzinfo=BEIJING_TIMEZONE)
        end = start + timedelta(days=1)
        untrusted_dates = {
            str(key)
            for key, value in (self._state.get("days", {}) or {}).items()
            if isinstance(value, dict) and bool(value.get("seconds_untrusted"))
        }
        segments = [
            segment
            for segment in self._projection_segments()
            if segment.start_at.date().isoformat() not in untrusted_dates
        ]
        return max(
            0,
            int(
                aggregate_focus_time(
                    segments,
                    start,
                    end,
                    # ``moment`` selects a historical report window.  Validity
                    # of open/future facts must use the actual clock, so a
                    # segment crossing midnight is not rejected when opening
                    # yesterday's report.
                    now=_as_beijing(self._now()),
                    interruption_grace_seconds=INTERRUPTION_GRACE_SECONDS,
                ).total_seconds
            ),
        )

    def _legacy_day_seconds(self, focus_date: date) -> int:
        """Return explicit legacy aggregate evidence for one Beijing day."""

        ledger = self._state.get("legacy_daily", {})
        raw = ledger.get(focus_date.isoformat(), {}) if isinstance(ledger, dict) else {}
        if not isinstance(raw, dict):
            return 0
        try:
            return max(0, min(MAX_ANALYTICS_DAY_SECONDS, int(raw.get("seconds", 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            return 0

    def focus_aggregate(
        self,
        period: str = "day",
        at: datetime | None = None,
        *,
        extra_segments: list[FocusSegment] | None = None,
    ) -> FocusAggregate:
        """Aggregate raw facts with one interval implementation."""

        moment = _as_beijing(at or self._now())
        validation_moment = _as_beijing(self._now())
        range_start, range_end, _ = calendar_window(period, moment)
        segments = self._projection_segments(extra_segments)
        # Legacy cumulative checkpoint rows are retained for diagnostics but
        # are marked untrusted by ``_rebuild_days_from_records``.  Exclude all
        # records on those dates from user-visible aggregates.
        untrusted_dates = {
            str(key)
            for key, value in (self._state.get("days", {}) or {}).items()
            if isinstance(value, dict) and bool(value.get("seconds_untrusted"))
        }
        if untrusted_dates:
            segments = [item for item in segments if item.start_at.date().isoformat() not in untrusted_dates]
        return aggregate_focus_time(
            segments,
            range_start,
            range_end,
            now=validation_moment,
            interruption_grace_seconds=INTERRUPTION_GRACE_SECONDS,
        )

    def range_segments(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        extra_segments: list[FocusSegment] | None = None,
    ) -> list[FocusSegment]:
        """Return canonical facts intersecting ``[start_at, end_at)``.

        This is a diagnostic/read model helper only.  It deliberately returns
        raw segment identities (including the device id) so a report can prove
        which facts fed a historical calculation without serialising task
        text or using a derived daily counter.
        """

        window_start = _as_beijing(start_at)
        window_end = _as_beijing(end_at)
        if window_end <= window_start:
            return []
        validation_moment = _as_beijing(self._now())
        untrusted_dates = {
            str(key)
            for key, value in (self._state.get("days", {}) or {}).items()
            if isinstance(value, dict) and bool(value.get("seconds_untrusted"))
        }
        result: list[FocusSegment] = []
        for segment in self._projection_segments(extra_segments):
            if segment.start_at.date().isoformat() in untrusted_dates:
                continue
            try:
                if segment.validation_error(validation_moment):
                    continue
                if segment.start_at < window_end and segment.effective_end(validation_moment) > window_start:
                    result.append(segment)
            except (TypeError, ValueError, OverflowError):
                continue
        return result

    def range_aggregate(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        extra_segments: list[FocusSegment] | None = None,
    ) -> FocusAggregate:
        """Aggregate the canonical account ledger over one half-open range."""

        window_start = _as_beijing(start_at)
        window_end = _as_beijing(end_at)
        if window_end <= window_start:
            raise ValueError("end_at must be after start_at")
        validation_moment = _as_beijing(self._now())
        untrusted_dates = {
            str(key)
            for key, value in (self._state.get("days", {}) or {}).items()
            if isinstance(value, dict) and bool(value.get("seconds_untrusted"))
        }
        segments = [
            item
            for item in self._projection_segments(extra_segments)
            if item.start_at.date().isoformat() not in untrusted_dates
        ]
        return aggregate_focus_time(
            segments,
            window_start,
            window_end,
            now=validation_moment,
            interruption_grace_seconds=INTERRUPTION_GRACE_SECONDS,
        )

    def range_summary(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        at: datetime | None = None,
        period: str = "custom",
        extra_segments: list[FocusSegment] | None = None,
    ) -> dict[str, Any]:
        """Summarize any half-open Beijing interval from the canonical union.

        The standard day/week/month/year report predates movable windows and
        has a few compatibility fields for the old UI.  This method is the
        deliberately small common path for arbitrary report windows.  It
        never adds daily counters or timer checkpoints as evidence: every
        duration is produced by :func:`aggregate_focus_time`, so overlapping
        devices are counted once and a session crossing midnight is clipped
        correctly at both ends.
        """

        window_start = _as_beijing(start_at)
        window_end = _as_beijing(end_at)
        if window_end <= window_start:
            raise ValueError("end_at must be after start_at")
        validation_moment = _as_beijing(self._now())
        raw_segments = self._projection_segments(extra_segments)
        untrusted_dates = {
            str(key)
            for key, value in (self._state.get("days", {}) or {}).items()
            if isinstance(value, dict) and bool(value.get("seconds_untrusted"))
        }
        segments = [
            item
            for item in raw_segments
            if item.start_at.date().isoformat() not in untrusted_dates
        ]
        # The daily projection is an incremental cache, not a second source
        # of truth.  In particular, never let an old checkpoint overwrite a
        # selected historical day's clipped union.
        aggregate = self.range_aggregate(
            window_start,
            window_end,
            extra_segments=extra_segments,
        )
        daily_rounds: dict[str, int] = {}
        for segment in segments:
            try:
                if segment.validation_error(validation_moment):
                    continue
                clipped_start = max(segment.start_at, window_start)
                clipped_end = min(segment.effective_end(validation_moment), window_end)
                if clipped_end <= clipped_start:
                    continue
                day_cursor = clipped_start.date()
                last_segment_day = (clipped_end - timedelta(microseconds=1)).date()
                while day_cursor <= last_segment_day:
                    daily_rounds[day_cursor.isoformat()] = daily_rounds.get(day_cursor.isoformat(), 0) + 1
                    day_cursor += timedelta(days=1)
            except (TypeError, ValueError, OverflowError):
                continue

        # Build full daily rows from the same clipped union.  A future day is
        # intentionally represented as ``None`` just like the standard
        # reports; a current day contains the live value available right now.
        daily: list[dict[str, Any]] = []
        cursor = window_start.date()
        last_date = (window_end - timedelta(microseconds=1)).date()
        while cursor <= last_date:
            day_start = datetime.combine(cursor, time.min, tzinfo=BEIJING_TIMEZONE)
            day_end = day_start + timedelta(days=1)
            is_future = cursor > validation_moment.date()
            untrusted = cursor.isoformat() in untrusted_dates
            seconds: int | None
            if is_future or untrusted:
                seconds = None
            else:
                # Always use the clipped interval-union bucket.  The
                # incremental daily projection is maintained for lightweight
                # consumers, but it must never be allowed to resurrect a
                # stale/corrupt historical counter in a report.
                seconds = max(0, int(aggregate.daily.get(cursor.isoformat(), 0) or 0))
            weekday = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[cursor.weekday()]
            daily.append({
                "date": cursor.isoformat(),
                "label": f"{cursor.month}/{cursor.day}",
                "weekday": weekday,
                "display_label": f"{cursor.month}/{cursor.day} {weekday}",
                "seconds": seconds,
                "rounds": daily_rounds.get(cursor.isoformat(), 0) if seconds is not None else None,
                "trusted": not is_future and not untrusted,
                "is_today": cursor == validation_moment.date(),
                "is_future": is_future,
                "status": "future" if is_future else "untrusted" if untrusted else "observed",
            })
            cursor += timedelta(days=1)

        # Quality and completion are descriptive metadata only.  Totals and
        # all chart values remain the unioned interval metrics above.
        quality_values = [max(0, min(100, int(value))) for value in aggregate.quality_values]
        valid_source_count = 0
        completed_source_count = 0
        for segment in segments:
            try:
                if segment.validation_error(validation_moment):
                    continue
                effective_end = segment.effective_end(validation_moment)
                clipped_start = max(segment.start_at, window_start)
                clipped_end = min(effective_end, window_end)
                if clipped_end <= clipped_start:
                    continue
                valid_source_count += 1
                completed_source_count += 1 if segment.completed or segment.end_at is not None else 0
            except (TypeError, ValueError, OverflowError):
                continue

        observed_daily = [row for row in daily if row.get("seconds") is not None]
        total_seconds = max(0, int(aggregate.total_seconds))
        active_days = sum(1 for row in observed_daily if int(row.get("seconds", 0) or 0) > 0)
        monthly: list[dict[str, Any]] = []
        month_keys = sorted({str(row.get("date") or "")[:7] for row in daily if row.get("date")})
        for month_key in month_keys:
            month_rows = [row for row in daily if str(row.get("date") or "").startswith(month_key)]
            known = [row for row in month_rows if row.get("seconds") is not None]
            month_seconds = sum(max(0, int(row.get("seconds", 0) or 0)) for row in known)
            try:
                month_date = date.fromisoformat(f"{month_key}-01")
                month_label = f"{month_date.year}年{month_date.month}月"
            except ValueError:
                month_label = month_key
            monthly.append({
                "date": f"{month_key}-01",
                "label": month_label,
                "year_label": month_label,
                "seconds": month_seconds if known else None,
                "active_days": sum(1 for row in known if int(row.get("seconds", 0) or 0) > 0),
                "workday_average_seconds": month_seconds // max(1, sum(1 for row in known if int(row.get("seconds", 0) or 0) > 0)),
                "is_future": bool(month_rows) and not known,
            })

        interval_rows = [dict(row) for row in aggregate.intervals]
        first_started = interval_rows[0].get("started_at") if interval_rows else "暂无记录"
        last_ended = interval_rows[-1].get("ended_at") if interval_rows else "暂无记录"
        try:
            first_started_text = parse_focus_timestamp(first_started).strftime("%H:%M") if first_started else "暂无记录"
        except (AttributeError, TypeError, ValueError):
            first_started_text = "暂无记录"
        try:
            last_ended_text = parse_focus_timestamp(last_ended).strftime("%H:%M") if last_ended else "暂无记录"
        except (AttributeError, TypeError, ValueError):
            last_ended_text = "暂无记录"
        end_date = last_date
        return {
            "period": str(period or "custom"),
            "start": window_start.date().isoformat(),
            "end": end_date.isoformat(),
            "total_seconds": total_seconds,
            "completed_rounds": completed_source_count,
            "longest_focus_seconds": max(0, int(aggregate.longest_seconds)),
            "interruptions": max(0, int(aggregate.interruption_count)),
            "average_quality": round(sum(quality_values) / len(quality_values)) if quality_values else 0,
            "active_days": active_days,
            "started_rounds": max(0, int(aggregate.segment_count)),
            "completion_rate": round(completed_source_count / valid_source_count * 100, 1) if valid_source_count else 0.0,
            "average_session_seconds": max(0, int(aggregate.average_seconds)),
            "high_quality_seconds": total_seconds if aggregate.longest_seconds >= 25 * 60 else 0,
            "deep_focus_seconds": total_seconds if aggregate.longest_seconds >= 25 * 60 else 0,
            "first_started_at": first_started_text,
            "last_ended_at": last_ended_text,
            "strongest_window": self._best_window(end_date, start=window_start.date()),
            "hourly": [dict(row) for row in aggregate.hourly],
            "focus_intervals": interval_rows,
            "daily": daily,
            "monthly": monthly,
            "untrusted_days": sorted(untrusted_dates.intersection({str(row.get("date")) for row in daily})),
            "data_quality": {
                "trusted": not bool(untrusted_dates.intersection({str(row.get("date")) for row in daily}) or aggregate.errors),
                "untrusted_days": sorted(untrusted_dates.intersection({str(row.get("date")) for row in daily})),
                "consistency_errors": list(aggregate.errors),
                "message": (
                    "本区间包含旧版异常计时记录；异常日期已剔除。"
                    if untrusted_dates.intersection({str(row.get("date")) for row in daily})
                    else "本区间统计存在一致性异常，异常区间已剔除。"
                    if aggregate.errors
                    else "本区间数据口径正常。"
                ),
            },
            "local_record_count": sum(
                1 for raw in self._state.get("records", [])
                if isinstance(raw, dict)
                and self._record_date(raw) is not None
                and window_start.date() <= self._record_date(raw) <= end_date
            ),
            "local_evidence": bool(valid_source_count),
            "raw_segment_count": len(segments),
            "raw_period_evidence": bool(valid_source_count),
            "raw_source_active": bool(valid_source_count),
            "workday_average_seconds": total_seconds // max(1, active_days),
        }

    def focus_device_diagnostics(
        self,
        period: str = "day",
        at: datetime | None = None,
    ) -> dict[str, Any]:
        """Return raw-per-device versus account-union time for diagnostics.

        This is deliberately not a statistics source. User-visible totals keep
        using :meth:`focus_aggregate`; this view only explains how much overlap
        that canonical union removed.
        """

        moment = _as_beijing(at or self._now())
        validation_moment = _as_beijing(self._now())
        range_start, range_end, _ = calendar_window(period, moment)
        untrusted_dates = {
            str(key)
            for key, value in (self._state.get("days", {}) or {}).items()
            if isinstance(value, dict) and bool(value.get("seconds_untrusted"))
        }
        segments = [
            segment
            for segment in self.focus_segments()
            if segment.start_at.date().isoformat() not in untrusted_dates
        ]
        grouped: dict[str, list[FocusSegment]] = {}
        for segment in segments:
            key = str(segment.device_id or "").strip() or "unattributed"
            grouped.setdefault(key, []).append(segment)

        devices: list[dict[str, Any]] = []
        raw_sum = 0
        for device_id, device_segments in sorted(grouped.items()):
            aggregate = aggregate_focus_time(
                device_segments,
                range_start,
                range_end,
                now=validation_moment,
                interruption_grace_seconds=INTERRUPTION_GRACE_SECONDS,
            )
            seconds = max(0, int(aggregate.total_seconds))
            raw_sum += seconds
            devices.append({
                "device_id": device_id,
                "seconds": seconds,
                "segment_count": aggregate.source_segment_count,
            })

        effective = self.focus_aggregate(period, moment)
        effective_seconds = max(0, int(effective.total_seconds))
        return {
            "period": str(period or "day"),
            "devices": devices,
            "raw_sum_seconds": raw_sum,
            "effective_union_seconds": effective_seconds,
            "overlap_seconds": max(0, raw_sum - effective_seconds),
        }

    @staticmethod
    def _segment_dates(segment: FocusSegment | None) -> set[date]:
        """Return every Beijing date touched by one interval."""

        if segment is None:
            return set()
        start = _as_beijing(segment.start_at)
        end = _as_beijing(segment.end_at or start)
        if end <= start:
            return {start.date()}
        cursor = start.date()
        last = (end - timedelta(microseconds=1)).date()
        result: set[date] = set()
        while cursor <= last:
            result.add(cursor)
            cursor += timedelta(days=1)
        return result

    def _rebuild_daily_focus_projection(self, dates: set[date] | list[date] | tuple[date, ...]) -> bool:
        """Incrementally rebuild the account projection for affected days."""

        affected = {item for item in dates if isinstance(item, date)}
        if not affected:
            return False
        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        projection = state.setdefault("daily_focus_projection", {})
        if not isinstance(projection, dict):
            projection = {}
            state["daily_focus_projection"] = projection
        sealed = self.focus_segments()
        now = _as_beijing(self._now())
        changed = False
        days = self._state.setdefault("days", {})
        for focus_date in sorted(affected):
            day_start = datetime.combine(focus_date, time.min, tzinfo=BEIJING_TIMEZONE)
            day_end = day_start + timedelta(days=1)
            aggregate = aggregate_focus_time(
                sealed,
                day_start,
                day_end,
                now=now,
                interruption_grace_seconds=INTERRUPTION_GRACE_SECONDS,
            )
            seconds = max(0, int(aggregate.total_seconds))
            key = focus_date.isoformat()
            value = {"seconds": seconds}
            if projection.get(key) != value:
                projection[key] = value
                changed = True
            # Keep the old compatibility day cache aligned for widgets that
            # still read its descriptive fields; it is never used as the
            # source of duration facts.
            day = days.setdefault(key, self._empty_day())
            if not isinstance(day, dict):
                day = self._empty_day()
                days[key] = day
            completed_rounds = sum(
                1
                for segment in sealed
                if segment.completed
                and segment.start_at < day_end
                and segment.effective_end(now) > day_start
            )
            for field, value in (
                ("seconds", seconds),
                ("rounds", max(0, int(completed_rounds))),
                ("longest", max(0, int(aggregate.longest_seconds))),
                ("interruptions", max(0, int(aggregate.interruption_count))),
            ):
                if int(day.get(field, 0) or 0) != value:
                    day[field] = value
                    changed = True
            if day.get("seconds_untrusted"):
                day["seconds_untrusted"] = False
                changed = True
        return changed

    def _ensure_daily_focus_projection(self) -> bool:
        """Backfill the projection once for ledgers created by older builds."""

        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        projection = state.get("daily_focus_projection")
        if isinstance(projection, dict) and projection:
            return False
        dates: set[date] = set()
        for segment in self.focus_segments():
            dates.update(self._segment_dates(segment))
        return self._rebuild_daily_focus_projection(dates)

    def period_summary(self, period: str = "day", at: datetime | None = None) -> dict[str, Any]:
        """Calculate a day/week/month/year report from account-local history.

        This is deliberately a read-only, on-demand projection.  The current
        live timer is supplied by the caller because it is not yet a closed
        analytics record.  Every closed total is recomputed from the same
        clipped raw interval union plus explicit legacy daily evidence.  The
        latter affects calendar totals only; detailed intervals and session
        metrics remain canonical raw projections.
        """

        moment = _as_beijing(at or self._now())
        today = moment.date()
        normalized = str(period or "day").strip().casefold()
        if normalized in {"week", "weekly", "本周", "周"}:
            key = "week"
            start = today - timedelta(days=today.weekday())
        elif normalized in {"month", "monthly", "月度", "月"}:
            key = "month"
            start = today.replace(day=1)
        elif normalized in {"year", "annual", "年度", "年"}:
            key = "year"
            start = today.replace(month=1, day=1)
        else:
            key = "day"
            start = today

        if key == "day":
            period_end = today
        elif key == "week":
            period_end = start + timedelta(days=6)
        elif key == "month":
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            period_end = next_month - timedelta(days=1)
        else:
            period_end = start.replace(year=start.year + 1) - timedelta(days=1)
        range_start = datetime.combine(start, time.min, tzinfo=BEIJING_TIMEZONE)
        range_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=BEIJING_TIMEZONE)
        aggregate = self.focus_aggregate(key, moment)
        # ``source_segment_count`` also includes facts outside the requested
        # window.  A positive projection (or an explicit invalid-interval
        # error) is the precise signal that this period has interval evidence.
        # This matters for a segment crossing midnight: its start date can be
        # yesterday while its overlap belongs to today's report.
        raw_segments = self._projection_segments()
        raw_period_evidence = any(
            segment.start_at < range_end
            and segment.effective_end(moment) > range_start
            for segment in raw_segments
        )
        has_raw_facts = bool(raw_segments)
        interval_evidence = raw_period_evidence
        period_has_legacy = any(
            self._legacy_day_seconds(start + timedelta(days=offset)) > 0
            for offset in range((period_end - start).days + 1)
            if start + timedelta(days=offset) <= today
        )

        stored = self._state.get("days", {})
        if not isinstance(stored, dict):
            stored = {}
        daily: list[dict[str, Any]] = []
        total_seconds = 0
        completed_rounds = 0
        longest_focus_seconds = 0
        interruptions = 0
        untrusted_days: list[str] = []
        cursor = start
        while cursor <= period_end:
            date_key = cursor.isoformat()
            raw = stored.get(date_key, {})
            raw = raw if isinstance(raw, dict) else {}
            is_future = cursor > today
            untrusted = bool(raw.get("seconds_untrusted"))
            legacy_seconds = self._legacy_day_seconds(cursor)
            rounds = 0
            longest = 0
            day_interruptions = 0
            if is_future:
                seconds = None
                rounds = None
                longest = 0
                day_interruptions = 0
            elif untrusted:
                # A local raw checkpoint may be quarantined while the server
                # still has a valid legacy daily aggregate from another
                # device. Preserve that calendar evidence without reviving
                # the untrusted local interval history.
                seconds = legacy_seconds or None
                rounds = None if seconds is None else 0
                longest = 0
                day_interruptions = 0
                untrusted_days.append(date_key)
            else:
                canonical_seconds = (
                    max(0, int(aggregate.daily.get(date_key, 0) or 0))
                    if has_raw_facts
                    else 0
                )
                seconds = max(canonical_seconds, legacy_seconds)
            if not is_future and not untrusted and has_raw_facts:
                try:
                    rounds = max(0, int(raw.get("rounds", 0) or 0))
                    longest = max(0, int(raw.get("longest", 0) or 0))
                    day_interruptions = max(0, int(raw.get("interruptions", 0) or 0))
                except (TypeError, ValueError, OverflowError):
                    rounds = longest = day_interruptions = 0
            total_seconds += int(seconds or 0)
            completed_rounds += int(rounds or 0)
            longest_focus_seconds = max(longest_focus_seconds, longest)
            interruptions += day_interruptions
            weekday = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[cursor.weekday()]
            daily.append({
                "date": date_key,
                "label": f"{cursor.month}/{cursor.day}",
                "weekday": weekday,
                "display_label": f"{cursor.month}/{cursor.day} {weekday}",
                "seconds": seconds,
                "canonical_seconds": (
                    max(0, int(aggregate.daily.get(date_key, 0) or 0))
                    if not is_future and has_raw_facts
                    else 0
                ),
                "legacy_seconds": legacy_seconds if not is_future else 0,
                "time_source": (
                    "legacy_compatibility"
                    if not is_future and legacy_seconds > max(
                        0,
                        int(aggregate.daily.get(date_key, 0) or 0) if has_raw_facts else 0,
                    )
                    else "canonical_interval_union"
                    if not is_future and (has_raw_facts or legacy_seconds > 0)
                    else "none"
                ),
                "rounds": rounds,
                "trusted": (has_raw_facts or legacy_seconds > 0) and not untrusted and not is_future,
                "is_today": cursor == today,
                "is_future": is_future,
                "status": (
                    "future" if is_future
                    else "untrusted" if untrusted and not legacy_seconds
                    else "legacy_compatibility" if legacy_seconds > 0 and not has_raw_facts
                    else "observed"
                ),
            })
            cursor += timedelta(days=1)

        # Calendar totals use the maximum of canonical interval union and the
        # explicit legacy daily evidence for each day.  The interval list,
        # hourly buckets, and session metrics remain canonical-only.
        local_period_evidence = raw_period_evidence or period_has_legacy
        total_seconds = sum(int(item.get("seconds") or 0) for item in daily)

        trusted_days = {
            str(item.get("date") or "")
            for item in daily
            if bool(item.get("trusted"))
        }

        def trusted_record_date(value: datetime) -> bool:
            return value.date().isoformat() in trusted_days

        quality_values: list[int] = []
        for raw in self._state.get("records", []):
            if not isinstance(raw, dict):
                continue
            try:
                started = parse_focus_timestamp(raw.get("started_at"))
                if started is None:
                    raise ValueError("invalid started_at")
                value = int(raw.get("quality", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if start <= started.date() <= today and trusted_record_date(started) and value > 0:
                quality_values.append(max(0, min(100, value)))

        # Derive report-only metrics from the raw account-scoped records. A
        # paused/resumed work session writes several segments with the same
        # session prefix in record_id, so group those segments before showing
        # completion rate or average session length.
        session_records: dict[str, dict[str, Any]] = {}
        hourly_intervals: list[list[tuple[datetime, datetime]]] = [[] for _ in range(24)]
        focus_intervals: list[dict[str, Any]] = []
        for index, raw in enumerate(self._state.get("records", [])):
            if not isinstance(raw, dict):
                continue
            try:
                started = parse_focus_timestamp(raw.get("started_at"))
                if started is None:
                    raise ValueError("invalid started_at")
                seconds = max(0, int(raw.get("seconds", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not (start <= started.date() <= today) or not trusted_record_date(started):
                continue
            record_id = str(raw.get("record_id") or "").strip()
            session_key = record_id.split(":", 1)[0] if record_id else f"record:{index}"
            ended = started + timedelta(seconds=seconds)
            item = session_records.setdefault(
                session_key,
                {
                    "seconds": 0,
                    "completed": False,
                    "started": started,
                    "ended": ended,
                    "intervals": [],
                },
            )
            item["seconds"] += seconds
            item["completed"] = bool(item["completed"] or raw.get("completed"))
            item["started"] = min(item["started"], started)
            item["ended"] = max(item["ended"], ended)
            item["intervals"].append((started, ended))
            focus_intervals.append(
                {
                    "date": started.date().isoformat(),
                    "started_at": started.isoformat(),
                    "ended_at": ended.isoformat(),
                    "seconds": seconds,
                    "task": str(raw.get("task_title") or raw.get("task") or raw.get("title") or ""),
                }
            )

            clipped_start = max(started, range_start)
            clipped_end = min(ended, range_end)
            cursor = clipped_start
            while cursor < clipped_end:
                next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                part_end = min(clipped_end, next_hour)
                hourly_intervals[cursor.hour].append((cursor, part_end))
                cursor = part_end

        def merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
            merged: list[list[datetime]] = []
            for interval_start, interval_end in sorted(intervals):
                if interval_end <= interval_start:
                    continue
                if not merged or interval_start > merged[-1][1]:
                    merged.append([interval_start, interval_end])
                elif interval_end > merged[-1][1]:
                    merged[-1][1] = interval_end
            return [(item[0], item[1]) for item in merged]

        def union_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
            return sum(
                max(0, int((interval_end - interval_start).total_seconds()))
                for interval_start, interval_end in merge_intervals(intervals)
            )

        def continuous_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
            """Merge short pause gaps while keeping active time as the value."""

            merged = merge_intervals(intervals)
            if not merged:
                return 0
            best = current = max(0, int((merged[0][1] - merged[0][0]).total_seconds()))
            previous_end = merged[0][1]
            for started, ended in merged[1:]:
                duration = max(0, int((ended - started).total_seconds()))
                gap = max(0, int((started - previous_end).total_seconds()))
                if gap <= INTERRUPTION_GRACE_SECONDS:
                    current += duration
                else:
                    best = max(best, current)
                    current = duration
                previous_end = max(previous_end, ended)
            return max(best, current)

        started_rounds = len(session_records)
        completed_sessions = sum(1 for item in session_records.values() if item["completed"])
        session_durations = [
            continuous_seconds(item["intervals"])
            for item in session_records.values()
            if continuous_seconds(item["intervals"]) > 0
        ]
        longest_session_seconds = max(
            session_durations,
            default=0,
        )
        first_started = min((item["started"] for item in session_records.values()), default=None)
        last_ended = max((item["ended"] for item in session_records.values()), default=None)
        completion_rate = round(completed_sessions / started_rounds * 100, 1) if started_rounds else 0.0
        high_quality_seconds = sum(
            continuous_seconds(item["intervals"])
            for item in session_records.values()
            if continuous_seconds(item["intervals"]) >= 25 * 60
        )
        high_quality_seconds = min(high_quality_seconds, total_seconds)
        if first_started is not None:
            first_started_text = first_started.strftime("%H:%M")
            last_ended_text = last_ended.strftime("%H:%M") if last_ended is not None else "--:--"
        else:
            first_started_text = last_ended_text = "暂无记录"

        return {
            "period": key,
            "start": start.isoformat(),
            "end": today.isoformat(),
            "total_seconds": total_seconds,
            "completed_rounds": completed_rounds,
            "longest_focus_seconds": (
                max(aggregate.longest_seconds, longest_session_seconds)
                if interval_evidence
                else max(aggregate.longest_seconds, longest_focus_seconds, longest_session_seconds)
            ),
            "interruptions": (
                aggregate.interruption_count
                if interval_evidence
                else max(interruptions, aggregate.interruption_count)
            ),
            "average_quality": round(sum(quality_values) / len(quality_values)) if quality_values else 0,
            "active_days": sum(1 for item in daily if int(item.get("seconds") or 0) > 0),
            "started_rounds": started_rounds,
            "completion_rate": completion_rate,
            # Average and maximum now use the same session grain.  Previously
            # average used unioned segment time while maximum used continuous
            # session time, which could make the average larger than the max.
            "average_session_seconds": (
                round(sum(session_durations) / len(session_durations))
                if session_durations else aggregate.average_seconds
            ),
            "high_quality_seconds": high_quality_seconds,
            "deep_focus_seconds": high_quality_seconds,
            "first_started_at": first_started_text,
            "last_ended_at": last_ended_text,
            "strongest_window": self._best_window(today, start=start),
            "hourly": [dict(item) for item in aggregate.hourly]
            if has_raw_facts
            else [
                {"hour": hour, "label": f"{hour:02d}:00", "seconds": union_seconds(intervals)}
                for hour, intervals in enumerate(hourly_intervals)
            ],
            "focus_intervals": [dict(item) for item in aggregate.intervals]
            if has_raw_facts else focus_intervals,
            "daily": daily,
            "untrusted_days": untrusted_days,
            "data_quality": {
                "trusted": not bool(untrusted_days or aggregate.errors),
                "untrusted_days": list(untrusted_days),
                "legacy_compatibility_days": [
                    str(item.get("date"))
                    for item in daily
                    if item.get("time_source") == "legacy_compatibility"
                ],
                "consistency_errors": list(aggregate.errors),
                "message": (
                    "本周期包含旧版异常计时记录；异常日期已从报告指标中剔除，避免把重复检查点当成真实工作时间。"
                    if untrusted_days
                    else "本周期区间统计存在一致性异常，异常区间已剔除。"
                    if aggregate.errors
                    else "本周期数据口径正常。"
                ),
            },
            "local_record_count": sum(
                1 for raw in self._state.get("records", [])
                if isinstance(raw, dict)
                and self._record_date(raw) is not None
                and start <= self._record_date(raw) <= today
            ),
            "local_evidence": local_period_evidence,
            "raw_segment_count": len(raw_segments),
            "raw_period_evidence": raw_period_evidence,
            "raw_source_active": has_raw_facts,
            "legacy_compatibility_active": period_has_legacy,
        }

    def _best_window(self, today: date, *, start: date | None = None) -> str:
        window_start = start or (today - timedelta(days=6))
        buckets: dict[int, list[int]] = {}
        for raw in self._state.get("records", []):
            try:
                started = parse_focus_timestamp(raw.get("started_at"))
                if started is None:
                    raise ValueError("invalid started_at")
                seconds = max(0, int(raw.get("seconds", 0)))
            except (ValueError, TypeError):
                continue
            if window_start <= started.date() <= today:
                day = self._state.get("days", {}).get(started.date().isoformat(), {})
                if isinstance(day, dict) and bool(day.get("seconds_untrusted")):
                    continue
                buckets.setdefault(started.hour, []).append(seconds)
        if not buckets:
            return "暂无足够数据"
        hour = max(buckets, key=lambda key: (sum(buckets[key]) / len(buckets[key]), len(buckets[key])))
        return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00"

    def _rebuild_days_from_records(self) -> bool:
        """Recompute daily duration as a union of raw focus intervals.

        Raw records are intentionally retained.  Only the derived daily
        ``seconds`` field is repaired, so a future migration can still inspect
        the original checkpoints that caused an over-count.
        """

        intervals: dict[str, list[tuple[datetime, datetime]]] = {}
        raw_durations: dict[str, list[int]] = {}
        changed = False
        for raw in self._state.get("records", []):
            if not isinstance(raw, dict):
                continue
            try:
                started = parse_focus_timestamp(raw.get("started_at"))
                if started is None:
                    raise ValueError("invalid started_at")
                duration = max(0, min(int(raw.get("seconds", 0)), MAX_ANALYTICS_DAY_SECONDS))
            except (TypeError, ValueError, OverflowError):
                continue
            if duration <= 0:
                continue
            day_key = started.date().isoformat()
            if raw.get("date") != day_key:
                raw["date"] = day_key
                changed = True
            canonical_started = started.isoformat()
            if raw.get("started_at") != canonical_started:
                raw["started_at"] = canonical_started
                changed = True
            raw_durations.setdefault(day_key, []).append(duration)
            end = started + timedelta(seconds=duration)
            cursor = started
            while cursor.date() < end.date():
                boundary = datetime.combine(
                    cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo
                )
                intervals.setdefault(cursor.date().isoformat(), []).append((cursor, boundary))
                cursor = boundary
            intervals.setdefault(cursor.date().isoformat(), []).append((cursor, end))

        days = self._state.setdefault("days", {})
        for day_key, pieces in intervals.items():
            pieces.sort(key=lambda item: item[0])
            merged: list[list[datetime]] = []
            for start, end in pieces:
                if not merged or start > merged[-1][1]:
                    merged.append([start, end])
                elif end > merged[-1][1]:
                    merged[-1][1] = end
            seconds = min(
                MAX_ANALYTICS_DAY_SECONDS,
                sum(max(0, int((end - start).total_seconds())) for start, end in merged),
            )
            day = days.setdefault(day_key, self._empty_day())
            raw_total = sum(raw_durations.get(day_key, []))
            # Before the session cursor was persisted, a recovered app could
            # write the cumulative timer total repeatedly.  A large raw/union
            # ratio with several records is a strong signal of that specific
            # corruption.  Keep the raw data for diagnostics, but never use
            # the derived value for a day-vs-day comparison.
            seconds_untrusted = (
                len(raw_durations.get(day_key, [])) >= 3
                and seconds > 0
                and raw_total >= seconds * 1.5
            )
            if int(day.get("seconds", 0) or 0) != seconds:
                day["seconds"] = seconds
                changed = True
            if bool(day.get("seconds_untrusted")) != seconds_untrusted:
                day["seconds_untrusted"] = seconds_untrusted
                changed = True
        return changed

    @staticmethod
    def _record_date(raw: dict[str, Any]) -> date | None:
        try:
            value = raw.get("started_at") or raw.get("date") or ""
            if "T" in str(value):
                parsed = parse_focus_timestamp(value)
                return parsed.date() if parsed is not None else None
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError, OverflowError):
            return None

    def _has_local_records(self, start: date, end: date) -> bool:
        return any(
            isinstance(raw, dict)
            and (record_date := self._record_date(raw)) is not None
            and start <= record_date <= end
            for raw in self._state.get("records", [])
        )

    def _has_observed_days(self, start: date, end: date) -> bool:
        """Return whether the local cache has a trusted observed day row.

        ``days`` may be populated by the cross-device history sync before a
        raw FocusSession is written on this machine.  Those rows are valid
        report evidence, while future and legacy-untrusted rows are not.
        """

        days = self._state.get("days", {})
        if not isinstance(days, dict):
            return False
        cursor = start
        while cursor <= end:
            raw = days.get(cursor.isoformat())
            if isinstance(raw, dict) and not bool(raw.get("seconds_untrusted")):
                try:
                    seconds = int(raw.get("seconds", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    seconds = -1
                if 0 <= seconds <= MAX_ANALYTICS_DAY_SECONDS:
                    return True
            cursor += timedelta(days=1)
        return False

    def _has_local_evidence(self, start: date, end: date) -> bool:
        return self._has_local_records(start, end) or self._has_observed_days(start, end)

    def _recover_focus_journal(self) -> bool:
        """Replay only WAL rows missing from the local sealed-fact store.

        A WAL row is never used directly by reports.  It first becomes an
        ordinary local ``records`` row, after which all existing interval
        projection code remains the only statistics path.  Hash conflicts are
        retained as diagnostics and deliberately do not overwrite either side.
        """

        if not self._persist:
            return False
        recovery = self._recovery_journal.recover(self.focus_segments())
        self._durability_conflicts = recovery.conflicts
        if recovery.conflicts:
            state = self._state.setdefault("account_state", {})
            if not isinstance(state, dict):
                state = {}
                self._state["account_state"] = state
            state["focus_durability_conflicts"] = list(recovery.conflicts)[-50:]
        if not recovery.segments:
            return bool(recovery.conflicts)
        records = self._state.setdefault("records", [])
        existing_ids = {
            str(raw.get("record_id") or raw.get("segment_id") or "")
            for raw in records
            if isinstance(raw, dict)
        }
        changed = False
        for segment in recovery.segments:
            if segment.segment_id in existing_ids:
                continue
            records.append(segment_as_store_record(segment))
            existing_ids.add(segment.segment_id)
            self._focus_segment_source_by_id[segment.segment_id] = "wal_recovery"
            changed = True
        if changed:
            affected = set()
            for segment in recovery.segments:
                affected.update(self._segment_dates(segment))
            # A recovered WAL row is a sealed fact whose Store write completed
            # only during this startup. Keep the handoff gate armed until the
            # normal delta path explicitly ACKs the recovered id.
            state = self._state.setdefault("account_state", {})
            if not isinstance(state, dict):
                state = {}
                self._state["account_state"] = state
            state["focus_handoff_segment_id"] = recovery.segments[-1].segment_id
            self._rebuild_daily_focus_projection(affected)
            self._trim_days()
        return changed or bool(recovery.conflicts)

    def _trim_days(self) -> bool:
        days = self._state.setdefault("days", {})
        cutoff = _as_beijing(self._now()).date() - timedelta(days=400)
        trimmed = {key: value for key, value in days.items() if key >= cutoff.isoformat()}
        changed = len(trimmed) != len(days)
        self._state["days"] = trimmed
        return changed

    def _trim_legacy_daily(self) -> bool:
        ledger = self._state.setdefault("legacy_daily", {})
        if not isinstance(ledger, dict):
            self._state["legacy_daily"] = {}
            return True
        cutoff = _as_beijing(self._now()).date() - timedelta(days=400)
        trimmed = {
            key: value
            for key, value in ledger.items()
            if str(key)[:10] >= cutoff.isoformat()
        }
        changed = len(trimmed) != len(ledger)
        self._state["legacy_daily"] = trimmed
        return changed

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._state.update(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _save(self) -> None:
        if not self._persist:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


class AccountFocusProjection:
    """Read-only account projection over sealed facts and live device rows."""

    def __init__(self, store: "AccountFocusStore") -> None:
        self.store = store

    def set_live_segments(self, segments: Any) -> bool:
        return self.store.set_live_projection_segments(segments)

    def clear_live_segments(self) -> bool:
        return self.store.set_live_projection_segments([])

    def seconds_for_range(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        at: datetime | None = None,
    ) -> int:
        aggregate = aggregate_focus_time(
            self.store._projection_segments(),
            _as_beijing(start_at),
            _as_beijing(end_at),
            now=_as_beijing(at or self.store.current_time()),
            interruption_grace_seconds=INTERRUPTION_GRACE_SECONDS,
        )
        return max(0, int(aggregate.total_seconds))

    def today_seconds(self, at: datetime | None = None) -> int:
        moment = _as_beijing(at or self.store.current_time())
        local_seconds = int(self.store.period_summary("day", moment).get("total_seconds", 0) or 0)
        remote = self.store.remote_effective_projection(moment)
        return max(local_seconds, int(remote["today_seconds"])) if remote else local_seconds

    def week_seconds(self, at: datetime | None = None) -> int:
        moment = _as_beijing(at or self.store.current_time())
        local_seconds = int(self.store.period_summary("week", moment).get("total_seconds", 0) or 0)
        remote = self.store.remote_effective_projection(moment)
        return max(local_seconds, int(remote["week_seconds"])) if remote else local_seconds


class AccountFocusStore(FocusAnalyticsStore):
    """Canonical local account ledger used by every focus surface.

    This is intentionally a semantic name over the existing durable
    ``FocusAnalyticsStore`` implementation, not a second database.  The
    durable rows remain sealed FocusSegments; the projection object only adds
    transient per-device presence intervals for read-only calculations.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.account_projection = AccountFocusProjection(self)

    def account_today_seconds(self, at: datetime | None = None) -> int:
        return self.account_projection.today_seconds(at)

    def account_week_seconds(self, at: datetime | None = None) -> int:
        return self.account_projection.week_seconds(at)


__all__ = [
    "AccountFocusProjection",
    "AccountFocusStore",
    "BEIJING_TIMEZONE",
    "FocusAnalyticsStore",
    "FocusAnalyticsSummary",
    "FocusQuality",
    "FocusQualityTracker",
    "score_focus_quality",
]
