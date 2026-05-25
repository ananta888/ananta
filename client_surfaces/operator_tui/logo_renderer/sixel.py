from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from client_surfaces.operator_tui.logo_renderer.base import LogoFrame, LogoRendererProbe
from client_surfaces.operator_tui.logo_renderer.detect import detect_sixel_support
from client_surfaces.operator_tui.logo_renderer.frame_cache import encode_png_bytes, rasterize_svg_rgba

_DEFAULT_SVG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "ananta.svg"))


class SixelRenderer:
    name = "sixel"
    quality_rank = 200
    supports_animation = True
    kind = "stream_sequences"

    def __init__(self) -> None:
        self._tool = shutil.which("img2sixel")

    def detect(self, probe: LogoRendererProbe) -> bool:
        if not probe.is_tty:
            return False
        if self._tool is None:
            return False
        return detect_sixel_support(probe.env)

    def render_frame(
        self,
        *,
        width_cells: int,
        height_cells: int,
        t: float = 0.0,
        writer=None,
    ) -> LogoFrame:
        image = rasterize_svg_rgba(
            svg_path=_DEFAULT_SVG,
            width_px=max(2, int(width_cells)),
            height_px=max(2, int(height_cells * 2)),
        )
        if image is None or self._tool is None:
            return LogoFrame(kind="stream_sequences", sequence="", metadata={"renderer": self.name, "ok": False})

        payload = self._encode_sixel_from_image(image)
        if not payload:
            return LogoFrame(kind="stream_sequences", sequence="", metadata={"renderer": self.name, "ok": False})

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

    def _encode_sixel_from_image(self, image) -> str:
        png_bytes = encode_png_bytes(image)
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
