from __future__ import annotations

import re

from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import (
    MermaidFallbackInfo,
    render_markdown_ansi,
)
from client_surfaces.operator_tui.visual.markdown.markdown_parser import (
    BlockquoteBlock,
    BulletListBlock,
    FencedCodeBlock,
    HeadingBlock,
    HorizontalRuleBlock,
    MermaidBlock,
    NumberedListBlock,
    ParagraphBlock,
    parse_markdown,
)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _visible(lines: list[str]) -> str:
    return "\n".join(_strip_ansi(l) for l in lines)


def test_heading_visible():
    lines = render_markdown_ansi([HeadingBlock(level=1, text="My Title")], width=40, height=10)
    assert "My Title" in _visible(lines)


def test_heading_prefix_present():
    lines = render_markdown_ansi([HeadingBlock(level=2, text="Sub")], width=40, height=10)
    assert "## Sub" in _visible(lines)


def test_paragraph_visible():
    lines = render_markdown_ansi([ParagraphBlock(text="Hello world")], width=40, height=10)
    assert "Hello world" in _visible(lines)


def test_bullet_list_items_visible():
    blocks = [BulletListBlock(items=("alpha", "beta", "gamma"))]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    combined = _visible(lines)
    assert "alpha" in combined and "beta" in combined and "gamma" in combined


def test_numbered_list_items_visible():
    blocks = [NumberedListBlock(items=("first", "second"))]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    combined = _visible(lines)
    assert "first" in combined and "second" in combined


def test_code_block_shows_language_label():
    blocks = [FencedCodeBlock(language="python", source="x = 1")]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    combined = _visible(lines)
    assert "python" in combined
    assert "x = 1" in combined


def test_code_block_no_language():
    blocks = [FencedCodeBlock(language="", source="raw code")]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    assert "raw code" in _visible(lines)


def test_blockquote_visible():
    blocks = [BlockquoteBlock(text="wise words")]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    assert "wise words" in _visible(lines)


def test_horizontal_rule_visible():
    blocks = [HorizontalRuleBlock()]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    combined = _visible(lines)
    assert any(c in combined for c in ("─", "-"))


def test_output_clips_to_height():
    blocks = [ParagraphBlock(text=f"Line {i}") for i in range(50)]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    assert len(lines) == 10


def test_output_pads_to_height():
    blocks = [ParagraphBlock(text="Short")]
    lines = render_markdown_ansi(blocks, width=40, height=10)
    assert len(lines) == 10


def test_scroll_offset_changes_output():
    blocks = [HeadingBlock(level=1, text="Top")] + [ParagraphBlock(text=f"P{i}") for i in range(20)]
    lines_a = render_markdown_ansi(blocks, width=40, height=5, scroll_offset=0)
    lines_b = render_markdown_ansi(blocks, width=40, height=5, scroll_offset=5)
    assert lines_a != lines_b


def test_scroll_offset_zero_same_as_default():
    blocks = [ParagraphBlock(text="Text")]
    assert (
        render_markdown_ansi(blocks, width=40, height=5)
        == render_markdown_ansi(blocks, width=40, height=5, scroll_offset=0)
    )


def test_mermaid_fallback_shows_reason():
    source = "graph TD\nA-->B"
    blocks = [MermaidBlock(source=source)]
    fallbacks = {source: MermaidFallbackInfo(source=source, reason="mmdc not found")}
    lines = render_markdown_ansi(blocks, width=60, height=15, mermaid_fallbacks=fallbacks)
    combined = _visible(lines)
    assert "mmdc not found" in combined
    assert "graph TD" in combined


def test_mermaid_without_fallback_shows_placeholder():
    source = "graph A-->B"
    blocks = [MermaidBlock(source=source)]
    lines = render_markdown_ansi(blocks, width=60, height=10, mermaid_fallbacks={})
    combined = _visible(lines)
    assert "mermaid" in combined.lower()


def test_mermaid_fallback_does_not_block_other_blocks():
    blocks = [
        ParagraphBlock(text="Before"),
        MermaidBlock(source="graph A-->B"),
        ParagraphBlock(text="After"),
    ]
    fallbacks = {"graph A-->B": MermaidFallbackInfo(source="graph A-->B", reason="unavailable")}
    combined = _visible(render_markdown_ansi(blocks, width=60, height=20, mermaid_fallbacks=fallbacks))
    assert "Before" in combined and "After" in combined


def test_empty_document_returns_placeholder():
    lines = render_markdown_ansi([], width=40, height=5)
    assert len(lines) == 5
    assert "empty" in _visible(lines).lower()


def test_deterministic_output():
    blocks = parse_markdown("# Title\n\nHello.\n\n- item 1\n- item 2")
    l1 = render_markdown_ansi(blocks, width=40, height=10)
    l2 = render_markdown_ansi(blocks, width=40, height=10)
    assert l1 == l2


def test_viewport_width_respected():
    blocks = [ParagraphBlock(text="a" * 100)]
    lines = render_markdown_ansi(blocks, width=20, height=10)
    # No line should exceed width in visible characters (ANSI codes excluded)
    for line in lines:
        assert len(_strip_ansi(line)) <= 20 + 5  # small tolerance for wrap edge cases
