from __future__ import annotations

from pathlib import Path

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def test_doc_open_activates_markdown_mermaid_view(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text("# Hello\n\n```mermaid\ngraph TD\nA-->B\n```\n", encoding="utf-8")

    state = OperatorState(endpoint="http://hub")
    result = execute_command(f"doc open {md}", state)

    game = result.state.header_logo_game or {}
    assert result.handled is True
    assert game.get("visual_viewport_enabled") is True
    assert game.get("visual_viewport_active_view_request") == "markdown_mermaid_document"
    assert game.get("markdown_mermaid_render_requested") is True
    source = dict(game.get("document_source") or {})
    assert source.get("kind") == "file"
    assert source.get("content_or_ref") == str(md.resolve())


def test_doc_open_requires_existing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    state = OperatorState(endpoint="http://hub")
    result = execute_command(f"doc open {missing}", state)
    assert result.handled is False
    assert "nicht gefunden" in str(result.state.status_message).lower()
