from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext, DrawResult
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


@dataclass
class KittyOutputAdapter:
    adapter_id: str = "kitty"
    enabled: bool = True
    supported: bool = False
    image_id: int = 777
    last_error: str = ""
    _last_frame_metadata: dict[str, Any] = field(default_factory=dict)

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "supported": self.supported,
            "last_error": self.last_error,
            "image_id": self.image_id,
        }

    def clear(self, stream: Any) -> None:
        # Clear stale image placement for this adapter image id.
        stream.write(f"\x1b_Ga=d,d=i,i={self.image_id}\x1b\\")

    def draw(self, frame: RenderFrame, *, region: ViewportRegion, stream: Any, context: DrawContext) -> DrawResult:
        _ = context
        self._last_frame_metadata = dict(frame.metadata or {})
        if not self.enabled:
            self.last_error = "adapter disabled by config"
            return DrawResult(drawn=False, reason="disabled", metadata=self.status())
        if not self.supported:
            self.last_error = "kitty unsupported"
            return DrawResult(drawn=False, reason="unsupported", metadata=self.status())
        if frame.frame_type != "raster":
            self.last_error = "non-raster frame"
            return DrawResult(drawn=False, reason="unsupported_frame_type", metadata=self.status())
        payload = frame.payload if isinstance(frame.payload, (bytes, bytearray)) else b""
        if not payload:
            self.last_error = "empty raster payload"
            return DrawResult(drawn=False, reason="empty_payload", metadata=self.status())
        encoded = base64.b64encode(bytes(payload)).decode("ascii")
        # Place image at viewport top-left cell.
        stream.write(f"\x1b[{region.y + 1};{region.x + 1}H")
        stream.write(
            f"\x1b_Ga=T,f=100,i={self.image_id},c={region.columns},r={region.rows},m=0;{encoded}\x1b\\"
        )
        self.last_error = ""
        return DrawResult(drawn=True, reason="ok", metadata=self.status())

    def last_frame_metadata(self) -> dict[str, Any]:
        return dict(self._last_frame_metadata)

