from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.logo_renderer.frame_cache import rasterize_svg_rgba

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency path
    Image = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class PixelFrame:
    width_px: int
    height_px: int
    rgba: bytes
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)

    @classmethod
    def from_image(
        cls,
        image: Any,
        *,
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> PixelFrame:
        if Image is None or image is None:
            return cls(width_px=0, height_px=0, rgba=b"", metadata=dict(metadata or {}))
        rgba_img = image.convert("RGBA")
        width, height = rgba_img.size
        return cls(
            width_px=int(width),
            height_px=int(height),
            rgba=bytes(rgba_img.tobytes()),
            metadata=dict(metadata or {}),
        )

    @property
    def is_empty(self) -> bool:
        return self.width_px <= 0 or self.height_px <= 0 or not self.rgba

    def to_png_bytes(self) -> bytes:
        if self.is_empty or Image is None:
            return b""
        image = Image.frombytes("RGBA", (self.width_px, self.height_px), self.rgba)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def cache_key(self) -> str:
        h = hashlib.sha256()
        h.update(str(self.width_px).encode("utf-8"))
        h.update(b"x")
        h.update(str(self.height_px).encode("utf-8"))
        h.update(b":")
        h.update(self.rgba)
        return h.hexdigest()


def frame_from_svg(
    *,
    svg_path: str,
    width_px: int,
    height_px: int,
    metadata: dict[str, str | int | float | bool] | None = None,
) -> PixelFrame:
    image = rasterize_svg_rgba(
        svg_path=svg_path,
        width_px=max(2, int(width_px)),
        height_px=max(2, int(height_px)),
    )
    return PixelFrame.from_image(image, metadata=metadata)
