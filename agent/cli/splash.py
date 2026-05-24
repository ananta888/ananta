from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from agent.cli.logo_assets import load_logo
from agent.cli.logo_layout import COMPACT_HEADER_LINES, render_compact_header
from agent.cli.status_snapshot import StatusSnapshot

try:
    from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend
    from client_surfaces.operator_tui.animation3d.capabilities import detect_3d_capability
    from client_surfaces.operator_tui.animation3d.models import AnimationCapability, LogoAnimationBackend

    _3D_AVAILABLE = True
except ImportError:
    _3D_AVAILABLE = False
    AnimationCapability = object  # type: ignore[assignment, misc]
    BuiltinBackend = object  # type: ignore[assignment, misc]
    LogoAnimationBackend = object  # type: ignore[assignment, misc]

    def detect_3d_capability(*args: object, **kwargs: object) -> object:  # type: ignore[misc]
        class _Fake:
            enabled = False
            reason_code = "import_error"
            color_mode = ""
            preset_name = ""
            duration_ms = 0
            max_fps = 0
            terminal_width = 0
            terminal_height = 0
        return _Fake()


class SplashState(str, Enum):
    DISABLED = "disabled"
    FULLSCREEN = "fullscreen"
    TRANSITION = "transition"
    COMPACT_HEADER = "compact_header"
    SKIPPED = "skipped"


_VALID_TRANSITIONS: dict[SplashState, set[SplashState]] = {
    SplashState.DISABLED: set(),
    SplashState.FULLSCREEN: {
        SplashState.TRANSITION,
        SplashState.COMPACT_HEADER,
        SplashState.SKIPPED,
        SplashState.DISABLED,
    },
    SplashState.TRANSITION: {
        SplashState.COMPACT_HEADER,
        SplashState.DISABLED,
    },
    SplashState.COMPACT_HEADER: {
        SplashState.DISABLED,
    },
    SplashState.SKIPPED: {
        SplashState.DISABLED,
    },
}


def _height_for_width(width: int) -> int:
    if width >= 160:
        return 48
    if width >= 110:
        return 32
    return 24


class SplashTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class SplashContext:
    state: SplashState = SplashState.COMPACT_HEADER
    fullscreen_duration: float = 0.0
    transition_progress: float = 0.0
    entered_at: float = 0.0
    status: StatusSnapshot = field(default_factory=StatusSnapshot)

    def elapsed(self, now: float) -> float:
        return now - self.entered_at


class SplashMachine:
    def __init__(
        self,
        *,
        fullscreen_seconds: float = 2.0,
        transition_seconds: float = 0.5,
        clock: Callable[[], float] | None = None,
        animation_backend: LogoAnimationBackend | None = None,
        animation_capability: AnimationCapability | None = None,
    ) -> None:
        self._fullscreen_seconds = fullscreen_seconds
        self._transition_seconds = transition_seconds
        self._clock = clock or time.time
        self._3d_backend: LogoAnimationBackend | None = animation_backend
        self._3d_capability: object | None = animation_capability

        if os.getenv("ANANTA_TUI_SPLASH", "").strip() == "0":
            initial = SplashState.DISABLED
        else:
            initial = SplashState.FULLSCREEN

        self._context = SplashContext(
            state=initial,
            entered_at=self._clock() if initial != SplashState.DISABLED else 0.0,
        )

    @property
    def context(self) -> SplashContext:
        return self._context

    def _now(self) -> float:
        return self._clock()

    def transition_to(self, target: SplashState) -> SplashContext:
        current = self._context.state
        if current == target:
            return self._context
        if target not in _VALID_TRANSITIONS.get(current, set()):
            raise SplashTransitionError(
                f"Invalid splash transition: {current.value} \u2192 {target.value}"
            )
        now = self._now()
        self._context = SplashContext(
            state=target,
            entered_at=now,
            status=self._context.status,
        )
        return self._context

    def update_status(self, status: StatusSnapshot) -> None:
        self._context = SplashContext(
            state=self._context.state,
            fullscreen_duration=self._context.fullscreen_duration,
            transition_progress=self._context.transition_progress,
            entered_at=self._context.entered_at,
            status=status,
        )

    def tick(self, now: float | None = None) -> SplashContext:
        now = now if now is not None else self._now()
        ctx = self._context
        elapsed = ctx.elapsed(now)

        if ctx.state == SplashState.FULLSCREEN and elapsed >= self._fullscreen_seconds:
            new_state = SplashState.TRANSITION
            self._context = SplashContext(
                state=new_state,
                entered_at=now,
                status=ctx.status,
            )
        elif ctx.state == SplashState.TRANSITION:
            t_elapsed = ctx.elapsed(now)
            transition_seconds = max(0.001, self._transition_seconds)
            progress = min(1.0, t_elapsed / transition_seconds)
            self._context = SplashContext(
                state=SplashState.TRANSITION,
                fullscreen_duration=self._context.fullscreen_duration,
                transition_progress=progress,
                entered_at=ctx.entered_at,
                status=ctx.status,
            )
            if progress >= 1.0:
                self._context = SplashContext(
                    state=SplashState.COMPACT_HEADER,
                    entered_at=now,
                    status=ctx.status,
                )

        return self._context

    def skip(self) -> None:
        self.transition_to(SplashState.SKIPPED)

    def disable(self) -> None:
        self.transition_to(SplashState.DISABLED)

    def reset(self) -> None:
        self._context = SplashContext(
            state=SplashState.FULLSCREEN,
            entered_at=self._now(),
        )

    def render(self, snapshot: StatusSnapshot, *, width: int | None = None, color: bool | None = None) -> list[str]:
        ctx = self._context
        if ctx.state in (SplashState.DISABLED, SplashState.SKIPPED):
            return []

        _width = width or 80
        _color = color if color is not None else True

        if ctx.state == SplashState.FULLSCREEN:
            if self._3d_backend is not None and getattr(self._3d_capability, "enabled", False):
                elapsed = ctx.elapsed(self._now())
                cap = self._3d_capability
                preset = getattr(cap, "preset_name", "rotate_in")
                color_mode = getattr(cap, "color_mode", "truecolor")
                no_color = color_mode in ("mono", "plain_ascii")
                result = self._3d_backend.frame_at(
                    t=elapsed,
                    width=_width,
                    height=_height_for_width(_width),
                    options={
                        "preset": preset,
                        "color_mode": color_mode,
                        "no_color": no_color,
                        "no_ansi": color_mode == "plain_ascii",
                    },
                )
                if result.text and result.fallback_reason is None:
                    return result.text.split("\n")
            raw = load_logo(width=_width, color=_color)
            if not raw:
                return []
            return raw.split("\n")

        if ctx.state == SplashState.TRANSITION:
            raw = load_logo(width=_width, color=_color)
            if not raw:
                return []
            all_lines = raw.split("\n")
            progress = ctx.transition_progress
            total = len(all_lines)
            target = COMPACT_HEADER_LINES
            current_count = target + int((total - target) * (1.0 - progress))
            current_count = max(target, min(total, current_count))
            return all_lines[:current_count]

        return render_compact_header(snapshot, terminal_width=_width, color=_color)
