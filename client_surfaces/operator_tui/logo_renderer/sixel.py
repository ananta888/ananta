from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from client_surfaces.operator_tui.logo_renderer.base import LogoFrame, LogoRendererProbe
from client_surfaces.operator_tui.logo_renderer.detect import detect_sixel_support
from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame, frame_from_svg

_DEFAULT_SVG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "ananta.svg"))


class SixelRenderer:
    name = "sixel"
    quality_rank = 200
    kind = "stream_sequences"

    def __init__(self) -> None:
        self._tool = shutil.which("img2sixel")

    def detect(self, probe: LogoRendererProbe) -> bool:
        if not probe.is_tty:
            return False
        if self._tool is None:
            return False
        return detect_sixel_support(probe.env)

    def supports_animation(self) -> bool:
        return True

    def supports_truecolor(self) -> bool:
        return True

    def get_capabilities(self) -> dict[str, str | int | float | bool]:
        return {
            "renderer": self.name,
            "protocol": "sixel",
            "supports_animation": True,
            "supports_truecolor": True,
            "img2sixel_available": bool(self._tool),
        }

    def clear_region(self, *, x: int, y: int, width: int, height: int, writer=None) -> str:
        sequence = f"\x1b7\x1b[{max(1, y)};{max(1, x)}H\x1b[0J\x1b8"
        if writer is not None:
            writer.write(sequence)
        return sequence

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
        if frame.is_empty or self._tool is None:
            return LogoFrame(
                kind="stream_sequences",
                sequence="",
                metadata={"renderer": self.name, "ok": False, "error": "img2sixel_missing_or_rasterize_failed"},
            )

        payload = self._encode_sixel_from_frame(frame)
        if not payload:
            return LogoFrame(
                kind="stream_sequences",
                sequence="",
                metadata={"renderer": self.name, "ok": False, "error": "sixel_encode_failed"},
            )

        sequence = f"\x1b7\x1b[1;1H{payload}\x1b[{height_cells + 1};1H\x1b8"
        if writer is not None:
            writer.write(sequence)
        return LogoFrame(
            kind="stream_sequences",
            sequence=sequence,
            metadata={"renderer": self.name, "ok": True, "x": 1, "y": 1, "height_cells": int(height_cells)},
        )

    def render_sequence(
        self,
        *,
        width_cells: int,
        height_cells: int,
        frame_count: int,
        fps: int,
        writer=None,
    ) -> list[LogoFrame]:
        count = max(1, min(24, int(frame_count)))
        frames = [self.render_frame(width_cells=width_cells, height_cells=height_cells, t=i / max(1, fps), writer=writer) for i in range(count)]
        return frames

    def render_pixel_frame(self, frame: PixelFrame) -> str:
        return self._encode_sixel_from_frame(frame)

    def _encode_sixel_from_frame(self, frame: PixelFrame) -> str:
        png_bytes = frame.to_png_bytes()
        if not png_bytes:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = handle.name
            handle.write(png_bytes)
        try:
            output = subprocess.run([self._tool, path], check=True, capture_output=True)
            return output.stdout.decode("utf-8", errors="ignore")
        except subprocess.CalledProcessError:
            return ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
