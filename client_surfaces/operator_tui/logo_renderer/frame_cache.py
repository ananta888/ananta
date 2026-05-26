from __future__ import annotations

import hashlib
import io
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.logo_renderer.ansi_halfblock import render_halfblock_image

try:
    from PIL import Image, ImageEnhance
except ImportError:  # pragma: no cover - optional dependency path
    Image = None  # type: ignore[assignment]
    ImageEnhance = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class _CacheKey:
    svg_path: str
    width_cells: int
    height_cells: int
    renderer_mode: str
    preset: str
    frame_count: int
    no_color: bool


@dataclass(slots=True)
class _CacheEntry:
    mtime_ns: int
    digest: str
    frames: list[list[str]]


class LogoFrameCache:
    def __init__(self) -> None:
        self._entries: dict[_CacheKey, _CacheEntry] = {}

    def get_ansi_frames(
        self,
        *,
        svg_path: str,
        width_cells: int,
        height_cells: int,
        renderer_mode: str = "ansi",
        preset: str = "static",
        frame_count: int = 1,
        no_color: bool = False,
    ) -> list[list[str]]:
        normalized_path = os.path.abspath(svg_path)
        safe_count = max(1, min(24, int(frame_count)))
        key = _CacheKey(
            svg_path=normalized_path,
            width_cells=max(1, int(width_cells)),
            height_cells=max(1, int(height_cells)),
            renderer_mode=renderer_mode.strip().lower() or "ansi",
            preset=(preset.strip().lower() or "static"),
            frame_count=safe_count,
            no_color=bool(no_color),
        )
        fingerprint = self._fingerprint_svg(normalized_path)
        existing = self._entries.get(key)
        if existing is not None and existing.mtime_ns == fingerprint[0] and existing.digest == fingerprint[1]:
            return existing.frames

        frames = self._build_ansi_frames(
            svg_path=normalized_path,
            width_cells=key.width_cells,
            height_cells=key.height_cells,
            preset=key.preset,
            frame_count=key.frame_count,
            no_color=key.no_color,
        )
        self._entries[key] = _CacheEntry(mtime_ns=fingerprint[0], digest=fingerprint[1], frames=frames)
        return frames

    def _fingerprint_svg(self, svg_path: str) -> tuple[int, str]:
        stat = os.stat(svg_path)
        with open(svg_path, "rb") as handle:
            payload = handle.read()
        digest = hashlib.sha256(payload).hexdigest()
        return int(stat.st_mtime_ns), digest

    def _build_ansi_frames(
        self,
        *,
        svg_path: str,
        width_cells: int,
        height_cells: int,
        preset: str,
        frame_count: int,
        no_color: bool,
    ) -> list[list[str]]:
        if Image is None:
            return []
        base = _rasterize_svg_rgba(svg_path=svg_path, width_px=max(2, width_cells), height_px=max(2, height_cells * 2))
        if base is None:
            return []

        frames: list[list[str]] = []
        if preset == "static" or frame_count <= 1:
            lines = render_halfblock_image(base, no_color=no_color)
            return [lines]

        for idx in range(frame_count):
            frame = _apply_preset(base, preset=preset, index=idx, total=frame_count)
            lines = render_halfblock_image(frame, no_color=no_color)
            frames.append(lines)
        return frames


def _apply_preset(image: Any, *, preset: str, index: int, total: int) -> Any:
    if ImageEnhance is None:
        return image
    preset_key = (preset or "pulse").strip().lower()
    if preset_key not in {"pulse", "shimmer", "rotate_hint"}:
        preset_key = "pulse"

    phase = (index / max(1, total)) * 6.283185307179586
    if preset_key == "pulse":
        factor = 0.90 + (0.18 * ((1.0 + math.sin(phase)) / 2.0))
        return ImageEnhance.Brightness(image).enhance(factor)

    # shimmer/rotate_hint: mild x-gradient brightness shift based on phase
    width, height = image.size
    rgba = image.convert("RGBA")
    pix = rgba.load()
    if preset_key == "rotate_hint":
        shift = int((index % max(1, total)) * max(1, width // max(1, total)))
    else:
        shift = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = pix[x, y]
            wave_x = x + shift
            wave = 0.92 + (0.16 * ((1.0 + math.sin((wave_x * 0.25) + phase)) / 2.0))
            pix[x, y] = (
                max(0, min(255, int(r * wave))),
                max(0, min(255, int(g * wave))),
                max(0, min(255, int(b * wave))),
                a,
            )
    return rgba


def _quality_oversampling_factor(quality: str) -> int:
    value = (quality or "medium").strip().lower()
    if value == "high":
        return 4
    if value == "ultra":
        return 6
    if value == "low":
        return 1
    return 2


def _svg_to_png(svg_path: str, png_path: str, *, width_px: int, height_px: int) -> bool:
    try:
        from cairosvg import svg2png

        svg2png(url=svg_path, write_to=png_path, output_width=width_px, output_height=height_px)
        return True
    except ImportError:
        pass
    except Exception:
        pass

    try:
        subprocess.run(
            [
                "rsvg-convert",
                "--width",
                str(width_px),
                "--height",
                str(height_px),
                "-o",
                png_path,
                svg_path,
            ],
            check=True,
            capture_output=True,
        )
        return True
    except FileNotFoundError:
        pass
    except subprocess.CalledProcessError:
        pass

    try:
        subprocess.run(
            [
                "inkscape",
                "--export-width",
                str(width_px),
                "--export-height",
                str(height_px),
                "--export-filename",
                png_path,
                svg_path,
            ],
            check=True,
            capture_output=True,
        )
        return True
    except FileNotFoundError:
        return False
    except subprocess.CalledProcessError:
        return False


def rasterize_svg_rgba(*, svg_path: str, width_px: int, height_px: int) -> Any | None:
    if Image is None:
        return None
    if not os.path.isfile(svg_path):
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        png_path = handle.name
    try:
        if not _svg_to_png(svg_path, png_path, width_px=width_px, height_px=height_px):
            return None
        return Image.open(png_path).convert("RGBA")
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass


def encode_png_bytes(image: Any) -> bytes:
    if Image is None:
        return b""
    if not hasattr(image, "save"):
        return b""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _rasterize_svg_rgba(*, svg_path: str, width_px: int, height_px: int) -> Any | None:
    quality = os.environ.get("ANANTA_TUI_LOGO_QUALITY", "medium")
    factor_raw = os.environ.get("ANANTA_TUI_LOGO_OVERSAMPLING", "").strip()
    try:
        factor = int(factor_raw) if factor_raw else _quality_oversampling_factor(quality)
    except ValueError:
        factor = _quality_oversampling_factor(quality)
    factor = max(1, min(8, factor))

    target_w = max(2, int(width_px))
    target_h = max(2, int(height_px))
    render_w = max(2, target_w * factor)
    render_h = max(2, target_h * factor)

    rendered = rasterize_svg_rgba(svg_path=svg_path, width_px=render_w, height_px=render_h)
    if rendered is None:
        return None
    if factor <= 1 or Image is None:
        return rendered
    lanczos = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
    return rendered.resize((target_w, target_h), resample=lanczos)
