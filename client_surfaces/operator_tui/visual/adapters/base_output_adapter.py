from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


@dataclass(frozen=True)
class DrawContext:
    now: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DrawResult:
    drawn: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class OutputAdapter(Protocol):
    adapter_id: str

    def draw(self, frame: RenderFrame, *, region: ViewportRegion, stream: Any, context: DrawContext) -> DrawResult:
        ...

