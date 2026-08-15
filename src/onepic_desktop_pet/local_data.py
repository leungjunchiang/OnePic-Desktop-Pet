"""Small, atomic local JSON storage used by Lili's time-memory features.

The first version of the paper/timeline features is intentionally local.  This
module keeps the path and write semantics in one place so a future Supabase
sync can migrate the records without changing every manager.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def app_data_dir(base: str | Path | None = None) -> Path:
    """Return Lili's writable per-user data directory."""

    if base is not None:
        root = Path(base)
    else:
        root = Path(os.environ.get("LOCALAPPDATA", "")) if os.environ.get("LOCALAPPDATA") else Path.home() / ".desktop_pet"
    return root / "Lili"


def local_data_path(filename: str, base: str | Path | None = None) -> Path:
    """Return a path below the app data directory."""

    clean = Path(str(filename).replace("\\", "/")).name
    if not clean.endswith(".json"):
        clean += ".json"
    return app_data_dir(base) / clean


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

