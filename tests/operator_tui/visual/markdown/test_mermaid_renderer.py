from __future__ import annotations

from unittest.mock import patch

from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import (
    FallbackCodeblockBackend,
    MermaidCliBackend,
    MermaidRenderer,
    MermaidRenderResult,
    PlaywrightBackend,
)


def test_fallback_codeblock_always_available():
    ok, reason = FallbackCodeblockBackend().available()
    assert ok is True
    assert reason == ""


def test_fallback_codeblock_returns_source_with_reason():
    source = "graph TD\nA-->B"
    result = FallbackCodeblockBackend().render(source)
    assert result.success is False
    assert result.fallback_text == source
    assert "unavailable" in result.reason.lower()


def test_mermaid_cli_unavailable_when_mmdc_missing():
    with patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_mmdc", return_value=None):
        ok, reason = MermaidCliBackend().available()
        assert ok is False
        assert "mmdc" in reason.lower()


def test_mermaid_cli_render_returns_fallback_when_unavailable():
    with patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_mmdc", return_value=None):
        result = MermaidCliBackend().render("graph A-->B")
        assert result.success is False
        assert result.fallback_text == "graph A-->B"
        assert "mmdc" in result.reason.lower()


def test_playwright_unavailable_when_not_installed():
    with patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_playwright", return_value=False):
        ok, reason = PlaywrightBackend().available()
        assert ok is False
        assert "playwright" in reason.lower()


def test_playwright_render_returns_fallback_when_not_installed():
    with patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_playwright", return_value=False):
        result = PlaywrightBackend().render("graph A-->B")
        assert result.success is False
        assert "playwright" in result.reason.lower()


def test_renderer_uses_fallback_when_no_image_renderer():
    renderer = MermaidRenderer(renderer_order=("fallback_codeblock",))
    result = renderer.render("graph TD\nA-->B")
    assert result.success is False
    assert result.fallback_text


def test_renderer_skips_unavailable_image_backends():
    with (
        patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_mmdc", return_value=None),
        patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_playwright", return_value=False),
    ):
        renderer = MermaidRenderer()
        result = renderer.render("graph A-->B")
        assert result.success is False
        assert result.fallback_text == "graph A-->B"


def test_renderer_capability_status_includes_all_backends():
    renderer = MermaidRenderer()
    status = renderer.capability_status()
    assert "fallback_codeblock" in status
    ok, _ = status["fallback_codeblock"]
    assert ok is True


def test_renderer_capability_status_reports_mmdc_unavailable():
    with patch("client_surfaces.operator_tui.visual.markdown.mermaid_renderer._check_mmdc", return_value=None):
        renderer = MermaidRenderer()
        status = renderer.capability_status()
        ok, reason = status["mermaid_cli"]
        assert ok is False
        assert "mmdc" in reason.lower()


def test_render_result_is_dataclass():
    result = MermaidRenderResult(
        success=False,
        image_data=None,
        image_format="",
        fallback_text="x",
        reason="test",
        duration_ms=0.0,
    )
    assert result.success is False
    assert result.fallback_text == "x"
