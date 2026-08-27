"""Low-overhead lifecycle tracing for native-exit investigations.

The packaged application has no console, and a native Qt crash does not pass
through Python's exception hooks.  This module therefore records a small,
bounded JSON-lines trace of important QObject/window/media transitions.  It
is deliberately best-effort: diagnostics must never become another reason
for the desktop pet to stop.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .local_data import platform_app_data_root


LOGGER = logging.getLogger("onepic_desktop_pet.lifecycle")
_HANDLER_MARKER = "_lili_lifecycle_handler"
_CONFIGURED_PATH: Path | None = None


def configure_lifecycle_logging() -> Path | None:
    """Enable a bounded per-user lifecycle trace and return its path."""

    global _CONFIGURED_PATH
    try:
        log_dir = platform_app_data_root() / "Lili" / "diagnostics"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "lifecycle.log"
        if not any(
            getattr(handler, _HANDLER_MARKER, False)
            for handler in LOGGER.handlers
        ):
            handler = RotatingFileHandler(
                log_path,
                maxBytes=1_048_576,
                backupCount=3,
                encoding="utf-8",
            )
            setattr(handler, _HANDLER_MARKER, True)
            handler.setFormatter(logging.Formatter("%(message)s"))
            LOGGER.addHandler(handler)
            LOGGER.setLevel(logging.INFO)
            LOGGER.propagate = False
        _CONFIGURED_PATH = log_path
        return log_path
    except Exception:
        return _CONFIGURED_PATH


def lifecycle_log(event: str, obj: object | None = None, **fields: Any) -> None:
    """Write one compact structured lifecycle record.

    QObject inspection is intentionally limited to cheap state accessors.  In
    particular this function never calls ``winId()``, ``show()``, ``close()``
    or anything that can create a native handle or re-enter Qt.
    """

    try:
        payload: dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "thread_id": getattr(threading, "get_native_id", threading.get_ident)(),
            "thread": threading.current_thread().name,
            "event": str(event),
        }
        if obj is not None:
            payload["object"] = _object_state(obj)
        for key, value in fields.items():
            payload[str(key)] = _safe_value(value)
        LOGGER.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Diagnostics are strictly non-critical, including during QObject
        # teardown when even a harmless property read can fail.
        return


def lifecycle_log_path() -> Path | None:
    """Return the active log path, if lifecycle logging was configured."""

    return _CONFIGURED_PATH


def _object_state(obj: object) -> dict[str, Any]:
    state: dict[str, Any] = {"class": type(obj).__name__}
    for name in ("objectName", "isVisible", "isHidden", "isEnabled", "isModal"):
        accessor = getattr(obj, name, None)
        if not callable(accessor):
            continue
        try:
            value = accessor()
        except Exception:
            continue
        if name == "objectName" and not value:
            continue
        state[name] = _safe_value(value)
    return state


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:12]]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in list(value.items())[:24]}
    return str(value)
