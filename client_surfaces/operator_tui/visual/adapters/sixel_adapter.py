from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext, DrawResult
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

_DCS = "\x1bP"
_ST = "\x1b\\"


def _png_to_rgba(payload: bytes | bytearray) -> tuple[bytes, int, int] | None:
    try:
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(bytes(payload))).convert("RGBA")
        return img.tobytes(), img.width, img.height
    except Exception:
        return None


def _rgba_dict_to_raw(payload: dict[str, Any]) -> tuple[bytes, int, int] | None:
    raw = payload.get("pixels")
    w = int(payload.get("width") or 0)
    h = int(payload.get("height") or 0)
    if isinstance(raw, (bytes, bytearray)) and w > 0 and h > 0:
        return bytes(raw), w, h
    return None


def _encode_sixel(rgba: bytes, width: int, height: int) -> str | None:
    """Minimal but real Sixel encoder using Pillow quantization (MIMG-011 / MDP-013)."""
    try:
        from PIL import Image  # type: ignore
        img = Image.frombytes("RGBA", (width, height), rgba).convert("RGB")
        q_img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)  # type: ignore[attr-defined]
        palette_flat = q_img.getpalette() or []
        palette = [
            (palette_flat[i * 3], palette_flat[i * 3 + 1], palette_flat[i * 3 + 2])
            for i in range(min(256, len(palette_flat) // 3))
        ]
        pixels = list(q_img.getdata())

        out: list[str] = []
        out.append(f"{_DCS}0;0;0q")
        out.append(f'"1;1;{width};{height}')
        for ci, (r, g, b) in enumerate(palette):
            out.append(f"#{ci};2;{r * 100 // 255};{g * 100 // 255};{b * 100 // 255}")

        num_bands = (height + 5) // 6
        for band in range(num_bands):
            y0 = band * 6
            color_data: dict[int, list[int]] = {}
            for x in range(width):
                for dy in range(6):
                    y = y0 + dy
                    if y >= height:
                        break
                    ci = pixels[y * width + x]
                    if ci not in color_data:
                        color_data[ci] = [0] * width
                    color_data[ci][x] |= (1 << dy)

            first = True
            for ci, masks in sorted(color_data.items()):
                if not first:
                    out.append("$")
                first = False
                out.append(f"#{ci}")
                run_char: str | None = None
                run_count = 0
                for mask in masks:
                    ch = chr(63 + mask)
                    if ch == run_char:
                        run_count += 1
                    else:
                        if run_char is not None:
                            out.append(f"!{run_count}{run_char}" if run_count > 3 else run_char * run_count)
                        run_char = ch
                        run_count = 1
                if run_char is not None:
                    out.append(f"!{run_count}{run_char}" if run_count > 3 else run_char * run_count)
            out.append("-")

        out.append(_ST)
        return "".join(out)
    except Exception:
        return None


@dataclass
class SixelOutputAdapter:
    adapter_id: str = "sixel"
    enabled: bool = True
    supported: bool = False
    last_error: str = ""
    _last_frame_metadata: dict[str, Any] = field(default_factory=dict)

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "supported": self.supported,
            "last_error": self.last_error,
        }

    def draw(self, frame: RenderFrame, *, region: ViewportRegion, stream: Any, context: DrawContext) -> DrawResult:
        _ = context
        _ = region
        self._last_frame_metadata = dict(frame.metadata or {})

        if not self.enabled:
            self.last_error = "adapter disabled by config"
            return DrawResult(drawn=False, reason="disabled", metadata=self.status())

        if not self.supported:
            self.last_error = "sixel_protocol_unsupported"
            return DrawResult(drawn=False, reason="sixel_protocol_unsupported", metadata=self.status())

        if frame.frame_type != "raster":
            self.last_error = "non-raster frame"
            return DrawResult(drawn=False, reason="unsupported_frame_type", metadata=self.status())

        payload = frame.payload
        rgba_result: tuple[bytes, int, int] | None = None
        if isinstance(payload, (bytes, bytearray)):
            rgba_result = _png_to_rgba(payload)
        elif isinstance(payload, dict):
            rgba_result = _rgba_dict_to_raw(payload)

        if rgba_result is None:
            self.last_error = "sixel_encoder_unavailable: cannot decode payload"
            return DrawResult(drawn=False, reason="sixel_encoder_unavailable", metadata=self.status())

        rgba_bytes, w, h = rgba_result
        sixel_str = _encode_sixel(rgba_bytes, w, h)
        if sixel_str is None:
            self.last_error = "sixel_encoder_unavailable: Pillow not installed or encoding failed"
            return DrawResult(drawn=False, reason="sixel_encoder_unavailable", metadata=self.status())

        stream.write(f"\x1b[{region.y + 1};{region.x + 1}H")
        stream.write(sixel_str)
        self.last_error = ""
        return DrawResult(
            drawn=True,
            reason="ok",
            metadata={**self.status(), "sixel_chars": len(sixel_str), "image_size": f"{w}x{h}"},
        )

    def last_frame_metadata(self) -> dict[str, Any]:
        return dict(self._last_frame_metadata)
