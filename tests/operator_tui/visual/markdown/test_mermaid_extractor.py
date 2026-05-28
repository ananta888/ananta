from __future__ import annotations

from client_surfaces.operator_tui.visual.markdown.mermaid_block_extractor import (
    ExtractedMermaidBlock,
    extract_mermaid_blocks,
)
from client_surfaces.operator_tui.visual.markdown.markdown_parser import (
    HeadingBlock,
    MermaidBlock,
    ParagraphBlock,
    parse_markdown,
)


def test_extract_single_mermaid():
    blocks = [MermaidBlock(source="graph TD\nA --> B")]
    extracted = extract_mermaid_blocks(blocks)
    assert len(extracted) == 1
    assert extracted[0].source == "graph TD\nA --> B"
    assert extracted[0].position_index == 0
    assert len(extracted[0].source_hash) == 16


def test_extract_preserves_exact_source():
    src = "sequenceDiagram\n  A ->> B: hello\n  B -->> A: world"
    extracted = extract_mermaid_blocks([MermaidBlock(source=src)])
    assert extracted[0].source == src


def test_extract_multiple_mermaid_blocks():
    blocks = [
        HeadingBlock(level=1, text="Title"),
        MermaidBlock(source="graph A --> B"),
        ParagraphBlock(text="Between"),
        MermaidBlock(source="sequenceDiagram\nA ->> B: hello"),
    ]
    extracted = extract_mermaid_blocks(blocks)
    assert len(extracted) == 2
    assert extracted[0].position_index == 0
    assert extracted[1].position_index == 1


def test_no_mermaid_returns_empty():
    blocks = [HeadingBlock(level=1, text="Title"), ParagraphBlock(text="Text")]
    assert extract_mermaid_blocks(blocks) == []


def test_empty_blocks_returns_empty():
    assert extract_mermaid_blocks([]) == []


def test_stable_hash_same_input():
    source = "graph TD\nA --> B"
    h1 = extract_mermaid_blocks([MermaidBlock(source=source)])[0].source_hash
    h2 = extract_mermaid_blocks([MermaidBlock(source=source)])[0].source_hash
    assert h1 == h2


def test_different_source_different_hash():
    h1 = extract_mermaid_blocks([MermaidBlock(source="graph A --> B")])[0].source_hash
    h2 = extract_mermaid_blocks([MermaidBlock(source="graph A --> C")])[0].source_hash
    assert h1 != h2


def test_render_config_key_affects_hash():
    blocks = [MermaidBlock(source="graph A-->B")]
    h_default = extract_mermaid_blocks(blocks, render_config_key="")[0].source_hash
    h_other = extract_mermaid_blocks(blocks, render_config_key="theme:dark")[0].source_hash
    assert h_default != h_other


def test_block_id_contains_hash():
    extracted = extract_mermaid_blocks([MermaidBlock(source="graph X-->Y")])
    entry = extracted[0]
    assert entry.source_hash in entry.block_id


def test_from_parsed_document():
    md = "# Doc\n\n```mermaid\ngraph TD\nA-->B\n```\n\nText\n\n```mermaid\nsequenceDiagram\n```"
    blocks = parse_markdown(md)
    extracted = extract_mermaid_blocks(blocks)
    assert len(extracted) == 2
