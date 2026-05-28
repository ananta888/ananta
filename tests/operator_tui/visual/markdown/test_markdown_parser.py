from __future__ import annotations

import pytest

from client_surfaces.operator_tui.visual.markdown.markdown_parser import (
    BlockquoteBlock,
    BulletListBlock,
    FencedCodeBlock,
    HeadingBlock,
    HorizontalRuleBlock,
    MermaidBlock,
    NumberedListBlock,
    ParagraphBlock,
    TableBlock,
    parse_markdown,
)


def test_heading_levels():
    blocks = parse_markdown("# H1\n## H2\n### H3")
    headings = [b for b in blocks if isinstance(b, HeadingBlock)]
    assert len(headings) == 3
    assert headings[0].level == 1 and headings[0].text == "H1"
    assert headings[1].level == 2 and headings[1].text == "H2"
    assert headings[2].level == 3 and headings[2].text == "H3"


def test_heading_up_to_level_6():
    blocks = parse_markdown("###### H6")
    h = [b for b in blocks if isinstance(b, HeadingBlock)]
    assert len(h) == 1 and h[0].level == 6


def test_paragraph_joined():
    blocks = parse_markdown("Hello world\nSecond line")
    paras = [b for b in blocks if isinstance(b, ParagraphBlock)]
    assert len(paras) == 1
    assert "Hello world" in paras[0].text
    assert "Second line" in paras[0].text


def test_separate_paragraphs():
    blocks = parse_markdown("Para one\n\nPara two")
    paras = [b for b in blocks if isinstance(b, ParagraphBlock)]
    assert len(paras) == 2


def test_bullet_list_dash():
    blocks = parse_markdown("- item one\n- item two\n- item three")
    lists = [b for b in blocks if isinstance(b, BulletListBlock)]
    assert len(lists) == 1
    assert lists[0].items == ("item one", "item two", "item three")


def test_bullet_list_star():
    blocks = parse_markdown("* alpha\n* beta")
    lists = [b for b in blocks if isinstance(b, BulletListBlock)]
    assert len(lists) == 1 and len(lists[0].items) == 2


def test_numbered_list():
    blocks = parse_markdown("1. first\n2. second\n3. third")
    lists = [b for b in blocks if isinstance(b, NumberedListBlock)]
    assert len(lists) == 1
    assert lists[0].items == ("first", "second", "third")


def test_fenced_code_block_python():
    md = "```python\nprint('hello')\n```"
    blocks = parse_markdown(md)
    code = [b for b in blocks if isinstance(b, FencedCodeBlock)]
    assert len(code) == 1
    assert code[0].language == "python"
    assert "print" in code[0].source


def test_fenced_code_block_no_language():
    md = "```\nsome code\n```"
    blocks = parse_markdown(md)
    code = [b for b in blocks if isinstance(b, FencedCodeBlock)]
    assert len(code) == 1
    assert code[0].language == ""


def test_mermaid_block_detected():
    md = "```mermaid\ngraph TD\nA --> B\n```"
    blocks = parse_markdown(md)
    mermaid = [b for b in blocks if isinstance(b, MermaidBlock)]
    assert len(mermaid) == 1
    assert "graph TD" in mermaid[0].source


def test_mermaid_preserves_exact_source():
    src = "graph TD\n  A -->|label| B\n  B --> C"
    md = f"```mermaid\n{src}\n```"
    blocks = parse_markdown(md)
    m = [b for b in blocks if isinstance(b, MermaidBlock)]
    assert m[0].source == src


def test_multiple_mermaid_blocks():
    md = "```mermaid\ngraph A --> B\n```\n\nText\n\n```mermaid\nsequenceDiagram\n```"
    blocks = parse_markdown(md)
    mermaid = [b for b in blocks if isinstance(b, MermaidBlock)]
    assert len(mermaid) == 2


def test_blockquote():
    blocks = parse_markdown("> This is a quote")
    quotes = [b for b in blocks if isinstance(b, BlockquoteBlock)]
    assert len(quotes) == 1
    assert "This is a quote" in quotes[0].text


def test_horizontal_rule():
    blocks = parse_markdown("---")
    hrs = [b for b in blocks if isinstance(b, HorizontalRuleBlock)]
    assert len(hrs) == 1


def test_table_fallback():
    md = "| Col1 | Col2 |\n|------|------|\n| a    | b    |"
    blocks = parse_markdown(md)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    assert "Col1" in tables[0].raw_text


def test_empty_input_returns_empty():
    assert parse_markdown("") == []


def test_whitespace_only_returns_empty():
    assert parse_markdown("   \n\n   ") == []


def test_invalid_markdown_no_crash():
    blocks = parse_markdown("###\n   \n\nsome text\n\n```unclosed")
    assert isinstance(blocks, list)


def test_mermaid_block_does_not_become_fenced_code():
    md = "```mermaid\ngraph A-->B\n```"
    blocks = parse_markdown(md)
    assert not any(isinstance(b, FencedCodeBlock) for b in blocks)
    assert any(isinstance(b, MermaidBlock) for b in blocks)
