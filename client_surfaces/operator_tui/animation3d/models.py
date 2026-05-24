from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FrameResult:
    text: str
    visible_width: int
    visible_height: int
    ansi_used: bool
    fallback_reason: str | None = None


@dataclass(frozen=True)
class BackendCapabilities:
    supports_3d: bool = False
    max_fps: int = 0
    color_modes: tuple[str, ...] = ()
    preset_names: tuple[str, ...] = ()
    description: str = ""


@runtime_checkable
class LogoAnimationBackend(Protocol):
    def capabilities(self) -> BackendCapabilities:
        ...

    def frame_at(
        self,
        t: float,
        width: int,
        height: int,
        options: dict | None = None,
    ) -> FrameResult:
        ...


@dataclass(frozen=True)
class AnimationCapability:
    enabled: bool
    reason_code: str
    terminal_width: int
    terminal_height: int
    color_mode: str
    preset_name: str
    max_fps: int
    duration_ms: int


REASON_CODES = {
    "ok": "3D animation is enabled",
    "no_tty": "Not a TTY — 3D animation disabled",
    "too_small": "Terminal too small for 3D animation",
    "no_color": "NO_COLOR set — falling back to mono 3D",
    "disabled_by_env": "ANANTA_TUI_3D=0",
    "disabled_by_splash_env": "ANANTA_TUI_SPLASH=0",
    "reduced_motion": "Reduced motion preferred",
    "preset_not_found": "Preset not found, using default",
}


COLOR_MODES = ("truecolor", "ansi_256", "mono", "plain_ascii")


@dataclass(frozen=True)
class Vertex:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Edge:
    start: int
    end: int
    part_id: str = ""


@dataclass(frozen=True)
class GeometryModel:
    vertices: tuple[Vertex, ...]
    edges: tuple[Edge, ...]
    part_ids: tuple[str, ...] = ()
    label: str = ""

    def scale(self, factor: float) -> GeometryModel:
        return GeometryModel(
            vertices=tuple(Vertex(v.x * factor, v.y * factor, v.z * factor) for v in self.vertices),
            edges=self.edges,
            part_ids=self.part_ids,
            label=self.label,
        )
