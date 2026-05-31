from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.visual.markdown.config import DocsGraphicsProfile, MarkdownMermaidConfig


def _looks_like_wsl2() -> bool:
    try:
        import os

        if str(os.environ.get("ANANTA_TUI_WSL2") or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if os.environ.get("WSL_DISTRO_NAME"):
            return True
        with open("/proc/version", "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read().lower()
        return "microsoft" in text or "wsl" in text
    except Exception:
        return False


@dataclass(frozen=True)
class EffectiveRenderPolicy:
    active_profile: str
    backend_order: tuple[str, ...]
    timeout_seconds: float
    max_pixel_width: int
    max_pixel_height: int
    prefer_image_over_source: bool
    wsl2_detected: bool


class MarkdownRenderPolicyResolver:
    """Resolves effective Markdown/Mermaid rendering policy from config + runtime context."""

    def resolve(self, *, config: MarkdownMermaidConfig, state: dict[str, Any] | None = None) -> EffectiveRenderPolicy:
        state = state or {}
        requested = str(state.get("docs_graphics_profile") or config.docs_graphics_profile or "default").strip()
        profiles = {profile.name: profile for profile in config.docs_graphics_profiles}
        wsl2_detected = _looks_like_wsl2()

        if requested in {"auto", "wsl2_auto"}:
            profile_name = "wsl2" if wsl2_detected and "wsl2" in profiles else "default"
        else:
            profile_name = requested

        profile = profiles.get(profile_name) or profiles.get("default")
        if profile is None:
            profile = DocsGraphicsProfile(
                name="default",
                backend_order=config.mermaid_renderers,
                timeout_seconds=config.timeout_seconds,
                max_pixel_width=config.max_diagram_width,
                max_pixel_height=config.max_diagram_height,
                prefer_image_over_source=True,
            )

        return EffectiveRenderPolicy(
            active_profile=profile.name,
            backend_order=profile.backend_order,
            timeout_seconds=float(profile.timeout_seconds),
            max_pixel_width=int(profile.max_pixel_width),
            max_pixel_height=int(profile.max_pixel_height),
            prefer_image_over_source=bool(profile.prefer_image_over_source),
            wsl2_detected=wsl2_detected,
        )
