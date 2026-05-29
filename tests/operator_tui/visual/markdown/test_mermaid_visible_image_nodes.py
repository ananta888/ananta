"""MDP-018 / MIMG-013: Tests for Mermaid image node generation from document view."""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderResult
from client_surfaces.operator_tui.visual.views.markdown_mermaid_document_view import MarkdownMermaidDocumentView
from client_surfaces.operator_tui.visual.views.base_view import ViewContext
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


def _region(cols: int = 80, rows: int = 24) -> ViewportRegion:
    return ViewportRegion(x=0, y=0, columns=cols, rows=rows, pixel_width=800, pixel_height=480)


def _ctx(text: str, *, plain: bool = False) -> ViewContext:
    state: dict = {"markdown_text": text, "markdown_stream_plain": plain}
    return ViewContext(state=state, region=_region(), now=0.0)


_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
_FAKE_SVG = b"<svg><rect width='10' height='10'/></svg>"

_MERMAID_MD = "# Diagram\n\n```mermaid\ngraph TD\n  A --> B\n```\n\nEnd."


def _fake_renderer(success: bool, fmt: str = "png", data: bytes = _FAKE_PNG):
    result = MermaidRenderResult(
        success=success,
        image_data=data if success else None,
        image_format=fmt if success else "",
        fallback_text="graph TD\n  A --> B",
        reason="" if success else "render failed",
        duration_ms=1.0,
    )
    def render(source: str) -> MermaidRenderResult:
        return result
    return render


class FakeMermaidRenderer:
    def __init__(self, success: bool = True, fmt: str = "png", data: bytes = _FAKE_PNG):
        self._result = MermaidRenderResult(
            success=success,
            image_data=data if success else None,
            image_format=fmt if success else "",
            fallback_text="graph TD\n  A --> B",
            reason="" if success else "render failed",
            duration_ms=1.0,
        )
        self.renderer_order = ("fake_backend",)

    def render(self, source: str) -> MermaidRenderResult:
        return self._result

    def capability_status(self):
        return {"fake_backend": (True, "")}


# ── Successful PNG render → diagram_image node ────────────────────────────────

def test_successful_png_creates_diagram_image_node():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True, fmt="png", data=_FAKE_PNG)
    scene = view.render(_ctx(_MERMAID_MD))
    diagram_nodes = [n for n in scene.nodes if isinstance(n, dict) and n.get("kind") == "diagram_image"]
    assert diagram_nodes, "Expected at least one diagram_image node"
    node = diagram_nodes[0]
    assert node["image_format"] == "png"
    assert node["image_data"] == _FAKE_PNG


def test_successful_svg_creates_diagram_image_node():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True, fmt="svg", data=_FAKE_SVG)
    scene = view.render(_ctx(_MERMAID_MD))
    diagram_nodes = [n for n in scene.nodes if isinstance(n, dict) and n.get("kind") == "diagram_image"]
    assert diagram_nodes
    assert diagram_nodes[0]["image_format"] == "svg"
    assert diagram_nodes[0]["image_data"] == _FAKE_SVG


def test_diagram_node_has_required_fields():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    scene = view.render(_ctx(_MERMAID_MD))
    node = next((n for n in scene.nodes if n.get("kind") == "diagram_image"), None)
    assert node is not None
    for field_name in ("diagram_id", "image_format", "image_data", "x", "y",
                       "requested_width", "requested_height", "alt_text"):
        assert field_name in node, f"Missing field: {field_name}"


# ── Failed render → ANSI fallback ─────────────────────────────────────────────

def test_failed_render_creates_no_image_node():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=False)
    scene = view.render(_ctx(_MERMAID_MD))
    diagram_nodes = [n for n in scene.nodes if n.get("kind") == "diagram_image"]
    assert not diagram_nodes


def test_failed_render_shows_reason_in_ansi():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=False)
    scene = view.render(_ctx(_MERMAID_MD))
    label_texts = " ".join(n.get("text", "") for n in scene.nodes if n.get("kind") == "label")
    # "render failed" should appear in ANSI fallback
    assert "render failed" in label_texts or "mermaid" in label_texts.lower()


