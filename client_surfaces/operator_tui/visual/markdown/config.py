from __future__ import annotations

from dataclasses import dataclass, field

_VALID_MD_MODES = frozenset({"ansi", "raster_optional", "source_only"})
_VALID_MM_MODES = frozenset({"auto", "image", "source_only", "disabled"})
_VALID_BACKENDS = frozenset({"mermaid_cli", "playwright", "fallback_codeblock"})


@dataclass(frozen=True)
class DocsGraphicsProfile:
    name: str
    backend_order: tuple[str, ...]
    timeout_seconds: float
    max_pixel_width: int
    max_pixel_height: int
    prefer_image_over_source: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("docs graphics profile name must not be empty")
        if not self.backend_order:
            raise ValueError("docs graphics profile backend_order must not be empty")
        for backend in self.backend_order:
            if backend not in _VALID_BACKENDS:
                raise ValueError(f"unknown backend {backend!r} in docs graphics profile")
        if self.timeout_seconds <= 0:
            raise ValueError("docs graphics profile timeout_seconds must be positive")
        if self.max_pixel_width <= 0 or self.max_pixel_height <= 0:
            raise ValueError("docs graphics profile max pixel size must be positive")


@dataclass(frozen=True)
class MarkdownMermaidConfig:
    markdown_mode: str = "ansi"
    mermaid_mode: str = "auto"
    mermaid_renderers: tuple[str, ...] = ("mermaid_cli", "playwright", "fallback_codeblock")
    timeout_seconds: float = 15.0
    max_diagram_width: int = 1280
    max_diagram_height: int = 720
    cache_enabled: bool = True
    allowed_roots: tuple[str, ...] = ()
    docs_graphics_profile: str = "default"
    docs_graphics_profiles: tuple[DocsGraphicsProfile, ...] = field(
        default_factory=lambda: (
            DocsGraphicsProfile(
                name="default",
                backend_order=("mermaid_cli", "playwright", "fallback_codeblock"),
                timeout_seconds=15.0,
                max_pixel_width=1280,
                max_pixel_height=720,
                prefer_image_over_source=True,
            ),
            DocsGraphicsProfile(
                name="wsl2",
                backend_order=("mermaid_cli", "fallback_codeblock", "playwright"),
                timeout_seconds=12.0,
                max_pixel_width=1280,
                max_pixel_height=720,
                prefer_image_over_source=True,
            ),
        )
    )

    def __post_init__(self) -> None:
        if self.markdown_mode not in _VALID_MD_MODES:
            raise ValueError(
                f"markdown_mode {self.markdown_mode!r} invalid; choose from {sorted(_VALID_MD_MODES)}"
            )
        if self.mermaid_mode not in _VALID_MM_MODES:
            raise ValueError(
                f"mermaid_mode {self.mermaid_mode!r} invalid; choose from {sorted(_VALID_MM_MODES)}"
            )
        for r in self.mermaid_renderers:
            if r not in _VALID_BACKENDS:
                raise ValueError(f"unknown mermaid renderer {r!r}; valid: {sorted(_VALID_BACKENDS)}")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_diagram_width <= 0 or self.max_diagram_height <= 0:
            raise ValueError("max_diagram dimensions must be positive")
        if not self.docs_graphics_profile.strip():
            raise ValueError("docs_graphics_profile must not be empty")
        known_profiles = {profile.name for profile in self.docs_graphics_profiles}
        if self.docs_graphics_profile not in known_profiles and self.docs_graphics_profile not in {"auto", "wsl2_auto"}:
            raise ValueError(
                f"docs_graphics_profile {self.docs_graphics_profile!r} unknown; "
                f"available: {sorted(known_profiles)}"
            )


def config_from_dict(raw: dict) -> MarkdownMermaidConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"config must be a dict, got {type(raw).__name__}")
    kwargs: dict = {}
    if "markdown_mode" in raw:
        kwargs["markdown_mode"] = str(raw["markdown_mode"])
    if "mermaid_mode" in raw:
        kwargs["mermaid_mode"] = str(raw["mermaid_mode"])
    if "mermaid_renderers" in raw:
        renderers = raw["mermaid_renderers"]
        if not isinstance(renderers, list):
            raise ValueError("mermaid_renderers must be a list")
        kwargs["mermaid_renderers"] = tuple(str(r) for r in renderers)
    if "timeout_seconds" in raw:
        v = raw["timeout_seconds"]
        if not isinstance(v, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        kwargs["timeout_seconds"] = float(v)
    if "max_diagram_width" in raw:
        kwargs["max_diagram_width"] = int(raw["max_diagram_width"])
    if "max_diagram_height" in raw:
        kwargs["max_diagram_height"] = int(raw["max_diagram_height"])
    if "cache_enabled" in raw:
        kwargs["cache_enabled"] = bool(raw["cache_enabled"])
    if "allowed_roots" in raw:
        roots = raw["allowed_roots"]
        if not isinstance(roots, list):
            raise ValueError("allowed_roots must be a list")
        kwargs["allowed_roots"] = tuple(str(r) for r in roots)
    if "docs_graphics_profile" in raw:
        kwargs["docs_graphics_profile"] = str(raw["docs_graphics_profile"]).strip()
    if "docs_graphics_profiles" in raw:
        rows = raw["docs_graphics_profiles"]
        if not isinstance(rows, list):
            raise ValueError("docs_graphics_profiles must be a list")
        profiles: list[DocsGraphicsProfile] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("docs_graphics_profiles entries must be objects")
            name = str(row.get("name") or "").strip()
            backend_order_raw = row.get("backend_order")
            if not isinstance(backend_order_raw, list):
                raise ValueError("docs_graphics_profiles[].backend_order must be a list")
            backend_order = tuple(str(item).strip() for item in backend_order_raw if str(item).strip())
            timeout_seconds = float(row.get("timeout_seconds", raw.get("timeout_seconds", 15.0)))
            max_pixel_width = int(row.get("max_pixel_width", raw.get("max_diagram_width", 1280)))
            max_pixel_height = int(row.get("max_pixel_height", raw.get("max_diagram_height", 720)))
            prefer_image_over_source = bool(row.get("prefer_image_over_source", True))
            profiles.append(
                DocsGraphicsProfile(
                    name=name,
                    backend_order=backend_order,
                    timeout_seconds=timeout_seconds,
                    max_pixel_width=max_pixel_width,
                    max_pixel_height=max_pixel_height,
                    prefer_image_over_source=prefer_image_over_source,
                )
            )
        kwargs["docs_graphics_profiles"] = tuple(profiles)
    return MarkdownMermaidConfig(**kwargs)
