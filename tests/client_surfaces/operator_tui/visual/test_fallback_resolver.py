from __future__ import annotations

from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.runtime.config import VisualViewportConfig
from client_surfaces.operator_tui.visual.runtime.fallback_resolver import resolve_renderer_adapter_pair


def test_fallback_resolver_prefers_configured_pair_when_supported() -> None:
    cfg = VisualViewportConfig(default_renderer="cpu_raster", default_output_adapter="kitty")
    result = resolve_renderer_adapter_pair(
        config=cfg,
        capabilities=TerminalVisualCapabilities(ansi=True, kitty_graphics=True),
        available_renderers={"cpu_raster", "ansi_blocks"},
        available_adapters={"kitty", "ansi"},
    )
    assert (result.renderer, result.adapter) == ("cpu_raster", "kitty")
    assert any("candidate accepted" in row for row in result.diagnostics)


def test_fallback_resolver_skips_opengl_when_not_available() -> None:
    cfg = VisualViewportConfig(
        default_renderer="opengl_offscreen_optional",
        default_output_adapter="kitty",
    )
    result = resolve_renderer_adapter_pair(
        config=cfg,
        capabilities=TerminalVisualCapabilities(ansi=True, kitty_graphics=True, opengl_offscreen=False),
        available_renderers={"opengl_offscreen_optional", "ansi_blocks"},
        available_adapters={"kitty", "ansi"},
    )
    assert (result.renderer, result.adapter) == ("ansi_blocks", "ansi")
    assert any("opengl unavailable" in row for row in result.diagnostics)


def test_fallback_resolver_guarantees_ansi_fallback() -> None:
    cfg = VisualViewportConfig(default_renderer="cpu_raster", default_output_adapter="sixel")
    result = resolve_renderer_adapter_pair(
        config=cfg,
        capabilities=TerminalVisualCapabilities(ansi=True, sixel=False, kitty_graphics=False),
        available_renderers={"ansi_blocks"},
        available_adapters={"ansi"},
    )
    assert (result.renderer, result.adapter) == ("ansi_blocks", "ansi")
    assert any("renderer unavailable" in row or "adapter unsupported" in row for row in result.diagnostics)

