from __future__ import annotations

import base64
import os

from client_surfaces.operator_tui.logo_renderer.base import LogoFrame, LogoRendererProbe
from client_surfaces.operator_tui.logo_renderer.detect import detect_kitty_support
from client_surfaces.operator_tui.logo_renderer.frame_cache import encode_png_bytes, rasterize_svg_rgba

_DEFAULT_SVG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "ananta.svg"))


class KittyRenderer:
    name = "kitty"
    quality_rank = 300
    kind = "stream_sequences"

    def __init__(self, *, image_id: int = 1337, placement_id: int = 7331) -> None:
        self._image_id = int(image_id)
        self._placement_id = int(placement_id)

    def detect(self, probe: LogoRendererProbe) -> bool:
        return probe.is_tty and detect_kitty_support(probe.env)

    def supports_animation(self) -> bool:
        return True

    def supports_truecolor(self) -> bool:
        return True

    def get_capabilities(self) -> dict[str, str | int | float | bool]:
        return {
            "renderer": self.name,
            "protocol": "kitty_graphics",
            "supports_animation": True,
            "supports_truecolor": True,
        }

    def clear_region(self, *, x: int, y: int, width: int, height: int, writer=None) -> str:
        sequence = f"\x1b7\x1b[{max(1, y)};{max(1, x)}H\x1b_Ga=d,d=A\x1b\\\x1b8"
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
        image = rasterize_svg_rgba(
            svg_path=_DEFAULT_SVG,
            width_px=max(2, int(width_cells)),
            height_px=max(2, int(height_cells * 2)),
        )
        if image is None:
            return LogoFrame(kind="stream_sequences", sequence="", metadata={"renderer": self.name, "ok": False})

        payload = encode_png_bytes(image)
        if not payload:
            return LogoFrame(kind="stream_sequences", sequence="", metadata={"renderer": self.name, "ok": False})

        delete_old = f"\x1b_Ga=d,d=I,i={self._image_id}\x1b\\"
        draw = self._build_transmit_sequence(payload)
        # save cursor -> move top-left -> draw -> move below header -> restore
        sequence = f"\x1b7\x1b[1;1H{delete_old}{draw}\x1b[{height_cells + 1};1H\x1b8"
        if writer is not None:
            writer.write(sequence)
        return LogoFrame(
            kind="stream_sequences",
            sequence=sequence,
            metadata={
                "renderer": self.name,
                "ok": True,
                "image_id": self._image_id,
                "placement_id": self._placement_id,
            },
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
        return [self.render_frame(width_cells=width_cells, height_cells=height_cells, t=i / max(1, fps), writer=writer) for i in range(count)]

    def _build_transmit_sequence(self, payload: bytes) -> str:
        encoded = base64.b64encode(payload).decode("ascii")
        chunk_size = 4096
        chunks = [encoded[i : i + chunk_size] for i in range(0, len(encoded), chunk_size)]
        segments: list[str] = []
        for idx, chunk in enumerate(chunks):
            more = 1 if idx < len(chunks) - 1 else 0
            segments.append(
                f"\x1b_Ga=T,f=100,t=d,i={self._image_id},p={self._placement_id},q=2,m={more};{chunk}\x1b\\"
            )
        return "".join(segments)