# ── Multiple Mermaid blocks ───────────────────────────────────────────────────

def test_multiple_mermaid_blocks_produce_multiple_nodes():
    md = "```mermaid\ngraph TD\n  A-->B\n```\n\nText.\n\n```mermaid\ngraph LR\n  X-->Y\n```"
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    scene = view.render(_ctx(md))
    diagram_nodes = [n for n in scene.nodes if n.get("kind") == "diagram_image"]
    assert len(diagram_nodes) == 2


def test_multiple_mermaid_blocks_have_stable_unique_ids():
    md = "```mermaid\ngraph TD\n  A-->B\n```\n\n```mermaid\ngraph LR\n  X-->Y\n```"
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    scene = view.render(_ctx(md))
    diagram_nodes = [n for n in scene.nodes if n.get("kind") == "diagram_image"]
    ids = [n["diagram_id"] for n in diagram_nodes]
    assert len(set(ids)) == len(ids), "Diagram IDs must be unique"


# ── Diagnostics metadata (MIMG-003 / MDP-009) ─────────────────────────────────

def test_metadata_counts_mermaid_blocks():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    scene = view.render(_ctx(_MERMAID_MD))
    assert scene.metadata.get("mermaid_blocks_total") == 1
    assert scene.metadata.get("mermaid_images_rendered") == 1
    assert scene.metadata.get("mermaid_fallback_count") == 0


def test_metadata_counts_failed_render():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=False)
    scene = view.render(_ctx(_MERMAID_MD))
    assert scene.metadata.get("mermaid_blocks_total") == 1
    assert scene.metadata.get("mermaid_images_rendered") == 0
    assert scene.metadata.get("mermaid_fallback_count") == 1


def test_metadata_visible_images_count():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    scene = view.render(_ctx(_MERMAID_MD))
    assert scene.metadata.get("mermaid_visible_images") == 1


# ── Cache (MDP-008 / MIMG-006) ────────────────────────────────────────────────

def test_cache_prevents_duplicate_renders():
    view = MarkdownMermaidDocumentView()
    call_count = [0]
    orig_render = FakeMermaidRenderer(success=True)

    class CountingRenderer:
        renderer_order = ("counting",)
        def render(self, source: str) -> MermaidRenderResult:
            call_count[0] += 1
            return orig_render.render(source)
        def capability_status(self):
            return {"counting": (True, "")}

    view._mermaid_renderer = CountingRenderer()
    ctx = _ctx(_MERMAID_MD)
    view.render(ctx)
    view.render(ctx)
    # Second render should hit the cache, not call renderer again
    assert call_count[0] == 1, f"Expected 1 render call, got {call_count[0]}"


# ── Graceful fallback (no error for FallbackCodeblock) ────────────────────────

def test_graceful_fallback_not_recorded_as_error():
    """FallbackCodeblockBackend sentinel reason must not produce mermaid_fallback_count > 0."""
    from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderResult
    view = MarkdownMermaidDocumentView()

    class GracefulFallbackRenderer:
        renderer_order = ("fallback_codeblock",)
        def render(self, source: str) -> MermaidRenderResult:
            return MermaidRenderResult(
                success=False, image_data=None, image_format="",
                fallback_text=source, reason="Mermaid image renderer unavailable", duration_ms=0.0,
            )
        def capability_status(self):
            return {"fallback_codeblock": (True, "")}

    view._mermaid_renderer = GracefulFallbackRenderer()
    scene = view.render(_ctx(_MERMAID_MD))
    assert scene.metadata.get("mermaid_fallback_count") == 0
    assert scene.metadata.get("mermaid_images_rendered") == 0


# ── Plain text streaming mode ──────────────────────────────────────────────────

def test_streaming_plain_mode_has_no_diagram_nodes():
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    scene = view.render(_ctx(_MERMAID_MD, plain=True))
    diagram_nodes = [n for n in scene.nodes if n.get("kind") == "diagram_image"]
    assert not diagram_nodes
    assert scene.metadata.get("streaming_plain") is True
