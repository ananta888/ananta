from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext, DrawResult
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


@dataclass
class NoopDiagnosticsAdapter:
    adapter_id: str = "noop_diagnostics"
    draw_count: int = 0
    last_region: ViewportRegion | None = None
    last_frame_metadata: dict[str, Any] = field(default_factory=dict)

    def draw(self, frame: RenderFrame, *, region: ViewportRegion, stream: Any, context: DrawContext) -> DrawResult:
        _ = stream
        _ = context
        self.draw_count += 1
        self.last_region = region
        self.last_frame_metadata = dict(frame.metadata or {})
        return DrawResult(
            drawn=False,
            reason="noop",
            metadata={
                "draw_count": self.draw_count,
                "frame_type": frame.frame_type,
            },
        )

