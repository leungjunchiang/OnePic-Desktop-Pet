"""Pure state selection and lifecycle policy for local pet effects.

The renderer/window owns no PetState knowledge.  This module maps the local
pet state to one effect kind and keeps that request stable long enough for the
visual effect to read as an intentional status, rather than a frame-by-frame
flash.  It deliberately has no network, timer, thread, or QWidget code.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .behavior import PetState


class LocalEffectKind(str, Enum):
    NONE = "none"
    RED = "red"
    GOLD = "gold"
    BLUE = "blue"
    PURPLE = "purple"
    GREEN = "green"


class EffectPhase(str, Enum):
    OFF = "off"
    ENTERING = "entering"
    SUSTAINING = "sustaining"
    RELEASING = "releasing"


@dataclass(frozen=True)
class EffectTiming:
    min_hold_ms: int
    release_grace_ms: int
    max_hold_ms: int | None = None


EFFECT_PRIORITY: dict[LocalEffectKind, int] = {
    LocalEffectKind.NONE: 0,
    LocalEffectKind.BLUE: 100,
    LocalEffectKind.GREEN: 200,
    LocalEffectKind.PURPLE: 300,
    LocalEffectKind.GOLD: 400,
    LocalEffectKind.RED: 500,
}


EFFECT_TIMINGS: dict[LocalEffectKind, EffectTiming] = {
    LocalEffectKind.RED: EffectTiming(2500, 600, 6000),
    LocalEffectKind.GOLD: EffectTiming(2800, 800, 5000),
    LocalEffectKind.BLUE: EffectTiming(3000, 1200, None),
    LocalEffectKind.PURPLE: EffectTiming(2500, 900, 6000),
    LocalEffectKind.GREEN: EffectTiming(3000, 1200, None),
}

STATE_EFFECT_MAP: dict[PetState, LocalEffectKind] = {
    PetState.ANNOYED: LocalEffectKind.RED,
    PetState.HAPPY: LocalEffectKind.GOLD,
    PetState.WAVE: LocalEffectKind.GOLD,
    PetState.SELFIE: LocalEffectKind.GOLD,
    PetState.CURIOUS: LocalEffectKind.PURPLE,
    PetState.SURPRISED: LocalEffectKind.PURPLE,
    PetState.SLEEPY: LocalEffectKind.BLUE,
}


def resolve_state_effect(state: PetState) -> LocalEffectKind:
    """Resolve a local PetState without making the renderer know PetState."""

    return STATE_EFFECT_MAP.get(state, LocalEffectKind.NONE)


def normalize_effect_kind(value: LocalEffectKind | str | None) -> LocalEffectKind:
    if isinstance(value, LocalEffectKind):
        return value
    try:
        return LocalEffectKind(str(value or "none").strip().casefold())
    except ValueError:
        return LocalEffectKind.NONE


def duration_multiplier(value: str | None) -> float:
    return {"short": 0.7, "standard": 1.0, "long": 1.5}.get(
        str(value or "standard").strip().casefold(),
        1.0,
    )


class LocalEffectManager:
    """Drive one reusable local effect window from a state request.

    The manager emits commands through callbacks supplied by ``PetWindow``.
    This keeps the lifecycle policy testable and prevents it from constructing
    extra Qt objects.  ``tick`` is called by the existing effect window's one
    33 ms timer.
    """

    candidate_stability_ms = 220
    entry_duration_ms = 900
    crossfade_duration_ms = 360

    def __init__(
        self,
        *,
        on_start: Callable[[LocalEffectKind], None],
        on_switch: Callable[[LocalEffectKind], None],
        on_sustain: Callable[[LocalEffectKind], None],
        on_release: Callable[[LocalEffectKind, int], None],
        on_stop: Callable[[], None],
        now: Callable[[], float] = time.monotonic,
        duration_profile: str = "standard",
    ) -> None:
        self._on_start = on_start
        self._on_switch = on_switch
        self._on_sustain = on_sustain
        self._on_release = on_release
        self._on_stop = on_stop
        self._now = now
        self.duration_profile = str(duration_profile or "standard")
        self.enabled = True
        self.current_kind = LocalEffectKind.NONE
        self.requested_kind = LocalEffectKind.NONE
        self.phase = EffectPhase.OFF
        self.entered_at = 0.0
        self.state_last_seen_at = 0.0
        self.min_hold_until = 0.0
        self.release_after = 0.0
        self._candidate_kind = LocalEffectKind.NONE
        self._candidate_since = 0.0

    @property
    def active(self) -> bool:
        return self.phase is not EffectPhase.OFF

    def _timing(self, kind: LocalEffectKind) -> EffectTiming:
        base = EFFECT_TIMINGS.get(kind, EffectTiming(0, 0, 0))
        multiplier = duration_multiplier(self.duration_profile)
        return EffectTiming(
            round(base.min_hold_ms * multiplier),
            round(base.release_grace_ms * multiplier),
            None if base.max_hold_ms is None else round(base.max_hold_ms * multiplier),
        )

    def set_duration_profile(self, value: str) -> None:
        self.duration_profile = str(value or "standard")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.request(LocalEffectKind.NONE if not self.enabled else self.requested_kind)

    def _priority(self, kind: LocalEffectKind) -> int:
        return EFFECT_PRIORITY.get(kind, 0)

    def _apply_kind(self, kind: LocalEffectKind, now: float) -> None:
        kind = normalize_effect_kind(kind)
        if kind is LocalEffectKind.NONE:
            return
        previous = self.current_kind
        self.current_kind = kind
        self.phase = EffectPhase.ENTERING
        self.entered_at = now
        timing = self._timing(kind)
        self.min_hold_until = now + timing.min_hold_ms / 1000.0
        self.release_after = 0.0
        self._candidate_kind = LocalEffectKind.NONE
        self._candidate_since = 0.0
        if previous is LocalEffectKind.NONE:
            self._on_start(kind)
        else:
            self._on_switch(kind)

    def request(self, kind: LocalEffectKind | str | None, now: float | None = None) -> None:
        now = self._now() if now is None else float(now)
        requested = normalize_effect_kind(kind) if self.enabled else LocalEffectKind.NONE
        self.requested_kind = requested
        if requested is self.current_kind:
            self.state_last_seen_at = now
            self._candidate_kind = LocalEffectKind.NONE
            if self.phase is EffectPhase.RELEASING:
                self.phase = EffectPhase.SUSTAINING
                self._on_sustain(requested)
            return

        if requested is not LocalEffectKind.NONE and (
            requested is LocalEffectKind.RED
            or self.current_kind is LocalEffectKind.NONE
            or (
                self.current_kind is not LocalEffectKind.NONE
                and self._priority(requested) > self._priority(self.current_kind)
            )
        ):
            self.state_last_seen_at = now
            self._apply_kind(requested, now)
            return

        self.state_last_seen_at = now
        self._candidate_kind = requested
        self._candidate_since = now
        if requested is LocalEffectKind.NONE and self.current_kind is not LocalEffectKind.NONE:
            timing = self._timing(self.current_kind)
            self.release_after = max(
                self.min_hold_until,
                now + timing.release_grace_ms / 1000.0,
            )

    def tick(self, now: float | None = None) -> None:
        now = self._now() if now is None else float(now)
        if self.current_kind is LocalEffectKind.NONE:
            if (
                self._candidate_kind is not LocalEffectKind.NONE
                and now - self._candidate_since >= self.candidate_stability_ms / 1000.0
            ):
                self.requested_kind = self._candidate_kind
                self._apply_kind(self._candidate_kind, now)
            return

        if self.phase is EffectPhase.ENTERING and now - self.entered_at >= self.entry_duration_ms / 1000.0:
            self.phase = EffectPhase.SUSTAINING
            self._on_sustain(self.current_kind)

        timing = self._timing(self.current_kind)
        if timing.max_hold_ms is not None and now - self.entered_at >= timing.max_hold_ms / 1000.0:
            self.requested_kind = LocalEffectKind.NONE
            self.release_after = max(self.min_hold_until, now)

        if self.requested_kind is self.current_kind:
            return

        if self.requested_kind is LocalEffectKind.NONE:
            if now >= self.release_after and self.phase is not EffectPhase.RELEASING:
                self.phase = EffectPhase.RELEASING
                self._on_release(self.current_kind, round(timing.release_grace_ms))
            return

        if now < self.min_hold_until:
            return
        if self._candidate_kind is not self.requested_kind:
            self._candidate_kind = self.requested_kind
            self._candidate_since = now
            return
        if now - self._candidate_since >= self.candidate_stability_ms / 1000.0:
            self._apply_kind(self.requested_kind, now)

    def finished(self) -> None:
        """Called by the reusable window after a release animation ends."""

        previous = self.current_kind
        self.current_kind = LocalEffectKind.NONE
        self.phase = EffectPhase.OFF
        self.entered_at = 0.0
        self.min_hold_until = 0.0
        self.release_after = 0.0
        self._candidate_kind = LocalEffectKind.NONE
        self._candidate_since = 0.0
        if self.enabled and self.requested_kind is not LocalEffectKind.NONE:
            self.requested_kind = LocalEffectKind.NONE
        if previous is not LocalEffectKind.NONE:
            self._on_stop()

    def force_stop(self) -> None:
        self.requested_kind = LocalEffectKind.NONE
        self._candidate_kind = LocalEffectKind.NONE
        self.current_kind = LocalEffectKind.NONE
        self.phase = EffectPhase.OFF
        self._on_stop()
