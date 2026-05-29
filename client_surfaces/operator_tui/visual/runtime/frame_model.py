from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RenderScene:
    """A view's logical output: typed nodes plus metadata.

    Node kinds
    ----------
    label        : text line for ANSI rendering
                   keys: kind, text, x, y
    error        : error text
                   keys: kind, text, x, y
    territory    : strategy-map territory
                   keys: kind, id, owner, point
    diagram_image: embedded raster/SVG diagram (MIMG-002)
                   keys: kind, diagram_id, image_format, image_data,
                         x, y, requested_width, requested_height,
                         alt_text, fallback_text,
                         render_duration_ms, cache_hit
                   image_format: 'png' | 'svg' | 'svg+xml'
                   image_data  : bytes  (raw PNG or SVG)
                   Views emit diagram_image nodes; renderers/adapters
                   decide whether to draw raster images or fallback text.
                   Terminal escape sequences must not appear in nodes.
    """

    scene_type: str
    nodes: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderFrame:
    """Output of a renderer: ready-to-send bytes or ANSI lines.

    frame_type    : 'ansi' | 'raster'
    payload       : list[str] for ansi; bytes (PNG) or dict for raster
    mime_or_format: 'text/plain' | 'image/png' | 'application/x-rgba'
    metadata keys : renderer, scene_type, generation_ms, animated,
                    output_bytes, degraded, diagram_node_count, etc.
    """

    frame_type: str
    width: int
    height: int
    payload: Any
    mime_or_format: str
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame width/height must be positive")
