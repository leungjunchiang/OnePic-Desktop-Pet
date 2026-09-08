"""Pure validation helpers for the sealed FocusSegment delta protocol.

The focus ledger is a transactional protocol, not a best-effort dashboard
read.  Keep the wire-contract checks in a dependency-free module so the GUI
thread, relay tests, and reconciliation tests all use the same rules.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


MAX_CURSOR_LENGTH = 320


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
