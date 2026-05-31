from __future__ import annotations

from pathlib import Path
import json

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
    assert game.get("markdown_stream_plain") is True
    assert game.get("markdown_mermaid_render_requested") is False
    source = dict(game.get("document_source") or {})
    assert source.get("kind") == "file"
    assert source.get("content_or_ref") == str(md.resolve())


def test_doc_open_requires_existing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    state = OperatorState(endpoint="http://hub")
    result = execute_command(f"doc open {missing}", state)
    assert result.handled is False
    assert "nicht gefunden" in str(result.state.status_message).lower()


def test_doc_preflight_returns_structured_report() -> None:
    state = OperatorState(endpoint="http://hub")
    result = execute_command("doc preflight", state)
    assert result.handled is True
    assert "doc preflight" in str(result.state.status_message).lower()
    payload = json.loads(result.message)
    assert payload["status"] == "ok"
    assert isinstance(payload.get("report"), dict)
    assert isinstance(payload.get("hints"), list)
    report = dict(payload["report"])
    for key in ("mmdc_path", "node_path", "chafa_path", "playwright_installed", "wsl2_detected"):
        assert key in report


def test_doc_switch_uses_current_center_payload_as_markdown() -> None:
    state = OperatorState(
        endpoint="http://hub",
        section_id="dashboard",
        section_payloads={"dashboard": {"status": "ok", "items": [1, 2]}},
    )
    result = execute_command("doc switch", state)
    game = result.state.header_logo_game or {}
    assert result.handled is True
    assert game.get("visual_viewport_enabled") is True
    assert game.get("visual_viewport_active_view_request") == "markdown_mermaid_document"
    assert game.get("markdown_stream_plain") is True
    assert game.get("markdown_mermaid_render_requested") is False
    text = str(game.get("markdown_text") or "")
    assert text.startswith("# dashboard")
    assert "```json" in text
    assert '"status": "ok"' in text


def test_doc_switch_disables_center_browser_and_enables_visual_viewport() -> None:
    state = OperatorState(
        endpoint="http://hub",
        section_id="dashboard",
        header_logo_game={"center_browser_active": True, "center_browser_status": "active"},
        section_payloads={"dashboard": {"k": "v"}},
    )
    result = execute_command("doc switch", state)
    game = result.state.header_logo_game or {}
    assert result.handled is True
    assert game.get("center_browser_active") is False
    assert game.get("center_browser_status") == "exited"
    assert dict(game.get("visual_viewport") or {}).get("enabled") is True
    assert game.get("visual_viewport_enabled") is True


def test_doc_mode_switches_between_simple_rendered_and_mermaid() -> None:
    state = OperatorState(endpoint="http://hub", header_logo_game={})
    r1 = execute_command("doc mode simple", state)
    g1 = dict(r1.state.header_logo_game or {})
    assert g1.get("markdown_stream_plain") is True
    assert g1.get("markdown_mermaid_render_requested") is False
    r2 = execute_command("doc mode rendered", r1.state)
    g2 = dict(r2.state.header_logo_game or {})
    assert g2.get("markdown_stream_plain") is False
    assert g2.get("markdown_mermaid_render_requested") is False
    r3 = execute_command("doc mode mermaid", r2.state)
    g3 = dict(r3.state.header_logo_game or {})
    assert g3.get("markdown_stream_plain") is False
    assert g3.get("markdown_mermaid_render_requested") is True
