from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.visual.markdown.config import MarkdownMermaidConfig, config_from_dict
from client_surfaces.operator_tui.visual.markdown.document_source import (
    DocumentSource,
    inline_source,
    resolve_source,
)
from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import (
    MermaidFallbackInfo,
    render_markdown_ansi,
    render_markdown_ansi_lines,
)
from client_surfaces.operator_tui.visual.markdown.markdown_parser import parse_markdown
from client_surfaces.operator_tui.visual.markdown.mermaid_block_extractor import extract_mermaid_blocks
from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements


@dataclass
class MarkdownMermaidDocumentView:
    view_id: str = "markdown_mermaid_document"
    _config: MarkdownMermaidConfig = field(default_factory=MarkdownMermaidConfig)
    _mermaid_renderer: MermaidRenderer = field(default_factory=MermaidRenderer)
    _scroll_offset: int = 0

    def update(self, dt: float, state: dict[str, Any]) -> None:
        _ = dt
        if "scroll_offset" in state:
            self._scroll_offset = max(0, int(state["scroll_offset"]))
        raw_cfg = state.get("markdown_mermaid_config")
        if isinstance(raw_cfg, dict):
            try:
                self._config = config_from_dict(raw_cfg)
            except ValueError:
                pass

    def render(self, context: ViewContext) -> RenderScene:
        source = self._resolve_document_source(context.state)
        allowed_roots = tuple(str(r) for r in (context.state.get("allowed_roots") or []))
        artifacts = context.state.get("artifacts") if isinstance(context.state.get("artifacts"), dict) else {}

        text, error = resolve_source(source, allowed_roots=allowed_roots, artifacts=artifacts)
        if error:
            return self._error_scene(f"Document unavailable: {error}")

        blocks = parse_markdown(text)
        mermaid_fallbacks: dict[str, MermaidFallbackInfo] = {}

        if self._config.mermaid_mode != "disabled":
            for mb in extract_mermaid_blocks(blocks):
                result = self._mermaid_renderer.render(mb.source)
                if not result.success:
                    mermaid_fallbacks[mb.source] = MermaidFallbackInfo(
                        source=mb.source,
                        reason=result.reason or "Mermaid image renderer unavailable",
                    )

        scroll_offset = self._scroll_offset
        if bool(context.state.get("markdown_auto_follow")):
            rendered_lines = render_markdown_ansi_lines(
                blocks,
                width=context.region.columns,
                mermaid_fallbacks=mermaid_fallbacks,
            )
            scroll_offset = max(0, len(rendered_lines) - context.region.rows)

        lines = render_markdown_ansi(
            blocks,
            width=context.region.columns,
            height=context.region.rows,
            scroll_offset=scroll_offset,
            mermaid_fallbacks=mermaid_fallbacks,
        )

        nodes: list[dict[str, Any]] = [
            {"kind": "label", "text": line, "x": 0, "y": y}
            for y, line in enumerate(lines)
        ]
        mermaid_ok = len(mermaid_fallbacks) == 0 and self._config.mermaid_mode != "disabled"
        return RenderScene(
            scene_type="markdown_mermaid_document",
            nodes=nodes,
            metadata={
                "animated": False,
                "cache_hint": "state_versioned",
                "scroll_offset": scroll_offset,
                "mermaid_fallback_count": len(mermaid_fallbacks),
                "view_requirements": {
                    "markdown_ansi": "available",
                    "mermaid_image": "available" if mermaid_ok else "degraded",
                },
            },
        )

    def view_requirements(self) -> ViewRequirements:
        return ViewRequirements(
            view_id=self.view_id,
            display_name="Markdown/Mermaid",
            description="Renders Markdown documents and embedded Mermaid diagrams",
            required_render_features=("ansi",),
            optional_runtime_requirements=("mermaid_image",),
        )

    def capability_report(self) -> dict[str, Any]:
        status = self._mermaid_renderer.capability_status()
        mermaid_image_available = any(ok for ok, _ in status.values())
        return {
            "view_id": self.view_id,
            "markdown_ansi": True,
            "mermaid_image": mermaid_image_available,
            "mermaid_status": {
                name: {"available": ok, "reason": reason}
                for name, (ok, reason) in status.items()
            },
        }

    def _resolve_document_source(self, state: dict[str, Any]) -> DocumentSource:
        src = state.get("document_source")
        if isinstance(src, DocumentSource):
            return src
        if isinstance(src, dict):
            kind = str(src.get("kind") or "inline")
            content = str(src.get("content_or_ref") or "")
            title = str(src.get("title") or "")
            return DocumentSource(kind=kind, content_or_ref=content, title=title)
        text = state.get("markdown_text")
        if isinstance(text, str):
            return inline_source(text)
        return inline_source("(no document)")

    def scroll_context(self, *, content_lines: int = 0) -> "object":
        from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext
        return ScrollContext(
            id="center_viewport",
            label="Center Viewport",
            content_height=max(content_lines, self._scroll_offset + 1),
            viewport_height=max(1, 24),
            offset=self._scroll_offset,
        )

    def _error_scene(self, message: str) -> RenderScene:
        return RenderScene(
            scene_type="markdown_mermaid_document",
            nodes=[{"kind": "error", "text": message, "x": 0, "y": 0}],
            metadata={"animated": False, "cache_hint": "static"},
        )
