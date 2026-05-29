"""TGFX-015: Tests for terminal graphics capability detection and resolver selection."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.capabilities.terminal_detector import (
    ImageOutputCapabilities,
    MermaidRendererCapabilities,
    RasterRendererCapabilities,
    detect_image_output_capabilities,
    detect_terminal_image_protocols,
    terminal_capabilities_from_env,
)
from client_surfaces.operator_tui.visual.runtime.config import FallbackPair, VisualViewportConfig
from client_surfaces.operator_tui.visual.runtime.fallback_resolver import (
    resolve_renderer_adapter_pair,
)


# ── Terminal protocol detection ───────────────────────────────────────────────

def test_kitty_detected_from_kitty_window_id():
    env = {"KITTY_WINDOW_ID": "42", "TERM": "xterm-256color", "WT_SESSION": "", "ANANTA_FORCE_KITTY": "", "ANANTA_FORCE_SIXEL": "", "SIXEL_SUPPORTED": ""}
    with patch.dict(os.environ, env, clear=False):
        kitty, _ = detect_terminal_image_protocols()
    assert kitty is True


def test_kitty_detected_from_term():
    env = {"TERM": "xterm-kitty", "KITTY_WINDOW_ID": "", "WT_SESSION": "", "ANANTA_FORCE_KITTY": "", "ANANTA_FORCE_SIXEL": "", "SIXEL_SUPPORTED": ""}
    with patch.dict(os.environ, env, clear=False):
        kitty, _ = detect_terminal_image_protocols()
    assert kitty is True


def test_kitty_force_override():
    env = {"ANANTA_FORCE_KITTY": "1", "TERM": "xterm", "KITTY_WINDOW_ID": "", "WT_SESSION": "", "ANANTA_FORCE_SIXEL": "", "SIXEL_SUPPORTED": ""}
    with patch.dict(os.environ, env, clear=False):
        kitty, _ = detect_terminal_image_protocols()
    assert kitty is True


def test_kitty_force_disable():
    env = {"ANANTA_FORCE_KITTY": "0", "TERM": "xterm-kitty", "KITTY_WINDOW_ID": "1", "WT_SESSION": "", "ANANTA_FORCE_SIXEL": "", "SIXEL_SUPPORTED": ""}
    with patch.dict(os.environ, env, clear=False):
        kitty, _ = detect_terminal_image_protocols()
    assert kitty is False


def test_sixel_from_env_flag():
    env = {"SIXEL_SUPPORTED": "1", "WT_SESSION": "", "ANANTA_FORCE_KITTY": "", "ANANTA_FORCE_SIXEL": ""}
    with patch.dict(os.environ, env, clear=False):
        _, sixel = detect_terminal_image_protocols()
    assert sixel is True


def test_sixel_force_override():
    env = {"ANANTA_FORCE_SIXEL": "1", "SIXEL_SUPPORTED": "0", "WT_SESSION": "", "ANANTA_FORCE_KITTY": ""}
    with patch.dict(os.environ, env, clear=False):
        _, sixel = detect_terminal_image_protocols()
    assert sixel is True


def test_windows_terminal_wt_session_disables_kitty():
    """WT_SESSION present → no Kitty unless forced (TGFX-003)."""
    env = {
        "WT_SESSION": "some-session-id",
        "TERM": "xterm-256color",
        "KITTY_WINDOW_ID": "",
        "ANANTA_FORCE_KITTY": "",
        "ANANTA_FORCE_SIXEL": "",
        "SIXEL_SUPPORTED": "",
    }
    with patch.dict(os.environ, env, clear=False):
        kitty, sixel = detect_terminal_image_protocols()
    assert kitty is False
    assert sixel is False


def test_windows_terminal_kitty_can_still_be_forced():
    env = {
        "WT_SESSION": "some-session-id",
        "ANANTA_FORCE_KITTY": "1",
        "TERM": "xterm-256color",
        "KITTY_WINDOW_ID": "",
        "ANANTA_FORCE_SIXEL": "",
        "SIXEL_SUPPORTED": "",
    }
    with patch.dict(os.environ, env, clear=False):
        kitty, _ = detect_terminal_image_protocols()
    assert kitty is True


def test_plain_ansi_no_graphics():
    env = {"TERM": "xterm-256color", "KITTY_WINDOW_ID": "", "WT_SESSION": "",
           "ANANTA_FORCE_KITTY": "", "ANANTA_FORCE_SIXEL": "", "SIXEL_SUPPORTED": ""}
    with patch.dict(os.environ, env, clear=False):
        kitty, sixel = detect_terminal_image_protocols()
    assert kitty is False
    assert sixel is False


# ── ImageOutputCapabilities ───────────────────────────────────────────────────

def test_can_show_mermaid_image_requires_all_layers():
    caps = ImageOutputCapabilities(
        mermaid_renderer=MermaidRendererCapabilities(mmdc_available=True),
        raster_renderer=RasterRendererCapabilities(pillow_available=True),
        kitty_supported=True,
        sixel_supported=False,
    )
    assert caps.can_show_mermaid_image() is True


def test_cannot_show_mermaid_without_renderer():
    caps = ImageOutputCapabilities(
        mermaid_renderer=MermaidRendererCapabilities(mmdc_available=False),
        raster_renderer=RasterRendererCapabilities(pillow_available=True),
        kitty_supported=True,
    )
    assert caps.can_show_mermaid_image() is False


def test_cannot_show_mermaid_without_protocol():
    caps = ImageOutputCapabilities(
        mermaid_renderer=MermaidRendererCapabilities(mmdc_available=True),
        raster_renderer=RasterRendererCapabilities(pillow_available=True),
        kitty_supported=False,
        sixel_supported=False,
    )
    assert caps.can_show_mermaid_image() is False


def test_degraded_reasons_include_all_missing_layers():
    caps = ImageOutputCapabilities(
        mermaid_renderer=MermaidRendererCapabilities(mmdc_available=False, playwright_available=False),
        raster_renderer=RasterRendererCapabilities(pillow_available=False),
        kitty_supported=False,
        sixel_supported=False,
    )
    reasons = caps.degraded_reasons()
    assert any("mermaid" in r.lower() for r in reasons)
    assert any("pillow" in r.lower() or "raster" in r.lower() for r in reasons)
    assert any("kitty" in r.lower() or "sixel" in r.lower() or "terminal" in r.lower() for r in reasons)


def test_as_dict_contains_required_keys():
    caps = ImageOutputCapabilities()
    d = caps.as_dict()
    for key in ("mermaid_renderer_available", "raster_renderer_available", "kitty_supported",
                "sixel_supported", "can_show_mermaid_image", "ansi_fallback"):
        assert key in d, f"Missing key: {key}"


# ── Resolver: prefer_image_mode (TGFX-002) ────────────────────────────────────

def _config_with_chain(*pairs: tuple[str, str]) -> VisualViewportConfig:
    return VisualViewportConfig(
        default_renderer="ansi_blocks",
        default_output_adapter="ansi",
        fallback_chain=tuple(FallbackPair(renderer=r, adapter=a) for r, a in pairs),
    )


def test_prefer_image_mode_selects_kitty_when_available():
    caps = TerminalVisualCapabilities(ansi=True, kitty_graphics=True, sixel=False)
    config = _config_with_chain(("ansi_blocks", "ansi"))
    resolution = resolve_renderer_adapter_pair(
        config=config,
        capabilities=caps,
        available_renderers={"ansi_blocks", "cpu_raster"},
        available_adapters={"ansi", "kitty"},
        prefer_image_mode=True,
    )
    assert resolution.renderer == "cpu_raster"
    assert resolution.adapter == "kitty"


def test_prefer_image_mode_selects_sixel_when_kitty_unavailable():
    caps = TerminalVisualCapabilities(ansi=True, kitty_graphics=False, sixel=True)
    config = _config_with_chain(("ansi_blocks", "ansi"))
    resolution = resolve_renderer_adapter_pair(
        config=config,
        capabilities=caps,
        available_renderers={"ansi_blocks", "cpu_raster"},
        available_adapters={"ansi", "sixel"},
        prefer_image_mode=True,
    )
    assert resolution.renderer == "cpu_raster"
    assert resolution.adapter == "sixel"


def test_prefer_image_mode_falls_back_to_ansi_when_no_protocol():
    caps = TerminalVisualCapabilities(ansi=True, kitty_graphics=False, sixel=False)
    config = _config_with_chain(("ansi_blocks", "ansi"))
    resolution = resolve_renderer_adapter_pair(
        config=config,
        capabilities=caps,
        available_renderers={"ansi_blocks"},
        available_adapters={"ansi"},
        prefer_image_mode=True,
    )
    assert resolution.renderer == "ansi_blocks"
    assert resolution.adapter == "ansi"
    assert any("no image protocol" in d.lower() or "ansi" in d.lower() for d in resolution.diagnostics)


def test_no_prefer_image_mode_stays_with_config_default():
    caps = TerminalVisualCapabilities(ansi=True, kitty_graphics=True)
    config = VisualViewportConfig(
        default_renderer="ansi_blocks",
        default_output_adapter="ansi",
    )
    resolution = resolve_renderer_adapter_pair(
        config=config,
        capabilities=caps,
        available_renderers={"ansi_blocks", "cpu_raster"},
        available_adapters={"ansi", "kitty"},
        prefer_image_mode=False,
    )
    assert resolution.renderer == "ansi_blocks"
    assert resolution.adapter == "ansi"


def test_diagnostics_list_skipped_candidates():
    caps = TerminalVisualCapabilities(ansi=True, kitty_graphics=True)
    config = _config_with_chain(("ansi_blocks", "ansi"))
    resolution = resolve_renderer_adapter_pair(
        config=config,
        capabilities=caps,
        available_renderers={"ansi_blocks", "cpu_raster"},
        available_adapters={"ansi", "kitty"},
        prefer_image_mode=True,
    )
    # Should have diagnostic entries explaining selections
    assert len(resolution.diagnostics) > 0


# ── VisualViewportConfig new fields ──────────────────────────────────────────

def test_config_ansi_theme_default():
    cfg = VisualViewportConfig()
    assert cfg.ansi_theme == "auto"


def test_config_ansi_theme_invalid_raises():
    with pytest.raises(ValueError):
        VisualViewportConfig(ansi_theme="rainbow")


def test_config_sixel_encoder_mode_default():
    cfg = VisualViewportConfig()
    assert cfg.sixel_encoder_mode == "auto"


def test_config_sixel_encoder_mode_invalid_raises():
    with pytest.raises(ValueError):
        VisualViewportConfig(sixel_encoder_mode="unknown")


def test_config_from_mapping_includes_new_fields():
    cfg = VisualViewportConfig.from_mapping({"ansi_theme": "dark", "sixel_encoder_mode": "internal"})
    assert cfg.ansi_theme == "dark"
    assert cfg.sixel_encoder_mode == "internal"
