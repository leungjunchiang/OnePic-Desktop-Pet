"""Stable machine identity used to enforce one active desktop per account.

The identity is deliberately machine-scoped rather than account-scoped.  It is
stored in Lili's per-user application-data directory, contains no hostname or
hardware fingerprint, and is only sent to the authenticated Supabase session
lease endpoint.  If the file cannot be written (for example in a read-only
portable build), a process-local UUID is used so the app remains usable while
the server can still reject an older process after this one claims the lease.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from .local_data import app_data_dir, read_json, write_json_atomic


_DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PROCESS_DEVICE_ID = uuid.uuid4().hex


def device_identity_path(base: str | Path | None = None) -> Path:
    """Return the machine identity path without exposing account data."""

    return app_data_dir(base) / "device-identity.json"


def get_device_id(base: str | Path | None = None) -> str:
    """Load or create a stable random device id.

    Only a UUID hex value is accepted from disk.  Corrupt or legacy values are
    replaced atomically; no hostname, MAC address, or other fingerprint is
    collected.
    """

    path = device_identity_path(base)
    raw: Any = read_json(path, {})
    value = str(raw.get("device_id") or "").strip().lower() if isinstance(raw, dict) else ""
    if _DEVICE_ID_RE.fullmatch(value):
        return value
    value = uuid.uuid4().hex
    try:
        write_json_atomic(path, {"schema_version": 1, "device_id": value})
        return value
    except OSError:
        # A non-persistent fallback must not stop offline/local features.  It
        # is intentionally process-local so two launches can never share a
        # stale identity when the app cannot persist its lease.
        return _PROCESS_DEVICE_ID

