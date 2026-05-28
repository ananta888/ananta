from __future__ import annotations

import re
import subprocess
from unittest.mock import patch

from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import (
    MermaidFallbackInfo,
    render_markdown_ansi,
)
from client_surfaces.operator_tui.visual.markdown.markdown_parser import (
    MermaidBlock,
    ParagraphBlock,
    parse_markdown,
)
from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidCliBackend


def _strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _visible(lines: list[str]) -> str:
    return "\n".join(_strip_ansi(l) for l in lines)


def test_failed_mermaid_does_not_crash_surrounding_text():
    blocks = parse_markdown("# Title\n\n```mermaid\ngraph A-->B\n```\n\nEnd of doc")
    fallbacks = {
        b.source: MermaidFallbackInfo(source=b.source, reason="Mermaid image renderer unavailable: mmdc missing")
        for b in blocks
        if isinstance(b, MermaidBlock)
    }
    combined = _visible(render_markdown_ansi(blocks, width=60, height=20, mermaid_fallbacks=fallbacks))
    assert "Title" in combined
    assert "End of doc" in combined


def test_fallback_shows_concise_reason():
    source = "graph TD\nA --> B"
    fallbacks = {source: MermaidFallbackInfo(source=source, reason="mmdc not found")}
    combined = _visible(
        render_markdown_ansi([MermaidBlock(source=source)], width=60, height=10, mermaid_fallbacks=fallbacks)
    )
    assert "mmdc not found" in combined


def test_fallback_shows_original_mermaid_source():
    source = "sequenceDiagram\nA ->> B: hello"
    fallbacks = {source: MermaidFallbackInfo(source=source, reason="unavailable")}
    combined = _visible(
        render_markdown_ansi([MermaidBlock(source=source)], width=60, height=15, mermaid_fallbacks=fallbacks)
    )
    assert "sequenceDiagram" in combined


def test_timeout_path_produces_fallback_not_crash():
    with patch(
        "client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_mmdc",
        return_value="/usr/bin/mmdc",
    ):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="mmdc", timeout=15)):
            result = MermaidCliBackend().render("graph A-->B", timeout_seconds=15.0)
    assert result.success is False
    assert "timeout" in result.reason.lower()
    assert result.fallback_text == "graph A-->B"


def test_multiple_mermaid_blocks_independent():
    blocks = [
        ParagraphBlock(text="Start"),
        MermaidBlock(source="graph A-->B"),
        ParagraphBlock(text="Middle"),
        MermaidBlock(source="sequenceDiagram\nX->>Y:msg"),
        ParagraphBlock(text="End"),
    ]
    fallbacks = {
        "graph A-->B": MermaidFallbackInfo(source="graph A-->B", reason="mmdc missing"),
        "sequenceDiagram\nX->>Y:msg": MermaidFallbackInfo(
            source="sequenceDiagram\nX->>Y:msg", reason="timeout"
        ),
    }
    combined = _visible(render_markdown_ansi(blocks, width=60, height=30, mermaid_fallbacks=fallbacks))
    assert "Start" in combined
    assert "Middle" in combined
    assert "End" in combined
    assert "mmdc missing" in combined
    assert "timeout" in combined


def test_source_only_config_skips_image_rendering():
    from client_surfaces.operator_tui.visual.markdown.config import MarkdownMermaidConfig
    from client_surfaces.operator_tui.visual.views.markdown_mermaid_document_view import MarkdownMermaidDocumentView
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

    view = MarkdownMermaidDocumentView()
    view._config = MarkdownMermaidConfig(mermaid_mode="disabled")
    region = ViewportRegion(x=0, y=0, columns=60, rows=10, pixel_width=800, pixel_height=450)
    context = ViewContext(
        region=region,
        now=0.0,
        state={"markdown_text": "# Title\n\n```mermaid\ngraph A-->B\n```"},
    )
    scene = view.render(context)
    assert scene.scene_type == "markdown_mermaid_document"
    assert scene.metadata["mermaid_fallback_count"] == 0
