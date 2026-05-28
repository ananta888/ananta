from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


@dataclass(frozen=True)
class ViewportState:
    region: ViewportRegion
    is_too_small_for_image_protocols: bool
    reason: str = ""

