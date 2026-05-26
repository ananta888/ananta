from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

LogoRendererKind = Literal["text_lines", "stream_sequences"]


@dataclass(frozen=True, slots=True)
class LogoFrame:
    kind: LogoRendererKind
    text_lines: tuple[str, ...] = ()
    sequence: str = ""
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogoRendererProbe:
    term: str = ""
    term_program: str = ""
    colorterm: str = ""
    no_color: bool = False
    is_tty: bool = True
    width: int = 120
    height: int = 32
    env: dict[str, str] = field(default_factory=dict)


class LogoWriter(Protocol):
    def write(self, data: str) -> int: ...


class LogoRenderer(Protocol):
    name: str
    quality_rank: int
    kind: LogoRendererKind

    def detect(self, probe: LogoRendererProbe) -> bool: ...

    def supports_animation(self) -> bool: ...

    def supports_truecolor(self) -> bool: ...

    def get_capabilities(self) -> dict[str, str | int | float | bool]: ...

    def clear_region(self, *, x: int, y: int, width: int, height: int, writer: LogoWriter | None = None) -> str: ...

    def render_frame(
        self,
        *,
        width_cells: int,
        height_cells: int,
        t: float = 0.0,
        writer: LogoWriter | None = None,
    ) -> LogoFrame: ...

    def render_sequence(
        self,
        *,
        width_cells: int,
        height_cells: int,
        frame_count: int,
        fps: int,
        writer: LogoWriter | None = None,
    ) -> list[LogoFrame]: ...


class TerminalGraphicsBackend(Protocol):
    """Protocol for terminal graphics backends used by the header renderer stack."""

    name: str

    def render_frame(self, *, width_cells: int, height_cells: int, t: float = 0.0, writer: LogoWriter | None = None) -> LogoFrame: ...

    def clear_region(self, *, x: int, y: int, width: int, height: int, writer: LogoWriter | None = None) -> str: ...

    def supports_animation(self) -> bool: ...

    def supports_truecolor(self) -> bool: ...

    def get_capabilities(self) -> dict[str, str | int | float | bool]: ...
