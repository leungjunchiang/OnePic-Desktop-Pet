"""Pure selection and lifecycle policy for local pet effects.

The renderer/window owns no PetState knowledge.  This module deliberately has
no network, timer, thread, or QWidget code.  Stable work states, explicit
semantic events, and the deterministic color-mist easter egg are kept separate so
ordinary autonomous animation cannot make colors appear at random.
"""

from __future__ import annotations

import math
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
    GREEN = "green"
    CYAN = "cyan"
    PURPLE = "purple"


class EffectSource(str, Enum):
    STATE = "state"
    EVENT = "event"
    COLOR_MIST_WORLD = "color_mist_world"


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
    LocalEffectKind.CYAN: 250,
    LocalEffectKind.PURPLE: 300,
    LocalEffectKind.GOLD: 400,
    LocalEffectKind.RED: 500,
}


EFFECT_TIMINGS: dict[LocalEffectKind, EffectTiming] = {
    LocalEffectKind.RED: EffectTiming(2500, 600, 12000),
    LocalEffectKind.GOLD: EffectTiming(2800, 800, 10000),
    LocalEffectKind.BLUE: EffectTiming(3000, 1200, None),
    LocalEffectKind.GREEN: EffectTiming(3000, 1200, None),
    LocalEffectKind.CYAN: EffectTiming(2500, 800, 10000),
    LocalEffectKind.PURPLE: EffectTiming(2500, 900, 10000),
}


_EVENT_KINDS = frozenset(
    {
        LocalEffectKind.RED,
        LocalEffectKind.GOLD,
        LocalEffectKind.PURPLE,
        LocalEffectKind.CYAN,
    }
)


def resolve_state_effect(state: PetState) -> LocalEffectKind:
    """Legacy compatibility: autonomous PetState is not a color source."""

    del state
    return LocalEffectKind.NONE


def resolve_work_effect(
    work_status: str | None,
    *,
    focus_blue_enabled: bool = True,
) -> LocalEffectKind:
    """Resolve the explicit work lifecycle into a sustained local effect."""

    status = str(work_status or "").strip().casefold()
    if status == "focus":
        return LocalEffectKind.BLUE if focus_blue_enabled else LocalEffectKind.NONE
    if status == "rest":
        return LocalEffectKind.GREEN
    return LocalEffectKind.NONE


def resolve_event_effect(event: str | None) -> LocalEffectKind:
    """Resolve a named semantic event; unknown events are a no-op."""

    return {
        "annoyed": LocalEffectKind.RED,
        "angry": LocalEffectKind.RED,
        "reward": LocalEffectKind.GOLD,
        "completed": LocalEffectKind.GOLD,
        "milestone": LocalEffectKind.GOLD,
        "thinking": LocalEffectKind.PURPLE,
        "special": LocalEffectKind.PURPLE,
        "playful": LocalEffectKind.CYAN,
        "active_interaction": LocalEffectKind.CYAN,
    }.get(str(event or "").strip().casefold(), LocalEffectKind.NONE)


COLOR_MIST_SEQUENCE: tuple[LocalEffectKind, ...] = (
    LocalEffectKind.RED,
    LocalEffectKind.GOLD,
    LocalEffectKind.BLUE,
    LocalEffectKind.GREEN,
    LocalEffectKind.CYAN,
    LocalEffectKind.PURPLE,
)
COLOR_MIST_SLOT_MS = 3_000
COLOR_MIST_TOTAL_MS = 180_000
COLOR_MIST_CYCLE_MS = len(COLOR_MIST_SEQUENCE) * COLOR_MIST_SLOT_MS
COLOR_MIST_CROSSFADE_MS = 350


