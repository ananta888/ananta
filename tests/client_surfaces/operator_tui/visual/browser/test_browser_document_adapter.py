from __future__ import annotations

import pytest

from client_surfaces.operator_tui.visual.browser.browser_document_adapter import (
    BrowserDocumentAdapter,
    _escape_plain_text,
    _markdown_to_html,
    _sanitize_html,
)
from client_surfaces.operator_tui.visual.browser.center_content_snapshot import (
    CenterContentSnapshot,
    unsupported_snapshot,
)


# ---------------------------------------------------------------------------
# Plain-text escaping
# ---------------------------------------------------------------------------

class TestEscapePlainText:
    def test_angle_brackets_escaped(self) -> None:
        result = _escape_plain_text("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_ampersand_escaped(self) -> None:
        result = _escape_plain_text("a & b")
        assert "&amp;" in result

    def test_quotes_escaped(self) -> None:
        result = _escape_plain_text('"hello"')
        assert "&quot;" in result or "&#x27;" in result or '"' not in result

    def test_plain_text_preserved(self) -> None:
        result = _escape_plain_text("Hello, World!")
        assert "Hello, World!" in result

    def test_empty_string(self) -> None:
        assert _escape_plain_text("") == ""


# ---------------------------------------------------------------------------
# HTML sanitization
# ---------------------------------------------------------------------------

class TestSanitizeHtml:
    def test_script_tag_removed(self) -> None:
        raw = "<p>Hello</p><script>alert('bad')</script>"
        result = _sanitize_html(raw)
        assert "<script>" not in result.lower()
        assert "alert" not in result

    def test_iframe_removed(self) -> None:
        raw = "<p>Visible</p><iframe src='http://evil.example'></iframe>"
        result = _sanitize_html(raw)
        assert "<iframe" not in result.lower()

    def test_javascript_href_removed(self) -> None:
        raw = '<a href="javascript:alert(1)">click</a>'
        result = _sanitize_html(raw)
        assert "javascript:" not in result.lower()

    def test_safe_html_preserved(self) -> None:
        raw = "<h1>Title</h1><p>Paragraph</p><code>code</code>"
        result = _sanitize_html(raw)
        assert "<h1>Title</h1>" in result
        assert "<p>Paragraph</p>" in result

    def test_on_handler_removed(self) -> None:
        raw = '<p onclick="doEvil()">text</p>'
        result = _sanitize_html(raw)
        assert "onclick" not in result


# ---------------------------------------------------------------------------
# Markdown conversion
# ---------------------------------------------------------------------------

class TestMarkdownToHtml:
    def test_heading_h1(self) -> None:
        result = _markdown_to_html("# My Title")
        assert "<h1>" in result
        assert "My Title" in result

    def test_heading_h3(self) -> None:
        result = _markdown_to_html("### Section")
        assert "<h3>" in result

    def test_bold(self) -> None:
        result = _markdown_to_html("**bold text**")
        assert "<strong>bold text</strong>" in result

    def test_italic(self) -> None:
        result = _markdown_to_html("*italic*")
        assert "<em>italic</em>" in result

    def test_inline_code(self) -> None:
        result = _markdown_to_html("Use `print()` here")
        assert "<code>print()</code>" in result

    def test_code_fence(self) -> None:
        source = "```python\nprint('hello')\n```"
        result = _markdown_to_html(source)
        assert "<pre>" in result
        assert "print" in result

    def test_mermaid_fence_gets_special_class(self) -> None:
        source = "```mermaid\ngraph TD\nA-->B\n```"
        result = _markdown_to_html(source)
        assert "mermaid-source" in result

    def test_unordered_list(self) -> None:
        source = "- item one\n- item two"
        result = _markdown_to_html(source)
        assert "<ul>" in result
        assert "<li>" in result

    def test_blockquote(self) -> None:
        result = _markdown_to_html("> quoted text")
        assert "<blockquote>" in result

    def test_html_chars_in_text_escaped(self) -> None:
        source = "Use <angle> & ampersand"
        result = _markdown_to_html(source)
        assert "<angle>" not in result
        assert "&lt;angle&gt;" in result

    def test_link_rendered(self) -> None:
        result = _markdown_to_html("[Ananta](https://example.com)")
        assert "<a href=" in result
        assert "Ananta" in result

    def test_javascript_link_stripped(self) -> None:
        result = _markdown_to_html("[click](javascript:alert(1))")
        assert "javascript:" not in result

    def test_title_injected(self) -> None:
        result = _markdown_to_html("content", title="My Doc")
        assert "<h1>My Doc</h1>" in result


# ---------------------------------------------------------------------------
# BrowserDocumentAdapter integration
# ---------------------------------------------------------------------------

class TestBrowserDocumentAdapter:
    def setup_method(self) -> None:
        # Use a minimal template to avoid filesystem access in tests
        self.template = "<!DOCTYPE html><html><head><title>{{TITLE}}</title></head><body>{{BODY}}</body></html>"
        self.adapter = BrowserDocumentAdapter(template_override=self.template)

    def _snap(self, content_type: str, source_text: str = "", html_text: str = "", title: str = "T") -> CenterContentSnapshot:
        return CenterContentSnapshot(
            content_type=content_type,
            title=title,
            source_text=source_text,
            html_text=html_text,
        )

    def test_plain_text_escaped(self) -> None:
        snap = self._snap("plain_text", source_text="<script>alert()</script>")
        doc = self.adapter.to_html(snap)
        assert "<script>" not in doc
        assert "&lt;script&gt;" in doc

    def test_markdown_renders_headings(self) -> None:
        snap = self._snap("markdown", source_text="# Hello World")
        doc = self.adapter.to_html(snap)
        assert "<h1>" in doc
        assert "Hello World" in doc

    def test_mermaid_markdown_renders(self) -> None:
        snap = self._snap("mermaid_markdown", source_text="```mermaid\ngraph TD\nA-->B\n```")
        doc = self.adapter.to_html(snap)
        assert "mermaid-source" in doc

    def test_html_preview_sanitized(self) -> None:
        snap = self._snap("html_preview", html_text="<p>safe</p><script>bad()</script>")
        doc = self.adapter.to_html(snap)
        assert "<p>safe</p>" in doc
        assert "<script>" not in doc.lower()

    def test_html_preview_no_html_uses_source(self) -> None:
        snap = self._snap("html_preview", source_text="raw <text>", html_text="")
        doc = self.adapter.to_html(snap)
        assert "&lt;text&gt;" in doc

    def test_ansi_text_escaped(self) -> None:
        snap = self._snap("ansi_text", source_text="line1\nline2")
        doc = self.adapter.to_html(snap)
        assert "line1" in doc

    def test_unsupported_snapshot(self) -> None:
        snap = unsupported_snapshot(reason="view not supported")
        doc = self.adapter.to_html(snap)
        assert "Browser preview unavailable" in doc
        assert "view not supported" in doc

    def test_title_in_document(self) -> None:
        snap = self._snap("plain_text", title="My Title", source_text="content")
        doc = self.adapter.to_html(snap)
        assert "My Title" in doc

    def test_source_code_escaped(self) -> None:
        snap = self._snap("source_code", source_text="def f(): return <value>")
        doc = self.adapter.to_html(snap)
        assert "&lt;value&gt;" in doc

    def test_deterministic_output(self) -> None:
        snap = self._snap("plain_text", source_text="hello world")
        doc1 = self.adapter.to_html(snap)
        doc2 = self.adapter.to_html(snap)
        assert doc1 == doc2
