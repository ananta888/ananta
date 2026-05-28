from __future__ import annotations

import pytest

from client_surfaces.operator_tui.visual.runtime.config import VisualViewportConfig


def test_visual_config_defaults_match_recommended_profile() -> None:
    cfg = VisualViewportConfig()
    assert cfg.default_pixel_width == 800
    assert cfg.default_pixel_height == 450
    assert cfg.max_pixel_width == 1280
    assert cfg.max_pixel_height == 720
    assert cfg.target_fps == 10
    assert cfg.animation_fps == 15
    assert cfg.max_fps == 30


def test_visual_config_rejects_invalid_fps_values() -> None:
    with pytest.raises(ValueError):
        VisualViewportConfig(target_fps=40, max_fps=30)
    with pytest.raises(ValueError):
        VisualViewportConfig(target_fps=0)


def test_visual_config_rejects_invalid_pixel_sizes() -> None:
    with pytest.raises(ValueError):
        VisualViewportConfig(default_pixel_width=-1)
    with pytest.raises(ValueError):
        VisualViewportConfig(default_pixel_width=1920, max_pixel_width=1280)


def test_visual_config_parses_fallback_chain_from_mapping() -> None:
    cfg = VisualViewportConfig.from_mapping(
        {
            "enabled": True,
            "fallback_chain": [
                {"renderer": "cpu_raster", "adapter": "kitty"},
                {"renderer": "ansi_blocks", "adapter": "ansi"},
            ],
        }
    )
    assert cfg.enabled is True
    assert cfg.fallback_chain[0].renderer == "cpu_raster"
    assert cfg.fallback_chain[1].adapter == "ansi"

