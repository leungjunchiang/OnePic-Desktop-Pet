"""Small helpers for keeping Qt worker threads alive until they are stopped.

Qt owns the native ``QThread`` object separately from the Python wrapper.  A
running thread must not be destroyed while its native work is still active;
that is especially important during application shutdown and when a window
owns short-lived network workers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PySide6.QtCore import QObject, QThread

from .lifecycle_log import lifecycle_log

LOGGER = logging.getLogger(__name__)


def child_qthreads(*roots: QObject | None) -> tuple[QThread, ...]:
    """Return each QThread below the supplied QObject roots once."""

    found: list[QThread] = []
    seen: set[int] = set()
    for root in roots:
        if root is None:
            continue
        candidates: Iterable[QObject]
        try:
            if isinstance(root, QThread):
                candidates = (root, *root.findChildren(QThread))
            else:
                candidates = root.findChildren(QThread)
        except RuntimeError:
            # A top-level utility window can finish deletion between two
            # shutdown passes. Its already-destroyed C++ object is harmless.
            continue
        for candidate in candidates:
            marker = id(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            found.append(candidate)  # type: ignore[arg-type]
    return tuple(found)


def request_thread_stop(thread: QThread | None) -> bool:
    """Ask a worker to stop without terminating its native thread forcefully."""

    if thread is None:
        return True
    try:
        if not thread.isRunning():
            return True
        lifecycle_log(
            "qthread.quit.request",
            thread,
            owner="request_thread_stop",
            thread_class=type(thread).__name__,
        )
        # Custom QThreads may override ``run`` with a blocking condition loop
        # and therefore never enter QThread's event loop. Give such workers a
        # cooperative stop hook before calling quit(); ordinary QThreads do
        # not expose one and keep the existing interruption/quit behavior.
        stopper = getattr(thread, "stop", None)
        if callable(stopper):
            stopper()
        thread.requestInterruption()
        # This stops a worker using QThread.exec().  Custom run() methods still
        # need to return naturally; requestInterruption remains harmless there.
        thread.quit()
        return False
    except RuntimeError:
        # The C++ object may already have been deleted by Qt's event loop.
        return True


def running_threads(*roots: QObject | None) -> tuple[QThread, ...]:
    """Return live child threads, tolerating a wrapper deleted by Qt."""

    result: list[QThread] = []
    for thread in child_qthreads(*roots):
        try:
            if thread.isRunning():
                result.append(thread)
        except RuntimeError:
            continue
    return tuple(result)


def request_stop_all(*roots: QObject | None) -> tuple[QThread, ...]:
    """Request all descendant QThreads to stop and return those still live."""

    threads = child_qthreads(*roots)
    for thread in threads:
        request_thread_stop(thread)
    return running_threads(*roots)


def wait_for_thread(thread: QThread | None, timeout_ms: int) -> bool:
    """Request a worker stop and wait for its native thread to finish."""

    if thread is None:
        return True
    try:
        if not thread.isRunning():
            # Synchronize with the native thread's final teardown before a
            # later deleteLater() or QObject-parent destruction.
            thread.wait(0)
            return True
        thread.requestInterruption()
        lifecycle_log(
            "qthread.quit.request",
            thread,
            owner="wait_for_thread",
            thread_class=type(thread).__name__,
            timeout_ms=int(timeout_ms),
        )
        thread.quit()
        stopped = bool(thread.wait(max(0, int(timeout_ms))))
        if not stopped:
            LOGGER.error(
                "[Lifecycle] QThread did not stop within %sms: %s",
                timeout_ms,
                type(thread).__name__,
            )
        return stopped
    except RuntimeError:
        return True
