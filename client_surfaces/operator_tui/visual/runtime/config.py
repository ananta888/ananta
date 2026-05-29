from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FallbackPair:
    renderer: str
    adapter: str


@dataclass(frozen=True)
class VisualViewportConfig:
    enabled: bool = False
    position: str = "center"
    default_view: str = "renderer_diagnostics"
    default_renderer: str = "cpu_raster"
    default_output_adapter: str = "kitty"
    # TGFX-011: ANSI theme — "auto" | "dark" | "light" | "no_color"
    ansi_theme: str = "auto"
    # TGFX-007: Sixel encoder mode — "auto" | "internal" | "disabled"
    sixel_encoder_mode: str = "auto"
    target_fps: int = 10
    animation_fps: int = 15
    max_fps: int = 30
    default_pixel_width: int = 800
    default_pixel_height: int = 450
    max_pixel_width: int = 1280
    max_pixel_height: int = 720
    fallback_chain: tuple[FallbackPair, ...] = field(
        default_factory=lambda: (
            FallbackPair(renderer="cpu_raster", adapter="kitty"),
            FallbackPair(renderer="cpu_raster", adapter="sixel"),
            FallbackPair(renderer="ansi_blocks", adapter="ansi"),
        )
    )

    def __post_init__(self) -> None:
        if self.ansi_theme not in {"auto", "dark", "light", "no_color"}:
            raise ValueError(f"ansi_theme must be auto/dark/light/no_color, got {self.ansi_theme!r}")
        if self.sixel_encoder_mode not in {"auto", "internal", "disabled"}:
            raise ValueError(f"sixel_encoder_mode must be auto/internal/disabled, got {self.sixel_encoder_mode!r}")
        if self.target_fps <= 0 or self.animation_fps <= 0 or self.max_fps <= 0:
            raise ValueError("fps values must be positive")
        if self.target_fps > self.max_fps or self.animation_fps > self.max_fps:
            raise ValueError("target/animation fps must be <= max_fps")
        if self.default_pixel_width <= 0 or self.default_pixel_height <= 0:
            raise ValueError("default pixel size must be positive")
        if self.max_pixel_width <= 0 or self.max_pixel_height <= 0:
            raise ValueError("max pixel size must be positive")
        if self.default_pixel_width > self.max_pixel_width or self.default_pixel_height > self.max_pixel_height:
            raise ValueError("default pixel size must be <= max pixel size")
        if not self.default_view.strip():
            raise ValueError("default_view must not be empty")
        if not self.default_renderer.strip():
            raise ValueError("default_renderer must not be empty")
        if not self.default_output_adapter.strip():
            raise ValueError("default_output_adapter must not be empty")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "VisualViewportConfig":
        raw = dict(mapping or {})
        fallback_chain_raw = raw.get("fallback_chain")
        parsed_chain: tuple[FallbackPair, ...] | None = None
        if fallback_chain_raw is not None:
            if not isinstance(fallback_chain_raw, list):
                raise ValueError("fallback_chain must be a list")
            pairs: list[FallbackPair] = []
            for row in fallback_chain_raw:
                if not isinstance(row, dict):
                    raise ValueError("fallback_chain entries must be objects")
                renderer = str(row.get("renderer") or "").strip()
                adapter = str(row.get("adapter") or "").strip()
                if not renderer or not adapter:
                    raise ValueError("fallback_chain entries require renderer and adapter")
                pairs.append(FallbackPair(renderer=renderer, adapter=adapter))
            parsed_chain = tuple(pairs)

        kwargs: dict[str, Any] = {
            "enabled": bool(raw.get("enabled", False)),
            "position": str(raw.get("position") or "center"),
            "default_view": str(raw.get("default_view") or "renderer_diagnostics"),
            "default_renderer": str(raw.get("default_renderer") or "cpu_raster"),
            "default_output_adapter": str(raw.get("default_output_adapter") or "kitty"),
            "ansi_theme": str(raw.get("ansi_theme") or "auto"),
            "sixel_encoder_mode": str(raw.get("sixel_encoder_mode") or "auto"),
            "target_fps": int(raw.get("target_fps", 10)),
            "animation_fps": int(raw.get("animation_fps", 15)),
            "max_fps": int(raw.get("max_fps", 30)),
            "default_pixel_width": int(raw.get("default_pixel_width", 800)),
            "default_pixel_height": int(raw.get("default_pixel_height", 450)),
            "max_pixel_width": int(raw.get("max_pixel_width", 1280)),
            "max_pixel_height": int(raw.get("max_pixel_height", 720)),
        }
        if parsed_chain is not None:
            kwargs["fallback_chain"] = parsed_chain
        return cls(**kwargs)

