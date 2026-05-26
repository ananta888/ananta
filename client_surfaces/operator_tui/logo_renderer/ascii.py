from __future__ import annotations

import os

from client_surfaces.operator_tui.logo_renderer.base import LogoFrame, LogoRendererProbe
from client_surfaces.operator_tui.logo_renderer.frame import frame_from_svg

_DEFAULT_SVG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "ananta.svg"))
_ASCII_CHARS = " .:-=+*#%@"


def _to_ascii_lines(frame, *, width_cells: int, height_cells: int) -> tuple[str, ...]:
    if frame.is_empty:
        return ()
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - optional dependency path
        return ()
    image = Image.frombytes("RGBA", (frame.width_px, frame.height_px), frame.rgba)
    target_w = max(1, int(width_cells))
    target_h = max(1, int(height_cells))
    gray = image.convert("L").resize((target_w, target_h))
    alpha = image.convert("RGBA").resize((target_w, target_h)).split()[3]
    luma = list(gray.get_flattened_data() if hasattr(gray, "get_flattened_data") else gray.getdata())
    alpha_vals = list(alpha.get_flattened_data() if hasattr(alpha, "get_flattened_data") else alpha.getdata())
    lines: list[str] = []
    for y in range(target_h):
        row_chars: list[str] = []
        for x in range(target_w):
            idx = y * target_w + x
            if alpha_vals[idx] < 30:
                row_chars.append(" ")
                continue
            shade = luma[idx]
            char_idx = int((shade / 255.0) * (len(_ASCII_CHARS) - 1))
            row_chars.append(_ASCII_CHARS[char_idx])
        lines.append("".join(row_chars))
    return tuple(lines)


class AsciiRenderer:
    name = "ascii"
    quality_rank = 10
    kind = "text_lines"

    def detect(self, probe: LogoRendererProbe) -> bool:
        return bool(probe.is_tty)

    def supports_animation(self) -> bool:
        return False

    def supports_truecolor(self) -> bool:
        return False

    def get_capabilities(self) -> dict[str, str | int | float | bool]:
        return {
            "renderer": self.name,
            "protocol": "ascii",
            "supports_animation": False,
            "supports_truecolor": False,
            "ascii_fallback": True,
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
        frame = frame_from_svg(
            svg_path=_DEFAULT_SVG,
            width_px=max(2, int(width_cells)),
            height_px=max(2, int(height_cells * 2)),
            metadata={"renderer": self.name},
        )
        lines = _to_ascii_lines(frame, width_cells=width_cells, height_cells=height_cells)
        return LogoFrame(kind="text_lines", text_lines=lines, metadata={"renderer": self.name, "ok": bool(lines), "ascii_fallback": True})

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
