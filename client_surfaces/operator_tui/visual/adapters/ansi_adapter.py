from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext, DrawResult
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

_ANSI_STRIP = re.compile(r"\x1b\[[0-9;]*m")


def _clip_line(text: str, width: int) -> str:
    if width <= 0:
        return ""
    value = str(text or "")
    return value[:width].ljust(width)


def _cursor_move(x: int, y: int) -> str:
    return f"\x1b[{max(1, y + 1)};{max(1, x + 1)}H"


def _raster_fallback_lines(frame: RenderFrame, *, columns: int, rows: int) -> list[str]:
    """Build a human-readable ANSI fallback for raster frames (TGFX-010)."""
    dim = "\x1b[2m"
    yellow = "\x1b[33m"
    cyan = "\x1b[36m"
    reset = "\x1b[0m"
    lines: list[str] = []

    scene = str(frame.metadata.get("scene_type") or "raster")
    renderer = str(frame.metadata.get("renderer") or "?")
    w, h = frame.width, frame.height
    reason = str(frame.metadata.get("degraded_reason") or "")

    lines.append(f"{yellow}╔{'═' * max(2, columns - 2)}╗{reset}")
    lines.append(f"{yellow}║{reset} {cyan}Grafik-Ausgabe nicht verfügbar{reset}".ljust(columns))
    lines.append(f"{yellow}║{reset} {dim}Scene: {scene}  Renderer: {renderer}  Size: {w}×{h}{reset}")
    if reason:
        lines.append(f"{yellow}║{reset} {dim}Grund: {reason[:columns - 12]}{reset}")

    # Show alt_text or fallback_text from frame metadata
    alt = str(frame.metadata.get("alt_text") or frame.metadata.get("fallback_text") or "")
    if alt:
        lines.append(f"{yellow}║{reset} {dim}{alt[:columns - 4]}{reset}")

    # Diagram ids
    diag_ids = frame.metadata.get("diagram_ids")
    if diag_ids:
        ids_str = ", ".join(str(d) for d in diag_ids[:3])
        lines.append(f"{yellow}║{reset} {dim}Diagramme: {ids_str[:columns - 14]}{reset}")

    lines.append(f"{yellow}║{reset}")
    hint = "→ Kitty/Sixel-Terminal oder ANSI-Quellmodus aktivieren"
    lines.append(f"{yellow}║{reset} {dim}{hint[:columns - 4]}{reset}")
    lines.append(f"{yellow}╚{'═' * max(2, columns - 2)}╝{reset}")

    # Pad or truncate to rows
    while len(lines) < rows:
        lines.append("")
    return lines[:rows]


@dataclass
class AnsiOutputAdapter:
    adapter_id: str = "ansi"
    last_error: str = ""
    draw_calls: int = 0
    _last_frame_metadata: dict[str, Any] = field(default_factory=dict)

    def draw(self, frame: RenderFrame, *, region: ViewportRegion, stream: Any, context: DrawContext) -> DrawResult:
        _ = context
        lines: list[str]
        if frame.frame_type == "ansi" and isinstance(frame.payload, list):
            lines = [str(row) for row in frame.payload]
        else:
            # Raster/unknown frame → rich fallback panel (TGFX-010)
            lines = _raster_fallback_lines(frame, columns=region.columns, rows=region.rows)

        out: list[str] = []
        for row_idx in range(region.rows):
            line = lines[row_idx] if row_idx < len(lines) else ""
            out.append(_cursor_move(region.x, region.y + row_idx) + _clip_line(line, region.columns))
        stream.write("".join(out))
        self.draw_calls += 1
        self._last_frame_metadata = dict(frame.metadata or {})
        return DrawResult(
            drawn=True,
            reason="ok",
            metadata={"draw_calls": self.draw_calls, "frame_type": frame.frame_type},
        )

    def last_frame_metadata(self) -> dict[str, Any]:
        return dict(self._last_frame_metadata)
