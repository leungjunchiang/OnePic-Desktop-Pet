"""Low-overhead GUI performance diagnostics.

The monitor deliberately keeps only a small rolling sample in memory.  It is
safe to leave enabled in production builds: normal callbacks are not logged,
and event-loop lag is written only when it exceeds the diagnostic threshold.
"""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from time import monotonic, perf_counter
from typing import Iterator

from .lifecycle_log import lifecycle_log


class PerformanceMonitor:
    """Collect callback timings and report useful percentiles on demand."""

    def __init__(self, sample_size: int = 128) -> None:
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=max(16, int(sample_size)))
        )

    def record(self, name: str, elapsed_ms: float) -> None:
        value = max(0.0, float(elapsed_ms))
        self._samples[str(name)].append(value)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self.record(name, (perf_counter() - started) * 1000.0)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for name, values in self._samples.items():
            if not values:
                continue
            ordered = sorted(values)
            p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
            result[name] = {
                "count": len(ordered),
                "avg_ms": round(sum(ordered) / len(ordered), 3),
                "p95_ms": round(ordered[p95_index], 3),
                "max_ms": round(ordered[-1], 3),
            }
        return result

    def log_summary(self, *, source: str = "periodic") -> None:
        snapshot = self.snapshot()
        if snapshot:
            lifecycle_log("perf.summary", source=source, metrics=snapshot)


class EventLoopLagTracker:
    """Measure delay between timer ticks without doing work on the tick."""

    def __init__(self, monitor: PerformanceMonitor, expected_interval_ms: int = 250) -> None:
        self.monitor = monitor
        self.expected_interval_ms = max(50, int(expected_interval_ms))
        self.last_tick = monotonic()

    def tick(self, *, active_window: str = "", active_page: str = "") -> float:
        current = monotonic()
        elapsed_ms = max(0.0, (current - self.last_tick) * 1000.0)
        self.last_tick = current
        lag_ms = max(0.0, elapsed_ms - self.expected_interval_ms)
        self.monitor.record("event_loop.lag", lag_ms)
        if lag_ms > 100.0:
            lifecycle_log(
                "perf.event_loop_lag",
                lag_ms=round(lag_ms, 1),
                expected_interval_ms=self.expected_interval_ms,
                actual_interval_ms=round(elapsed_ms, 1),
                active_window=active_window,
                active_page=active_page,
            )
        return lag_ms
