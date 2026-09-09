"""Small, atomic local JSON storage used by Lili's time-memory features.

The first version of the paper/timeline features is intentionally local.  This
module keeps the path and write semantics in one place so a future Supabase
sync can migrate the records without changing every manager.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


def platform_app_data_root() -> Path:
    """Return the stable per-user application-data root for this platform."""

    if sys.platform == "darwin":
        explicit = os.environ.get("LOCALAPPDATA")
        if explicit:
            return Path(explicit)
        return Path.home() / "Library" / "Application Support"
    if os.name == "nt":
        value = os.environ.get("LOCALAPPDATA")
        return Path(value) if value else Path.home() / "AppData" / "Local"
    value = os.environ.get("XDG_CONFIG_HOME")
    return Path(value) if value else Path.home() / ".config"


def app_data_dir(base: str | Path | None = None) -> Path:
    """Return Lili's writable per-user data directory."""

    root = Path(base) if base is not None else platform_app_data_root()
    return root / "Lili"


def local_data_path(filename: str, base: str | Path | None = None) -> Path:
    """Return a path below the app data directory."""

    clean = Path(str(filename).replace("\\", "/")).name
    if not clean.endswith(".json"):
        clean += ".json"
    return app_data_dir(base) / clean


def account_data_dir(account_id: str | None = None, base: str | Path | None = None) -> Path:
    """Return an account-scoped Lili data directory.

    The anonymous namespace is deliberately separate from every Supabase user
    so offline data cannot be uploaded after a later login.
    """

    return app_data_dir(base) / "accounts" / account_storage_key(account_id)


def account_storage_key(account_id: str | None = None) -> str:
    """Return the stable, filesystem-safe namespace for one account."""

    value = str(account_id or "").strip().casefold()
    return re.sub(r"[^a-z0-9._-]", "_", value)[:80] or "anonymous"


def legacy_private_app_data_root() -> Path:
    """Return the pre-platform-normalization data root.

    Older macOS/Linux builds stored the focus timer and analytics ledger below
    ``~/.desktop_pet`` while logs and every other account store used the native
    application-data directory.  Windows used ``LOCALAPPDATA`` in both paths,
    so this helper resolves to the same directory there.
    """

    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / ".desktop_pet"


def legacy_account_data_dir(account_id: str | None = None) -> Path:
    """Return the exact account directory used by older focus builds."""

    return legacy_private_app_data_root() / "Lili" / "accounts" / account_storage_key(account_id)


def adopt_legacy_account_file(
    filename: str,
    account_id: str | None = None,
    *,
    destination: Path | None = None,
    source: Path | None = None,
) -> Path | None:
    """Atomically copy one exact legacy account file when the target is absent.

    The source is deliberately retained.  This is a one-way, idempotent
    namespace repair, not a destructive move and not an aggregate-time import.
    Callers decide which structured files are safe to adopt.
    """

    clean = Path(str(filename).replace("\\", "/")).name
    if not clean:
        return None
    target = destination or (account_data_dir(account_id) / clean)
    legacy = source or (legacy_account_data_dir(account_id) / clean)
    try:
        if target == legacy or target.exists() or not legacy.is_file():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.legacy-import.tmp")
        shutil.copyfile(legacy, temporary)
        temporary.replace(target)
        return legacy
    except OSError:
        return None


def account_local_data_path(
    filename: str,
    account_id: str | None = None,
    base: str | Path | None = None,
) -> Path:
    """Return a sanitized JSON path inside one account namespace."""

    clean = Path(str(filename).replace("\\", "/")).name
    if not clean.endswith(".json"):
        clean += ".json"
    return account_data_dir(account_id, base) / clean


def read_json(path: Path, default: Any) -> Any:
    """Read JSON and return *default* for missing/corrupt old files."""

    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> Path:
    """Write UTF-8 JSON through a sibling temporary file, then replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
