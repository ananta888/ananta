from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion
from client_surfaces.operator_tui.visual.viewport.viewport_state import ViewportState


@dataclass(frozen=True)
class ViewportDrawRequest:
    region: ViewportRegion
    payload: dict[str, Any]


class CenterViewport:
    def __init__(self, *, minimum_columns: int = 8, minimum_rows: int = 4) -> None:
        if minimum_columns <= 0 or minimum_rows <= 0:
            raise ValueError("minimum viewport size must be positive")
        self._minimum_columns = minimum_columns
        self._minimum_rows = minimum_rows
        self._state: ViewportState | None = None

    @property
    def state(self) -> ViewportState:
        if self._state is None:
            raise RuntimeError("CenterViewport has no region yet; call resize() first")
        return self._state

    def resize(self, region: ViewportRegion) -> ViewportState:
        too_small = region.columns < self._minimum_columns or region.rows < self._minimum_rows
        reason = ""
        if too_small:
            reason = (
                f"viewport too small: got {region.columns}x{region.rows}, "
                f"need at least {self._minimum_columns}x{self._minimum_rows}"
            )
        self._state = ViewportState(
            region=region,
            is_too_small_for_image_protocols=too_small,
            reason=reason,
        )
        return self._state

    def make_draw_request(self, payload: dict[str, Any] | None = None) -> ViewportDrawRequest:
        snapshot = self.state
        return ViewportDrawRequest(region=snapshot.region, payload=dict(payload or {}))

