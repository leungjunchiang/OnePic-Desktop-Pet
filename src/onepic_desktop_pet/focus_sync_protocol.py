"""Pure validation helpers for the sealed FocusSegment delta protocol.

The focus ledger is a transactional protocol, not a best-effort dashboard
read.  Keep the wire-contract checks in a dependency-free module so the GUI
thread, relay tests, and reconciliation tests all use the same rules.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Any


MAX_CURSOR_LENGTH = 320


def _canonical_upload_json(value: dict[str, Any]) -> str:
    """Serialize one upload row for deterministic identity diagnostics."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _upload_payload_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_upload_json(value).encode("utf-8")
    ).hexdigest()


def canonicalize_focus_upload_segments(
    segments: Any,
    *,
    source_entries: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Canonicalize the final delta-v2 upload batch before any RPC.

    A segment id is the immutable identity of one sealed fact.  This helper
    deliberately runs at the last boundary before the network call, because
    legacy migration, WAL replay and pending-upload assembly can otherwise
    expose the same fact more than once.  Identical duplicate payloads are
    safe to collapse.  Conflicting payloads are never guessed or overwritten:
    the caller must block the upload and leave its ACK/fingerprint/cursor
    state untouched.

    ``source_entries`` is an optional list aligned with ``segments``.  It is
    diagnostic-only metadata and is never sent to the server.
    """

    rows = segments if isinstance(segments, list) else []
    sources = source_entries if isinstance(source_entries, list) else []
    grouped: dict[str, list[dict[str, Any]]] = {}
    ordered_ids: list[str] = []
    malformed: list[dict[str, Any]] = []
    if not isinstance(segments, list):
        malformed.append({"index": -1, "reason": "payload_not_list"})
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            malformed.append({"index": index, "reason": "item_not_object"})
            continue
        segment_id = str(item.get("segment_id") or "").strip()[:160]
        if not segment_id:
            malformed.append({"index": index, "reason": "segment_id_missing"})
            continue
        # Keep the wire row exactly as supplied, but compare/hash a copy with
        # the normalized identity value so whitespace cannot evade the gate.
        payload = dict(item)
        payload["segment_id"] = segment_id
        source_value = sources[index] if index < len(sources) else "pending_upload"
        if isinstance(source_value, dict):
            source = str(source_value.get("source") or "pending_upload")[:80]
        else:
            source = str(source_value or "pending_upload")[:80]
        grouped.setdefault(segment_id, []).append(
            {
                "source": source,
                "hash": _upload_payload_hash(payload),
                "payload": payload,
                "index": index,
            }
        )
        if segment_id not in ordered_ids:
            ordered_ids.append(segment_id)

    duplicate_groups: list[dict[str, Any]] = []
    conflicts: list[str] = []
    canonical_rows: list[dict[str, Any]] = []
    for segment_id in ordered_ids:
        group = grouped[segment_id]
        hashes = {str(entry["hash"]) for entry in group}
        if len(group) > 1:
            duplicate_groups.append(
                {
                    "segment_id": segment_id,
                    "same_payload": len(hashes) == 1,
                    "records": [
                        {
                            "source": entry["source"],
                            "hash": entry["hash"],
                            "payload": entry["payload"],
                        }
                        for entry in group
                    ],
                }
            )
        if len(hashes) > 1:
            conflicts.append(segment_id)
        # Even for a conflict, retain no candidate row: callers must not
        # accidentally upload the first record and overwrite the other one.
        if len(hashes) == 1:
            canonical_rows.append(dict(group[0]["payload"]))

    duplicate_ids = [
        group["segment_id"] for group in duplicate_groups
    ]
    source_diagnostics: list[dict[str, str]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "").strip()[:160]
        if not segment_id:
            continue
        source_value = sources[index] if index < len(sources) else "pending_upload"
        if isinstance(source_value, dict):
            source = str(source_value.get("source") or "pending_upload")[:80]
        else:
            source = str(source_value or "pending_upload")[:80]
        source_diagnostics.append(
            {
                "segment_id": segment_id,
                "source": source,
                "pipeline_stage": "pending_upload",
            }
        )
    diagnostics: dict[str, Any] = {
        "ok": not malformed and not conflicts,
        "input_count": len(rows),
        "unique_count": len(canonical_rows) if not conflicts else 0,
        "duplicate_count": sum(
            max(0, len(grouped[item]) - 1) for item in duplicate_ids
        ),
        "duplicate_segment_ids": duplicate_ids,
        "conflict_segment_ids": conflicts,
        "malformed": malformed,
        "groups": duplicate_groups,
        "sources": source_diagnostics,
    }
    if malformed:
        diagnostics["error"] = "upload_segment_payload_malformed"
    elif conflicts:
        diagnostics["error"] = "duplicate_focus_segment_id_conflict"
    elif duplicate_ids:
        diagnostics["error"] = "duplicate_focus_segment_id_deduplicated"
    else:
        diagnostics["error"] = ""
    # This is a final invariant, not just a metric.  Keep the assertion
    # explicit so a future caller cannot accidentally reintroduce duplicate
    # ids after this function returns.
    if diagnostics["ok"] and len(canonical_rows) != len(
        {str(item.get("segment_id") or "") for item in canonical_rows}
    ):
        diagnostics["ok"] = False
        diagnostics["error"] = "duplicate_focus_segment_id_internal"
        diagnostics["conflict_segment_ids"] = duplicate_ids or ["<unknown>"]
        return [], diagnostics
    return (canonical_rows if diagnostics["ok"] else []), diagnostics


def is_valid_delta_cursor(value: Any, *, allow_legacy_timestamp: bool = True) -> bool:
    """Return whether *value* is a safe server delta cursor.

    Current servers return a JSON string containing the ordered pair
    ``updated_at``/``segment_id``.  A timestamp-only cursor is accepted while
    older deployments are being rolled forward, but arbitrary strings are
    never accepted as a cursor from a network response.
    """

    text = str(value or "").strip()
    if not text or len(text) > MAX_CURSOR_LENGTH:
        return False
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        updated_at = str(decoded.get("updated_at") or "").strip()
        segment_id = str(decoded.get("segment_id") or "").strip()
        if not updated_at or len(segment_id) > 160:
            return False
        try:
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        return True
    if not allow_legacy_timestamp:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


def validate_focus_delta_response(
    response: Any,
    uploaded_segments: Any,
    *,
    cursor_before: Any = None,
) -> tuple[bool, str, set[str]]:
    """Validate one v2 delta response before facts or cursor are committed.

    The server must acknowledge the complete upload batch, including an empty
    batch, and return a structurally valid ordered-stream page.  A malformed
    response is never treated as an empty delta: callers must retain both the
    upload fingerprints and the previous cursor so the next poll retries it.
    """

    if not isinstance(response, dict):
        return False, "response_not_object", set()
    uploaded = uploaded_segments if isinstance(uploaded_segments, list) else []
    expected_ids: list[str] = []
    for item in uploaded:
        if not isinstance(item, dict):
            return False, "uploaded_item_not_object", set()
        segment_id = str(item.get("segment_id") or "").strip()
        if not segment_id:
            return False, "uploaded_segment_id_missing", set()
        expected_ids.append(segment_id)
    expected_set = set(expected_ids)
    if len(expected_set) != len(expected_ids):
        return False, "uploaded_segment_id_duplicate", set()

    segments = response.get("segments")
    if not isinstance(segments, list):
        return False, "segments_not_list", set()
    seen_returned: set[str] = set()
    for item in segments:
        if not isinstance(item, dict):
            return False, "returned_segment_not_object", set()
        segment_id = str(item.get("segment_id") or "").strip()
        if not segment_id or segment_id in seen_returned:
            return False, "returned_segment_id_invalid", set()
        seen_returned.add(segment_id)

    accepted_raw = response.get("accepted_segment_ids")
    if not isinstance(accepted_raw, list):
        return False, "accepted_segment_ids_missing", set()
    accepted_ids = {
        str(value or "").strip()
        for value in accepted_raw
        if str(value or "").strip()
    }
    if len(accepted_ids) != len(accepted_raw):
        return False, "accepted_segment_ids_duplicate_or_invalid", accepted_ids

    requested_count = response.get("requested_count")
    accepted_count = response.get("accepted_count")
    if not isinstance(requested_count, int) or isinstance(requested_count, bool):
        return False, "requested_count_missing", accepted_ids
    if not isinstance(accepted_count, int) or isinstance(accepted_count, bool):
        return False, "accepted_count_missing", accepted_ids
    if requested_count != len(uploaded):
        return False, "requested_count_mismatch", accepted_ids
    if accepted_count != len(accepted_ids):
        return False, "accepted_count_mismatch", accepted_ids
    if accepted_ids != expected_set:
        return False, "accepted_segment_ids_mismatch", accepted_ids

    next_cursor = response.get("next_cursor")
    if not is_valid_delta_cursor(next_cursor):
        return False, "next_cursor_invalid", accepted_ids
    if not isinstance(response.get("has_more"), bool):
        return False, "has_more_missing", accepted_ids
    if not isinstance(response.get("full_sync"), bool):
        return False, "full_sync_missing", accepted_ids

    # Empty deltas must not move the stream position.  The server migration
    # enforces this too; the client check prevents an older/broken relay from
    # silently skipping a row committed during the request.
    if not segments and cursor_before:
        before = str(cursor_before).strip()
        after = str(next_cursor).strip()
        if before != after:
            return False, "empty_delta_cursor_advanced", accepted_ids
    return True, "", accepted_ids
