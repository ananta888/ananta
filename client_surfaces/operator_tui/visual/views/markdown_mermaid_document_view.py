from __future__ import annotations

import hashlib
import textwrap
import time
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
from client_surfaces.operator_tui.visual.markdown.markdown_parser import MermaidBlock, parse_markdown
from client_surfaces.operator_tui.visual.markdown.mermaid_block_extractor import extract_mermaid_blocks
from client_surfaces.operator_tui.visual.markdown.mermaid_cache import MermaidCache
from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements

# Rows reserved per Mermaid diagram block in ANSI/layout mode
_DIAGRAM_RESERVED_ROWS = 10
# Sentinel reason emitted by FallbackCodeblockBackend — not a real render failure
_GRACEFUL_FALLBACK_REASON = "Mermaid image renderer unavailable"


def _source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _make_diagram_image_node(
    *,
    diagram_id: str,
    image_format: str,
    image_data: bytes,
    x: int,
    y: int,
    requested_width: int,
    requested_height: int,
    alt_text: str = "",
    fallback_text: str = "",
    render_duration_ms: float = 0.0,
    cache_hit: bool = False,
) -> dict[str, Any]:
    """Build a diagram_image RenderScene node (MIMG-002 / MDP-006)."""
    return {
        "kind": "diagram_image",
        "diagram_id": diagram_id,
        "image_format": image_format,
        "image_data": image_data,
        "x": x,
        "y": y,
        "requested_width": requested_width,
        "requested_height": requested_height,
        "alt_text": alt_text,
        "fallback_text": fallback_text,
        "render_duration_ms": render_duration_ms,
        "cache_hit": cache_hit,
    }


