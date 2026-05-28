from __future__ import annotations

from dataclasses import dataclass

_VALID_MD_MODES = frozenset({"ansi", "raster_optional", "source_only"})
_VALID_MM_MODES = frozenset({"auto", "image", "source_only", "disabled"})
_VALID_BACKENDS = frozenset({"mermaid_cli", "playwright", "fallback_codeblock"})


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
    return MarkdownMermaidConfig(**kwargs)
