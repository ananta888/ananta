from __future__ import annotations

import os

from client_surfaces.operator_tui.logo_renderer.ansi_halfblock import render_halfblock_image
from client_surfaces.operator_tui.logo_renderer.base import LogoFrame, LogoRendererProbe
from client_surfaces.operator_tui.logo_renderer.frame import frame_from_svg

_DEFAULT_SVG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "ananta.svg"))


class HalfblockRenderer:
    name = "halfblock"
    quality_rank = 120
    kind = "text_lines"

    def detect(self, probe: LogoRendererProbe) -> bool:
        return bool(probe.is_tty)

    def supports_animation(self) -> bool:
        return False

    def supports_truecolor(self) -> bool:
        return True

    def get_capabilities(self) -> dict[str, str | int | float | bool]:
        return {
            "renderer": self.name,
            "protocol": "unicode_halfblock",
            "supports_animation": False,
            "supports_truecolor": True,
            "fallback_renderer": True,
        }

    def clear_region(self, *, x: int, y: int, width: int, height: int, writer=None) -> str:
        lines = [" " * max(0, int(width)) for _ in range(max(0, int(height)))]
        payload = "\n".join(lines)
        if writer is not None and payload:
            writer.write(payload)
        return payload

    def render_frame(
        self,
        *,
        width_cells: int,
        height_cells: int,
        t: float = 0.0,
        writer=None,
    ) -> LogoFrame:
        no_color = os.environ.get("NO_COLOR", "").strip().lower() in {"1", "true", "yes", "on"}
        frame = frame_from_svg(
            svg_path=_DEFAULT_SVG,
            width_px=max(2, int(width_cells)),
            height_px=max(2, int(height_cells * 2)),
            metadata={"renderer": self.name},
        )
        if frame.is_empty:
            return LogoFrame(kind="text_lines", text_lines=(), metadata={"renderer": self.name, "ok": False})
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - optional dependency path
            return LogoFrame(kind="text_lines", text_lines=(), metadata={"renderer": self.name, "ok": False})
        image = Image.frombytes("RGBA", (frame.width_px, frame.height_px), frame.rgba)
        lines = tuple(render_halfblock_image(image, no_color=no_color))
        return LogoFrame(kind="text_lines", text_lines=lines, metadata={"renderer": self.name, "ok": True})

    def render_sequence(
        self,
        *,
        width_cells: int,
        height_cells: int,
        frame_count: int,
        fps: int,
        writer=None,
    ) -> list[LogoFrame]:
        return [self.render_frame(width_cells=width_cells, height_cells=height_cells, t=0.0, writer=writer)]
