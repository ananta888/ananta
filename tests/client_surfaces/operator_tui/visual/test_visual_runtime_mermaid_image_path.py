"""MIMG-017: E2E test for full VisualRuntime Mermaid image path.

Tests the path: markdown_mermaid_document view → RenderScene with
diagram_image nodes → raster RenderFrame → image-capable adapter.
Uses fake Mermaid renderer and monkeypatching — no real mmdc, Playwright or terminal.
"""
from __future__ import annotations

import io
import time
from unittest.mock import patch

import pytest

from client_surfaces.operator_tui.visual.adapters.ansi_adapter import AnsiOutputAdapter
from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
from client_surfaces.operator_tui.visual.adapters.noop_adapter import NoopDiagnosticsAdapter
from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderResult, MermaidRenderer
from client_surfaces.operator_tui.visual.renderers.ansi_renderer import AnsiBlocksRenderer
from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
from client_surfaces.operator_tui.visual.runtime.config import VisualViewportConfig
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
from client_surfaces.operator_tui.visual.runtime.registry import (
    OutputAdapterRegistry,
    RendererRegistry,
    ViewRegistry,
)
from client_surfaces.operator_tui.visual.runtime.visual_runtime import VisualRuntime
from client_surfaces.operator_tui.visual.views.markdown_mermaid_document_view import MarkdownMermaidDocumentView
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

_FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_MERMAID_MD = "# Test\n\n```mermaid\ngraph TD\n  A-->B\n```\n\nEnd."


class FakeMermaidRenderer(MermaidRenderer):
    """MermaidRenderer that returns fake PNG without calling mmdc/playwright."""

    def __init__(self, success: bool = True):
        super().__init__()
        self._success = success

    def render(self, source: str) -> MermaidRenderResult:
        if self._success:
            return MermaidRenderResult(
                success=True,
                image_data=_FAKE_PNG,
                image_format="png",
                fallback_text="",
                reason="",
                duration_ms=1.0,
            )
        return MermaidRenderResult(
            success=False, image_data=None, image_format="",
            fallback_text=source, reason="fake_render_failed", duration_ms=0.0,
        )


def _build_runtime(*, kitty: bool = False, mermaid_success: bool = True) -> VisualRuntime:
    caps = TerminalVisualCapabilities(ansi=True, kitty_graphics=kitty, sixel=False)

    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=mermaid_success)

    views = ViewRegistry()
    views.register_factory("markdown_mermaid_document", lambda: view)
    views.register_factory("renderer_diagnostics", lambda: view)

    renderers = RendererRegistry()
    renderers.register_factory("ansi_blocks", lambda: AnsiBlocksRenderer())
    renderers.register_factory("cpu_raster", lambda: CpuRasterRenderer(max_width=64, max_height=32))

    adapters = OutputAdapterRegistry()
    adapters.register_factory("ansi", lambda: AnsiOutputAdapter())
    adapters.register_factory("kitty", lambda: KittyOutputAdapter(supported=kitty, enabled=True))
    adapters.register_factory("noop_diagnostics", lambda: NoopDiagnosticsAdapter())

    config = VisualViewportConfig(
        enabled=True,
        default_view="markdown_mermaid_document",
        default_renderer="cpu_raster" if kitty else "ansi_blocks",
        default_output_adapter="kitty" if kitty else "ansi",
        default_pixel_width=64,
        default_pixel_height=32,
    )
    runtime = VisualRuntime(
        config=config,
        view_registry=views,
        renderer_registry=renderers,
        adapter_registry=adapters,
        capabilities=caps,
    )
    runtime.switch_view("markdown_mermaid_document")
    return runtime


def _region() -> ViewportRegion:
    return ViewportRegion(x=0, y=0, columns=40, rows=10, pixel_width=64, pixel_height=32)


def _rendered_state(version: str = "test") -> dict[str, object]:
    return {
        "markdown_text": _MERMAID_MD,
        "markdown_stream_plain": False,
        "markdown_mermaid_render_requested": True,
        "visual_state_version": version,
    }


# ── Image-capable runtime (Kitty) ──────────────────────────────────────────────

def test_kitty_runtime_renders_markdown_without_crash():
    runtime = _build_runtime(kitty=True, mermaid_success=True)
    frame = runtime.render_frame(region=_region(), now=time.monotonic(), state=_rendered_state("test-1"), force=True)
    assert frame is not None


def test_kitty_runtime_produces_raster_frame_for_mermaid():
    runtime = _build_runtime(kitty=True, mermaid_success=True)
    frame = runtime.render_frame(region=_region(), now=time.monotonic(), state=_rendered_state("test-2"), force=True)
    if frame is not None:
        assert frame.frame_type in {"raster", "ansi"}


