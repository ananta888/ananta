from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext, DrawResult
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


def _clip_line(text: str, width: int) -> str:
    if width <= 0:
        return ""
    value = str(text or "")
    return value[:width].ljust(width)


def _cursor_move(x: int, y: int) -> str:
    # Terminal coordinates are 1-based.
    return f"\x1b[{max(1, y + 1)};{max(1, x + 1)}H"


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
            lines = [f"[degraded:{frame.frame_type}] {frame.mime_or_format} {frame.width}x{frame.height}"]
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

