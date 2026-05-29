"""Terminal capability detection for image output protocols (MIMG-012 / MDP-009).

Detects Kitty graphics protocol and Sixel support separately from
Mermaid renderer and raster renderer availability, so diagnostics
can clearly distinguish which layer caused degraded output.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities


@dataclass
class MermaidRendererCapabilities:
    """Whether image-generating Mermaid backends are available."""
    mmdc_available: bool = False
    mmdc_path: str = ""
    playwright_available: bool = False
    fallback_only: bool = True  # True when no real image renderer found

    def any_image_renderer(self) -> bool:
        return self.mmdc_available or self.playwright_available


@dataclass
class RasterRendererCapabilities:
    """Whether raster (PIL/cairosvg) rendering deps are available."""
    pillow_available: bool = False
    cairosvg_available: bool = False

    def any_available(self) -> bool:
        return self.pillow_available


@dataclass
class ImageOutputCapabilities:
    """Full image-output capability report (MIMG-012)."""
    # Mermaid renderer layer
    mermaid_renderer: MermaidRendererCapabilities = field(default_factory=MermaidRendererCapabilities)
    # Raster renderer layer
    raster_renderer: RasterRendererCapabilities = field(default_factory=RasterRendererCapabilities)
    # Terminal image protocol layer
    kitty_supported: bool = False
    sixel_supported: bool = False
    # Summary
    ansi_fallback: bool = True  # always available

    def can_show_mermaid_image(self) -> bool:
        return (
            self.mermaid_renderer.any_image_renderer()
            and self.raster_renderer.any_available()
            and (self.kitty_supported or self.sixel_supported)
        )

    def degraded_reasons(self) -> list[str]:
        reasons: list[str] = []
        if not self.mermaid_renderer.any_image_renderer():
            reasons.append("mermaid_renderer_unavailable: install mmdc or playwright")
        if not self.raster_renderer.any_available():
            reasons.append("raster_renderer_unavailable: install Pillow")
        if not self.kitty_supported and not self.sixel_supported:
            reasons.append("terminal_image_protocol_unsupported: use Kitty terminal or set SIXEL_SUPPORTED=1")
        return reasons

    def as_dict(self) -> dict[str, Any]:
        return {
            "mermaid_renderer_available": self.mermaid_renderer.any_image_renderer(),
            "mermaid_mmdc": self.mermaid_renderer.mmdc_available,
            "mermaid_playwright": self.mermaid_renderer.playwright_available,
            "mermaid_fallback_only": self.mermaid_renderer.fallback_only,
            "raster_renderer_available": self.raster_renderer.any_available(),
            "pillow_available": self.raster_renderer.pillow_available,
            "cairosvg_available": self.raster_renderer.cairosvg_available,
            "kitty_supported": self.kitty_supported,
            "sixel_supported": self.sixel_supported,
            "can_show_mermaid_image": self.can_show_mermaid_image(),
            "ansi_fallback": self.ansi_fallback,
            "degraded_reasons": self.degraded_reasons(),
        }


def detect_mermaid_renderer() -> MermaidRendererCapabilities:
    mmdc_path = shutil.which("mmdc") or ""
    mmdc_ok = bool(mmdc_path)
    try:
        import importlib.util
        pw_ok = importlib.util.find_spec("playwright") is not None
    except Exception:
        pw_ok = False
    return MermaidRendererCapabilities(
        mmdc_available=mmdc_ok,
        mmdc_path=mmdc_path,
        playwright_available=pw_ok,
        fallback_only=not (mmdc_ok or pw_ok),
    )


def detect_raster_renderer() -> RasterRendererCapabilities:
    try:
        from PIL import Image  # type: ignore  # noqa: F401
        pil_ok = True
    except Exception:
        pil_ok = False
    try:
        import cairosvg  # type: ignore  # noqa: F401
        cairo_ok = True
    except Exception:
        cairo_ok = False
    return RasterRendererCapabilities(pillow_available=pil_ok, cairosvg_available=cairo_ok)


def detect_terminal_image_protocols() -> tuple[bool, bool]:
    """Returns (kitty_supported, sixel_supported)."""
    term = os.environ.get("TERM", "").lower()
    term_prog = os.environ.get("TERM_PROGRAM", "").lower()
    colorterm = os.environ.get("COLORTERM", "").lower()
    kitty = (
        "kitty" in term
        or "kitty" in term_prog
        or os.environ.get("KITTY_WINDOW_ID") is not None
    )
    sixel = os.environ.get("SIXEL_SUPPORTED", "").lower() in {"1", "true", "yes"}
    return kitty, sixel


def detect_image_output_capabilities() -> ImageOutputCapabilities:
    """Full detection: Mermaid renderer + raster renderer + terminal protocol."""
    mermaid = detect_mermaid_renderer()
    raster = detect_raster_renderer()
    kitty, sixel = detect_terminal_image_protocols()
    return ImageOutputCapabilities(
        mermaid_renderer=mermaid,
        raster_renderer=raster,
        kitty_supported=kitty,
        sixel_supported=sixel,
    )


def terminal_capabilities_from_env() -> TerminalVisualCapabilities:
    """Build TerminalVisualCapabilities using env-based detection."""
    kitty, sixel = detect_terminal_image_protocols()
    return TerminalVisualCapabilities(
        ansi=True,
        sixel=sixel,
        kitty_graphics=kitty,
        opengl_offscreen=False,
    )
