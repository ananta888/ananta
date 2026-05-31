from __future__ import annotations

from pathlib import Path

from client_surfaces.operator_tui.visual.markdown.markdown_parser import parse_markdown
from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import render_markdown_ansi


FIXTURE_ROOT = Path("tests/fixtures/scenarios/markdown_mermaid_quality")


def _read(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def test_lists_tables_code_fixture_renders_key_sections() -> None:
    text = _read("lists_tables_code.md")
    blocks = parse_markdown(text)
    rendered = "\n".join(render_markdown_ansi(blocks, width=88, height=40))
    assert "Nested Lists" in rendered
    assert "Table" in rendered
    assert "Code" in rendered
    assert "greet" in rendered


def test_mermaid_flow_fixture_keeps_source_visible_when_no_image_renderer() -> None:
    text = _read("mermaid_flow.md")
    blocks = parse_markdown(text)
    rendered = "\n".join(render_markdown_ansi(blocks, width=88, height=40))
    assert "flowchart TD" in rendered
    assert "Condition" in rendered
