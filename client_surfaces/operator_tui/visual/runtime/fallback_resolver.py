from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.runtime.config import FallbackPair, VisualViewportConfig


@dataclass(frozen=True)
class FallbackResolution:
    renderer: str
    adapter: str
    diagnostics: tuple[str, ...]


def _supports_adapter(adapter: str, capabilities: TerminalVisualCapabilities) -> bool:
    if adapter == "ansi":
        return capabilities.ansi
    if adapter == "sixel":
        return capabilities.sixel
    if adapter == "kitty":
        return capabilities.kitty_graphics
    return True


def _is_opengl_renderer(renderer: str) -> bool:
    normalized = renderer.lower()
    return "opengl" in normalized


def _image_capable_candidates(
    capabilities: TerminalVisualCapabilities,
) -> list[FallbackPair]:
    """Preferred renderer/adapter pairs for Mermaid image mode (MIMG-009 / MDP-014)."""
    pairs: list[FallbackPair] = []
    if capabilities.kitty_graphics:
        pairs.append(FallbackPair(renderer="cpu_raster", adapter="kitty"))
        pairs.append(FallbackPair(renderer="svg_raster_optional", adapter="kitty"))
    if capabilities.sixel:
        pairs.append(FallbackPair(renderer="cpu_raster", adapter="sixel"))
        pairs.append(FallbackPair(renderer="svg_raster_optional", adapter="sixel"))
    return pairs


def resolve_renderer_adapter_pair(
    *,
    config: VisualViewportConfig,
    capabilities: TerminalVisualCapabilities,
    available_renderers: set[str],
    available_adapters: set[str],
    excluded_pairs: set[tuple[str, str]] | None = None,
    prefer_image_mode: bool = False,
) -> FallbackResolution:
    diagnostics: list[str] = []
    blocked = set(excluded_pairs or set())

    # When Mermaid image mode is preferred and image protocols are available,
    # prepend image-capable pairs before the config chain (MDP-014 / MIMG-009).
    preamble: list[FallbackPair] = []
    if prefer_image_mode:
        preamble = _image_capable_candidates(capabilities)
        if preamble:
            diagnostics.append("mermaid_image_mode: prepending image-capable candidates")
        else:
            diagnostics.append("mermaid_image_mode: no image protocol supported, using config chain")

    candidates: list[FallbackPair] = [
        *preamble,
        FallbackPair(renderer=config.default_renderer, adapter=config.default_output_adapter),
        *list(config.fallback_chain),
    ]
    if not any(pair.renderer == "ansi_blocks" and pair.adapter == "ansi" for pair in candidates):
        candidates.append(FallbackPair(renderer="ansi_blocks", adapter="ansi"))

    for pair in candidates:
        renderer = pair.renderer.strip()
        adapter = pair.adapter.strip()
        if (renderer, adapter) in blocked:
            diagnostics.append(f"skip {renderer}+{adapter}: excluded")
            continue
        if renderer not in available_renderers:
            diagnostics.append(f"skip {renderer}+{adapter}: renderer unavailable")
            continue
        if adapter not in available_adapters:
            diagnostics.append(f"skip {renderer}+{adapter}: adapter unavailable")
            continue
        if _is_opengl_renderer(renderer) and not capabilities.opengl_offscreen:
            diagnostics.append(f"skip {renderer}+{adapter}: opengl unavailable")
            continue
        if not _supports_adapter(adapter, capabilities):
            diagnostics.append(f"skip {renderer}+{adapter}: adapter unsupported by terminal")
            continue
        diagnostics.append(f"select {renderer}+{adapter}: candidate accepted")
        return FallbackResolution(renderer=renderer, adapter=adapter, diagnostics=tuple(diagnostics))

    raise RuntimeError("no viable renderer/adapter pair; ensure ansi_blocks+ansi is registered")