@dataclass
class ColorMistWorldState:
    started_at: float
    ends_at: float
    seed: int = 0
    last_slot_index: int = -1
    visual_origin_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.visual_origin_at:
            self.visual_origin_at = self.started_at

    def elapsed_ms(self, now: float) -> float:
        return max(0.0, (float(now) - self.visual_origin_at) * 1000.0)

    def slot_index(self, now: float) -> int:
        elapsed_ms = self.elapsed_ms(now)
        return int(elapsed_ms // COLOR_MIST_SLOT_MS)

    def kind_at(self, now: float) -> LocalEffectKind:
        return COLOR_MIST_SEQUENCE[self.slot_index(now) % len(COLOR_MIST_SEQUENCE)]

    def visual_state(
        self, now: float
    ) -> tuple[LocalEffectKind, LocalEffectKind, float, float]:
        """Return current/next kinds, crossfade mix, and continuous phase.

        The color timeline never enters ``NONE``.  The renderer can therefore
        keep one stable geometry and blend the two neighboring presets during
        the final 350ms of each slot.
        """

        elapsed_ms = self.elapsed_ms(now)
        cycle_position = elapsed_ms % COLOR_MIST_CYCLE_MS
        slot_index = int(cycle_position // COLOR_MIST_SLOT_MS)
        slot_elapsed = cycle_position % COLOR_MIST_SLOT_MS
        current = COLOR_MIST_SEQUENCE[slot_index % len(COLOR_MIST_SEQUENCE)]
        following = COLOR_MIST_SEQUENCE[(slot_index + 1) % len(COLOR_MIST_SEQUENCE)]
        transition_start = COLOR_MIST_SLOT_MS - COLOR_MIST_CROSSFADE_MS
        if slot_elapsed < transition_start:
            mix = 0.0
        else:
            raw = (slot_elapsed - transition_start) / COLOR_MIST_CROSSFADE_MS
            mix = -(math.cos(math.pi * max(0.0, min(1.0, raw))) - 1.0) / 2.0
        return current, following, mix, elapsed_ms / 1000.0


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
    """Policy for one reusable local effect window."""

    candidate_stability_ms = 320
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
        on_color_mist_world_frame: Callable[
            [LocalEffectKind, LocalEffectKind, float, float], None
        ] | None = None,
        on_color_mist_world_resume: Callable[[LocalEffectKind], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        duration_profile: str = "standard",
    ) -> None:
        self._on_start = on_start
        self._on_switch = on_switch
        self._on_sustain = on_sustain
        self._on_release = on_release
        self._on_stop = on_stop
        self._on_color_mist_world_frame = on_color_mist_world_frame or (lambda *_args: None)
        self._on_color_mist_world_resume = on_color_mist_world_resume or (lambda *_args: None)
        self._now = now
        self.duration_profile = str(duration_profile or "standard")
        self.enabled = True
        self.current_kind = LocalEffectKind.NONE
        self.requested_kind = LocalEffectKind.NONE
        self.current_source = EffectSource.STATE
        self.phase = EffectPhase.OFF
        self.entered_at = 0.0
        self.state_last_seen_at = 0.0
        self.min_hold_until = 0.0
        self.release_after = 0.0
        self._candidate_kind = LocalEffectKind.NONE
        self._candidate_since = 0.0
        self._background_kind = LocalEffectKind.NONE
        self._event_until = 0.0
        self._color_mist_world: ColorMistWorldState | None = None

    @property
    def active(self) -> bool:
        return self.phase is not EffectPhase.OFF

    @property
    def color_mist_world_active(self) -> bool:
        return self._color_mist_world is not None

    @staticmethod
    def color_mist_world_kind_at(started_at: float, now: float) -> LocalEffectKind:
        """Return the deterministic color-mist slot for monotonic timestamps."""

        session = ColorMistWorldState(
            started_at,
            started_at + COLOR_MIST_TOTAL_MS / 1000.0,
        )
        return session.kind_at(now)

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
        if self.enabled:
            return
        self._color_mist_world = None
        self.requested_kind = LocalEffectKind.NONE
        self._background_kind = LocalEffectKind.NONE
        self._begin_release(self._now())

    def _priority(self, kind: LocalEffectKind) -> int:
        return EFFECT_PRIORITY.get(kind, 0)

    def _apply_kind(
        self,
        kind: LocalEffectKind,
        now: float,
        *,
        source: EffectSource,
    ) -> None:
        kind = normalize_effect_kind(kind)
        if kind is LocalEffectKind.NONE:
            return
        previous = self.current_kind
        self.current_kind = kind
        self.current_source = source
        self.phase = EffectPhase.ENTERING
        self.entered_at = now
        timing = self._timing(kind)
        self.min_hold_until = now + timing.min_hold_ms / 1000.0
        self.release_after = 0.0
        self._event_until = (
            now + timing.max_hold_ms / 1000.0
            if source is EffectSource.EVENT and timing.max_hold_ms is not None
            else 0.0
        )
        self._candidate_kind = LocalEffectKind.NONE
        self._candidate_since = 0.0
        if previous is LocalEffectKind.NONE:
            self._on_start(kind)
        else:
            self._on_switch(kind)

    def request(
        self,
        kind: LocalEffectKind | str | None,
        now: float | None = None,
    ) -> None:
        """Request a stable state or dispatch an event-like kind."""

        requested = normalize_effect_kind(kind)
        if requested in _EVENT_KINDS:
            self.request_event(requested, now=now)
        else:
            self.request_state(requested, now=now)

    def request_state(
        self,
        kind: LocalEffectKind | str | None,
        now: float | None = None,
    ) -> None:
        now = self._now() if now is None else float(now)
        requested = normalize_effect_kind(kind) if self.enabled else LocalEffectKind.NONE
        self.requested_kind = requested
        self._background_kind = requested
        if self._color_mist_world is not None:
            return
        if self.current_source is EffectSource.EVENT:
            return
        if requested is self.current_kind:
            self.state_last_seen_at = now
            self._candidate_kind = LocalEffectKind.NONE
            if self.phase is EffectPhase.RELEASING:
                self.phase = EffectPhase.SUSTAINING
                self._on_sustain(requested)
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

    def request_event(
        self,
        kind: LocalEffectKind | str | None,
        now: float | None = None,
    ) -> None:
        """Start or extend one semantic event without another overlay."""

        now = self._now() if now is None else float(now)
        requested = normalize_effect_kind(kind)
        if not self.enabled or requested is LocalEffectKind.NONE:
            return
        if requested not in _EVENT_KINDS:
            self.request_state(requested, now=now)
            return
        if self._color_mist_world is not None:
            return
        if self.current_source is EffectSource.EVENT and requested is self.current_kind:
            timing = self._timing(requested)
            max_hold = timing.max_hold_ms or 0
            self._event_until = max(self._event_until, now + max_hold / 1000.0)
            self.state_last_seen_at = now
            return
        if (
            self.current_kind is LocalEffectKind.NONE
            or self.current_source is EffectSource.STATE
            or self._priority(requested) >= self._priority(self.current_kind)
        ):
            self.state_last_seen_at = now
            self._apply_kind(requested, now, source=EffectSource.EVENT)

    def start_color_mist_world(self, now: float | None = None, *, seed: int = 0) -> bool:
        """Start or extend one continuous deterministic 3-minute timeline."""

        if not self.enabled:
            return False
        now = self._now() if now is None else float(now)
        if self._color_mist_world is not None:
            # Extend only the deadline.  Keeping visual_origin_at means a
            # repeated trigger never jumps or restarts the colors.
            self._color_mist_world.ends_at = now + COLOR_MIST_TOTAL_MS / 1000.0
            self._color_mist_world.seed = int(seed)
            self._emit_color_mist_world_frame(now)
            return True
        self._color_mist_world = ColorMistWorldState(
            started_at=now,
            ends_at=now + COLOR_MIST_TOTAL_MS / 1000.0,
            seed=int(seed),
            last_slot_index=-1,
            visual_origin_at=now,
        )
        self._apply_kind(COLOR_MIST_SEQUENCE[0], now, source=EffectSource.COLOR_MIST_WORLD)
        self._color_mist_world.last_slot_index = 0
        self._emit_color_mist_world_frame(now)
        return True

    def _emit_color_mist_world_frame(self, now: float) -> None:
        if self._color_mist_world is None:
            return
        current, following, mix, phase = self._color_mist_world.visual_state(now)
        self.current_kind = current
        self.current_source = EffectSource.COLOR_MIST_WORLD
        self._on_color_mist_world_frame(current, following, mix, phase)

    def stop_color_mist_world(self, now: float | None = None) -> None:
        """Stop color mist world and restore the stable state without a blank frame."""

        if self._color_mist_world is None:
            return
        now = self._now() if now is None else float(now)
        self._color_mist_world = None
        desired = self._background_kind if self.enabled else LocalEffectKind.NONE
        self.requested_kind = desired
        if desired is LocalEffectKind.NONE:
            self._begin_release(now)
        else:
            self.current_kind = desired
            self.current_source = EffectSource.STATE
            self.phase = EffectPhase.SUSTAINING
            self.entered_at = now
            self.min_hold_until = now
            self.release_after = 0.0
            self._on_color_mist_world_resume(desired)

    def toggle_color_mist_world(self, now: float | None = None, *, seed: int = 0) -> bool:
        """Toggle the one color-mist session and return its resulting state."""

        if self._color_mist_world is not None:
            self.stop_color_mist_world(now)
            return False
        return bool(self.start_color_mist_world(now, seed=seed))

    def _begin_release(self, now: float) -> None:
        if self.current_kind is LocalEffectKind.NONE or self.phase is EffectPhase.RELEASING:
            return
        timing = self._timing(self.current_kind)
        self.release_after = now
        self.phase = EffectPhase.RELEASING
        self._on_release(self.current_kind, round(timing.release_grace_ms))

    def tick(self, now: float | None = None) -> None:
        now = self._now() if now is None else float(now)
        if self._color_mist_world is not None:
            if now >= self._color_mist_world.ends_at:
                self.stop_color_mist_world(now)
                return
            slot_index = self._color_mist_world.slot_index(now)
            self._color_mist_world.last_slot_index = slot_index
            self._emit_color_mist_world_frame(now)
            return

        if self.current_kind is LocalEffectKind.NONE:
            if (
                self._candidate_kind is not LocalEffectKind.NONE
                and now - self._candidate_since >= self.candidate_stability_ms / 1000.0
            ):
                self.requested_kind = self._candidate_kind
                self._apply_kind(self._candidate_kind, now, source=EffectSource.STATE)
            return

        if (
            self.phase is EffectPhase.ENTERING
            and now - self.entered_at >= self.entry_duration_ms / 1000.0
        ):
            self.phase = EffectPhase.SUSTAINING
            self._on_sustain(self.current_kind)

        if self.current_source is EffectSource.EVENT:
            if now >= self._event_until:
                desired = self._background_kind if self.enabled else LocalEffectKind.NONE
                self.requested_kind = desired
                if desired is LocalEffectKind.NONE:
                    self._begin_release(now)
                elif desired is self.current_kind:
                    self.current_source = EffectSource.STATE
                    self.phase = EffectPhase.SUSTAINING
                    self._on_sustain(desired)
                else:
                    self._apply_kind(desired, now, source=EffectSource.STATE)
            return

        timing = self._timing(self.current_kind)
        if self.requested_kind is self.current_kind:
            return
        if self.requested_kind is LocalEffectKind.NONE:
            if now >= self.release_after and self.phase is not EffectPhase.RELEASING:
                self._begin_release(now)
            return
        if now < self.min_hold_until:
            return
        if self._candidate_kind is not self.requested_kind:
            self._candidate_kind = self.requested_kind
            self._candidate_since = now
            return
        if now - self._candidate_since >= self.candidate_stability_ms / 1000.0:
            self._apply_kind(self.requested_kind, now, source=EffectSource.STATE)

    def finished(self) -> None:
        """Called by the reusable window after a release animation ends."""

        previous = self.current_kind
        self.current_kind = LocalEffectKind.NONE
        self.current_source = EffectSource.STATE
        self.phase = EffectPhase.OFF
        self.entered_at = 0.0
        self.min_hold_until = 0.0
        self.release_after = 0.0
        self._event_until = 0.0
        self._candidate_kind = LocalEffectKind.NONE
        self._candidate_since = 0.0
        if previous is not LocalEffectKind.NONE:
            self._on_stop()
        if self.enabled and self.requested_kind is not LocalEffectKind.NONE:
            desired = self.requested_kind
            self.requested_kind = LocalEffectKind.NONE
            self.request_state(desired)

    def force_stop(self) -> None:
        self._color_mist_world = None
        self.requested_kind = LocalEffectKind.NONE
        self._background_kind = LocalEffectKind.NONE
        self._candidate_kind = LocalEffectKind.NONE
        self.current_kind = LocalEffectKind.NONE
        self.current_source = EffectSource.STATE
        self.phase = EffectPhase.OFF
        self._on_stop()
