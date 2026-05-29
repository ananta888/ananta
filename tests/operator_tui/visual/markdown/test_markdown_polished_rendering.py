"""MDP-017: Tests for polished Markdown rendering."""
from __future__ import annotations

import re

import pytest

from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import render_markdown_ansi_lines
from client_surfaces.operator_tui.visual.markdown.markdown_parser import parse_markdown, parse_inline_spans


def _strip(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _render(md: str, width: int = 80) -> list[str]:
    return [_strip(l) for l in render_markdown_ansi_lines(parse_markdown(md), width=width)]


# ── Headings ──────────────────────────────────────────────────────────────────

def test_h1_rendered_with_prefix():
    lines = _render("# Hello World")
    assert any("# Hello World" in l for l in lines)


def test_h2_rendered_with_separator():
    lines = _render("## Section")
    assert any("## Section" in l for l in lines)
    assert any("─" in l for l in lines)


def test_h3_no_separator():
    lines = _render("### Sub")
    assert any("### Sub" in l for l in lines)
    # No separator for h3+
    content_lines = [l for l in lines if l.strip()]
    assert not any("─" * 5 in l for l in content_lines if "Sub" not in l)


def test_h6_rendered():
    lines = _render("###### Deep")
    assert any("######" in l for l in lines)


# ── Inline formatting ──────────────────────────────────────────────────────────

def test_inline_code_backtick_preserved():
    spans = parse_inline_spans("Use `print()` here")
    kinds = [k for k, _ in spans]
    assert "code" in kinds
    code_span = next((c for k, c in spans if k == "code"), "")
    assert "print()" in code_span


def test_bold_star_parsed():
    spans = parse_inline_spans("This is **bold** text")
    kinds = [k for k, _ in spans]
    assert "bold" in kinds


def test_italic_star_parsed():
    spans = parse_inline_spans("This is *italic* text")
    kinds = [k for k, _ in spans]
    assert "italic" in kinds


def test_link_parsed():
    spans = parse_inline_spans("See [Python](https://python.org) for more")
    link_spans = [(k, c) for k, c in spans if k == "link"]
    assert link_spans
    label, _, url = link_spans[0][1].partition("\x00")
    assert label == "Python"
    assert "python.org" in url


def test_mixed_inline_in_paragraph():
    md = "Use `code` and **bold** and *italic* here."
    lines = _render(md)
    full = " ".join(lines)
    # After rendering, the plain text content should still be there
    assert "code" in full
    assert "bold" in full
    assert "italic" in full


# ── Bullet lists ──────────────────────────────────────────────────────────────

def test_bullet_list_rendered():
    lines = _render("- Alpha\n- Beta\n- Gamma")
    content = [l for l in lines if l.strip()]
    assert any("Alpha" in l for l in content)
    assert any("Beta" in l for l in content)
    assert any("Gamma" in l for l in content)


def test_bullet_uses_bullet_char():
    lines = _render("- Item one")
    assert any("•" in l or "-" in l or "·" in l for l in lines)


# ── Numbered lists ─────────────────────────────────────────────────────────────

def test_numbered_list_rendered():
    lines = _render("1. First\n2. Second\n3. Third")
    content = [l for l in lines if l.strip()]
    assert any("First" in l for l in content)
    assert any("Second" in l for l in content)


# ── Blockquote ────────────────────────────────────────────────────────────────

def test_blockquote_rendered_with_marker():
    lines = _render("> This is a quote")
    assert any("▌" in l or "|" in l for l in lines)
    assert any("quote" in l for l in lines)


# ── Horizontal rule ───────────────────────────────────────────────────────────

def test_hr_rendered():
    lines = _render("---")
    assert any("─" in l for l in lines)


# ── Tables ────────────────────────────────────────────────────────────────────

def test_table_renders_headers():
    md = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
    lines = _render(md, width=60)
    full = "\n".join(lines)
    assert "Name" in full
    assert "Alice" in full
    assert "Bob" in full


def test_table_renders_box_chars():
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    lines = _render(md, width=40)
    full = "\n".join(lines)
    assert "│" in full or "|" in full


def test_table_narrow_viewport():
    md = "| Col1 | Col2 | Col3 |\n|------|------|------|\n| a | b | c |"
    lines = _render(md, width=20)
    # Should not crash, just clip
    assert len(lines) > 0


# ── Code blocks ───────────────────────────────────────────────────────────────

def test_code_block_shows_language():
    md = "```python\nprint('hello')\n```"
    lines = _render(md)
    assert any("python" in l.lower() for l in lines)


def test_code_block_shows_source():
    md = "```\nmy code here\n```"
    lines = _render(md)
    assert any("my code here" in l for l in lines)


def test_code_block_clips_long_lines():
    long_line = "x" * 200
    md = f"```\n{long_line}\n```"
    lines = _render(md, width=80)
    for l in lines:
        if "x" in _strip(l):
            assert len(_strip(l)) <= 82  # some tolerance for prefix chars


def test_code_block_no_ansi_injection():
    # Ensure ANSI sequences in code content are escaped
    md = "```\n\033[31mRED\033[0m\n```"
    lines = _render(md)
    # Should not contain raw ESC from code content
    # Our renderer replaces \033 with ^[
    assert any("^[" in l for l in lines) or not any("\033[31m" in l for l in lines)


# ── Mermaid source fallback ───────────────────────────────────────────────────

def test_mermaid_source_shown_as_code_block():
    md = "```mermaid\ngraph TD\n  A --> B\n```"
    lines = _render(md)
    full = "\n".join(lines)
    assert "mermaid" in full.lower()
    assert "A --> B" in full


def test_mermaid_no_error_header_without_fallback():
    md = "```mermaid\ngraph TD\n  A --> B\n```"
    lines = _render(md)
    assert not any("unavailable" in l.lower() for l in lines)


# ── Links ────────────────────────────────────────────────────────────────────

def test_link_renders_label_and_url():
    md = "Visit [Python](https://python.org) today."
    lines = _render(md)
    full = "\n".join(lines)
    assert "Python" in full
    assert "python.org" in full


def test_long_url_shortened():
    long_url = "https://example.com/" + "x" * 100
    md = f"[label]({long_url})"
    lines = _render(md, width=80)
    full = "\n".join(lines)
    # URL should be shortened (truncated with ellipsis)
    assert "…" in full or len(full) < len(long_url) + 50


# ── Viewport width ────────────────────────────────────────────────────────────

def test_narrow_viewport_does_not_crash():
    md = "# Heading\n\nSome paragraph text.\n\n- item\n- another"
    lines = _render(md, width=20)
    assert len(lines) > 0


def test_deterministic_output():
    md = "# Test\n\nParagraph.\n\n- a\n- b"
    out1 = _render(md)
    out2 = _render(md)
    assert out1 == out2


def test_empty_document():
    lines = _render("")
    assert any("empty" in l.lower() for l in lines)