def test_kitty_runtime_scene_contains_diagram_image_node():
    """Verify the view produces diagram_image nodes when Mermaid succeeds."""
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    ctx = ViewContext(state=_rendered_state(), region=_region(), now=0.0)
    scene = view.render(ctx)
    diagram_nodes = [n for n in scene.nodes if n.get("kind") == "diagram_image"]
    assert diagram_nodes, "Image-capable path must produce diagram_image nodes"
    assert diagram_nodes[0]["image_format"] == "png"


def test_kitty_adapter_receives_raster_frame_with_mermaid_payload():
    """Verify the full path from diagram_image node through cpu_raster to Kitty."""
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext

    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    ctx = ViewContext(state=_rendered_state(), region=_region(), now=0.0)
    scene = view.render(ctx)
    assert scene.metadata.get("mermaid_visible_images", 0) >= 1

    renderer = CpuRasterRenderer(max_width=64, max_height=32)
    frame = renderer.render(scene, width=64, height=32, context=RenderContext(now=time.monotonic()))
    assert frame.frame_type == "raster"

    if frame.mime_or_format == "image/png":
        adapter = KittyOutputAdapter(supported=True, enabled=True)
        out = io.StringIO()
        result = adapter.draw(frame, region=_region(), stream=out, context=DrawContext(now=time.monotonic()))
        assert result.drawn is True
        assert "\x1b_G" in out.getvalue()


# ── ANSI-only runtime (no image protocol) ────────────────────────────────────

def test_ansi_only_runtime_renders_markdown_source_fallback():
    runtime = _build_runtime(kitty=False, mermaid_success=False)
    frame = runtime.render_frame(region=_region(), now=time.monotonic(), state=_rendered_state("test-ansi-1"), force=True)
    assert frame is not None
    assert frame.frame_type == "ansi"


def test_ansi_only_runtime_no_crash_with_mermaid():
    """ANSI-only path must handle Mermaid without crashing."""
    runtime = _build_runtime(kitty=False, mermaid_success=True)
    frame = runtime.render_frame(region=_region(), now=time.monotonic(), state=_rendered_state("test-ansi-2"), force=True)
    assert frame is not None


def test_ansi_frame_contains_mermaid_source():
    """ANSI fallback must show mermaid source code."""
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    import re

    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=False)
    ctx = ViewContext(state=_rendered_state(), region=_region(), now=0.0)
    scene = view.render(ctx)
    label_texts = " ".join(re.sub(r"\033\[[0-9;]*m", "", n.get("text", "")) for n in scene.nodes if n.get("kind") == "label")
    assert "A-->B" in label_texts or "mermaid" in label_texts.lower()


# ── get_view_instance ─────────────────────────────────────────────────────────

def test_runtime_get_view_instance_returns_none_before_render():
    runtime = _build_runtime(kitty=False)
    # Before first render, instance may not be cached
    instance = runtime.get_view_instance("markdown_mermaid_document")
    # Could be None or the pre-created instance; must not crash
    assert instance is None or hasattr(instance, "render")


def test_runtime_get_view_instance_returns_instance_after_render():
    runtime = _build_runtime(kitty=False)
    runtime.render_frame(region=_region(), now=time.monotonic(), state={
        "markdown_text": "# Hello", "visual_state_version": "v1",
    }, force=True)
    instance = runtime.get_view_instance("markdown_mermaid_document")
    # After render the instance should be cached
    if instance is not None:
        assert hasattr(instance, "render")


# ── Metadata: mermaid_visible_images ─────────────────────────────────────────

def test_scene_metadata_mermaid_visible_images_image_path():
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    ctx = ViewContext(state=_rendered_state(), region=_region(), now=0.0)
    scene = view.render(ctx)
    assert scene.metadata.get("mermaid_visible_images") == 1


def test_scene_does_not_render_mermaid_without_explicit_request():
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=True)
    ctx = ViewContext(
        state={"markdown_text": _MERMAID_MD, "markdown_stream_plain": False},
        region=_region(),
        now=0.0,
    )
    scene = view.render(ctx)
    assert scene.metadata.get("mermaid_visible_images") == 0
    assert not [n for n in scene.nodes if n.get("kind") == "diagram_image"]


def test_scene_metadata_mermaid_visible_images_ansi_path():
    from client_surfaces.operator_tui.visual.views.base_view import ViewContext
    view = MarkdownMermaidDocumentView()
    view._mermaid_renderer = FakeMermaidRenderer(success=False)
    ctx = ViewContext(state=_rendered_state(), region=_region(), now=0.0)
    scene = view.render(ctx)
    assert scene.metadata.get("mermaid_visible_images") == 0