@dataclass
class MarkdownMermaidDocumentView:
    view_id: str = "markdown_mermaid_document"
    _config: MarkdownMermaidConfig = field(default_factory=MarkdownMermaidConfig)
    _mermaid_renderer: MermaidRenderer = field(default_factory=MermaidRenderer)
    _mermaid_cache: MermaidCache = field(default_factory=MermaidCache)
    _scroll_offset: int = 0
    _last_content_lines: int = 0

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
        if bool(context.state.get("markdown_stream_plain")):
            return self._streaming_plain_scene(context)
        return self._rendered_scene(context)

    def _rendered_scene(self, context: ViewContext) -> RenderScene:
        source = self._resolve_document_source(context.state)
        allowed_roots = tuple(str(r) for r in (context.state.get("allowed_roots") or []))
        artifacts = context.state.get("artifacts") if isinstance(context.state.get("artifacts"), dict) else {}

        text, error = resolve_source(source, allowed_roots=allowed_roots, artifacts=artifacts)
        if error:
            return self._error_scene(f"Document unavailable: {error}")

        blocks = parse_markdown(text)
        mermaid_fallbacks: dict[str, MermaidFallbackInfo] = {}
        diagram_nodes: list[dict[str, Any]] = []

        # Diagnostics counters (MIMG-003 / MDP-009)
        mermaid_blocks_total = 0
        mermaid_images_rendered = 0
        mermaid_fallback_count = 0
        mermaid_renderer_used: str = "none"
        cache_hits = 0
        cache_misses = 0

        if self._config.mermaid_mode != "disabled":
            mermaid_block_list = extract_mermaid_blocks(blocks)
            mermaid_blocks_total = len(mermaid_block_list)
            diagram_w = max(64, context.region.columns)
            diagram_h = _DIAGRAM_RESERVED_ROWS * 14  # approx pixel height

            for idx, mb in enumerate(mermaid_block_list):
                src_hash = _source_hash(mb.source)
                backend_name = self._mermaid_renderer.renderer_order[0] if self._mermaid_renderer.renderer_order else "unknown"
                diagram_id = f"mermaid_{src_hash}_{idx}"

                # Check cache first (MDP-008 / MIMG-006)
                cached = self._mermaid_cache.get(
                    src_hash, backend_name, "auto", diagram_w, diagram_h
                )
                if cached is not None and cached.success:
                    cache_hits += 1
                    result = cached
                    was_cache_hit = True
                else:
                    cache_misses += 1
                    was_cache_hit = False
                    t0 = time.perf_counter()
                    result = self._mermaid_renderer.render(mb.source)
                    duration_ms = (time.perf_counter() - t0) * 1000.0
                    if result.success:
                        self._mermaid_cache.put(src_hash, backend_name, "auto", diagram_w, diagram_h, result)

                if result.success and result.image_data:
                    # Attach as diagram_image node (MIMG-004 / MDP-006)
                    mermaid_images_rendered += 1
                    mermaid_renderer_used = backend_name
                    diagram_nodes.append(_make_diagram_image_node(
                        diagram_id=diagram_id,
                        image_format=result.image_format or "svg",
                        image_data=result.image_data,
                        x=0,
                        y=idx * _DIAGRAM_RESERVED_ROWS,
                        requested_width=diagram_w,
                        requested_height=diagram_h,
                        alt_text=f"Mermaid diagram {idx + 1}",
                        fallback_text=mb.source,
                        render_duration_ms=result.duration_ms,
                        cache_hit=was_cache_hit,
                    ))
                elif not result.success and result.reason != _GRACEFUL_FALLBACK_REASON:
                    # Real render failure — record for ANSI fallback display
                    mermaid_fallback_count += 1
                    mermaid_fallbacks[mb.source] = MermaidFallbackInfo(
                        source=mb.source,
                        reason=result.reason or "render failed",
                    )

        # Compute scroll offset
        scroll_offset = self._scroll_offset
        if bool(context.state.get("markdown_auto_follow")):
            rendered_lines = render_markdown_ansi_lines(
                blocks,
                width=context.region.columns,
                mermaid_fallbacks=mermaid_fallbacks,
            )
            scroll_offset = max(0, len(rendered_lines) - context.region.rows)
            self._last_content_lines = len(rendered_lines)

        lines = render_markdown_ansi(
            blocks,
            width=context.region.columns,
            height=context.region.rows,
            scroll_offset=scroll_offset,
            mermaid_fallbacks=mermaid_fallbacks,
        )
        if not bool(context.state.get("markdown_auto_follow")):
            self._last_content_lines = max(self._last_content_lines, len(lines) + scroll_offset)

        # Build label nodes from ANSI lines
        nodes: list[dict[str, Any]] = [
            {"kind": "label", "text": line, "x": 0, "y": y}
            for y, line in enumerate(lines)
        ]
        # Append diagram image nodes after label nodes (MIMG-004)
        nodes.extend(diagram_nodes)

        mermaid_image_ok = mermaid_images_rendered > 0
        cache_diag = self._mermaid_cache.diagnostics()

        return RenderScene(
            scene_type="markdown_mermaid_document",
            nodes=nodes,
            metadata={
                "animated": False,
                "cache_hint": "state_versioned",
                "scroll_offset": scroll_offset,
                "content_lines": self._last_content_lines,
                # Mermaid diagnostics (MIMG-003 / MDP-009)
                "mermaid_blocks_total": mermaid_blocks_total,
                "mermaid_images_rendered": mermaid_images_rendered,
                "mermaid_fallback_count": mermaid_fallback_count,
                "mermaid_renderer_used": mermaid_renderer_used,
                "mermaid_cache_hits": cache_diag["hits"],
                "mermaid_cache_misses": cache_diag["misses"],
                "mermaid_visible_images": mermaid_images_rendered,
                "view_requirements": {
                    "markdown_ansi": "available",
                    "mermaid_image": "available" if mermaid_image_ok else "degraded",
                    "mermaid_renderer": mermaid_renderer_used if mermaid_image_ok else "none",
                },
            },
        )

    def _streaming_plain_scene(self, context: ViewContext) -> RenderScene:
        text = str(context.state.get("markdown_plain_text") or context.state.get("markdown_text") or "")
        width = max(1, context.region.columns)
        body_width = max(1, width)
        lines: list[str] = [
            "# Chat-Nachricht",
            "",
            "> Antwortstream wird hier in der mittleren Ansicht fortgesetzt.",
            "",
        ]
        for raw_line in text.splitlines() or [""]:
            if not raw_line:
                lines.append("")
                continue
            lines.extend(textwrap.wrap(raw_line, width=body_width) or [""])

        scroll_offset = 0
        if bool(context.state.get("markdown_auto_follow")):
            scroll_offset = max(0, len(lines) - context.region.rows)
        else:
            scroll_offset = self._scroll_offset
        visible = lines[scroll_offset : scroll_offset + context.region.rows]
        while len(visible) < context.region.rows:
            visible.append("")

        nodes = [{"kind": "label", "text": line, "x": 0, "y": y} for y, line in enumerate(visible)]
        return RenderScene(
            scene_type="markdown_mermaid_document",
            nodes=nodes,
            metadata={
                "animated": False,
                "cache_hint": "state_versioned",
                "scroll_offset": scroll_offset,
                "content_lines": len(lines),
                "streaming_plain": True,
                "mermaid_blocks_total": 0,
                "mermaid_images_rendered": 0,
                "mermaid_fallback_count": 0,
                "mermaid_visible_images": 0,
                "view_requirements": {
                    "markdown_ansi": "available",
                    "mermaid_image": "deferred",
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
        real_backends = {k: v for k, v in status.items() if k != "fallback_codeblock"}
        mermaid_image_available = any(ok for ok, _ in real_backends.values())
        return {
            "view_id": self.view_id,
            "markdown_ansi": True,
            "mermaid_renderer": mermaid_image_available,
            "mermaid_image": mermaid_image_available,
            "mermaid_status": {
                name: {"available": ok, "reason": reason}
                for name, (ok, reason) in status.items()
            },
            "cache_diagnostics": self._mermaid_cache.diagnostics(),
        }

    def scroll_context(self, *, content_lines: int = 0) -> "object":
        """Return accurate ScrollContext for shared ScrollManager (MDP-005)."""
        from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext
        effective_content = max(content_lines, self._last_content_lines, self._scroll_offset + 1)
        return ScrollContext(
            id="center_viewport",
            label="Center Viewport",
            content_height=effective_content,
            viewport_height=max(1, 24),
            offset=self._scroll_offset,
        )

    def apply_scroll_offset(self, offset: int) -> None:
        """Called by ScrollManager to update this view's scroll position (MDP-005)."""
        self._scroll_offset = max(0, offset)

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

    def _error_scene(self, message: str) -> RenderScene:
        return RenderScene(
            scene_type="markdown_mermaid_document",
            nodes=[{"kind": "error", "text": message, "x": 0, "y": 0}],
            metadata={
                "animated": False,
                "cache_hint": "static",
                "mermaid_blocks_total": 0,
                "mermaid_images_rendered": 0,
                "mermaid_fallback_count": 0,
                "mermaid_visible_images": 0,
            },
        )
