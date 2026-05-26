from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def _mock_github_rows() -> list[dict]:
    return [
        {
            "run": {
                "run_id": 101,
                "workflow_name": "CI",
                "branch": "main",
                "commit_sha": "abcdef1234567890",
                "conclusion": "failure",
                "html_url": "https://example/runs/101",
                "updated_at": "2026-05-27T00:00:00Z",
            },
            "jobs": [
                {
                    "job_id": 9001,
                    "job_name": "tests",
                    "conclusion": "failure",
                    "html_url": "https://example/runs/101/jobs/9001",
                    "log_excerpt": "FAILURES\nAssertionError related T03.03 goal-help-1",
                }
            ],
        }
    ]


def test_helpcenter_view_empty_and_render(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = execute_command(":helpcenter", _state())
    assert result.handled is True
    payload = json.loads(result.message)
    assert payload["helpcenter_mode"] is True
    assert payload["reports"] == []
    rendered = render_operator_shell(result.state.with_updates(section_id="artifacts"), width=170, height=40)
    assert "Helpcenter" in rendered
    assert "no helpcenter reports" in rendered


def test_helpcenter_ingest_github_failures_dry_run_and_write(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    state = _state().with_updates(header_logo_game={"helpcenter_mock_github_rows": _mock_github_rows()})
    dry = execute_command(":helpcenter ingest github-failures --repo acme/repo --limit 1 --dry-run", state)
    assert dry.handled is True
    dry_payload = json.loads(dry.message)
    assert dry_payload["ingest"]["found"] == 1
    assert dry_payload["ingest"]["written"] == 0

    write = execute_command(":helpcenter ingest github-failures --repo acme/repo --limit 1", dry.state)
    assert write.handled is True
    write_payload = json.loads(write.message)
    assert write_payload["ingest"]["found"] == 1
    assert write_payload["ingest"]["written"] == 1
    selected = dict(write_payload["payload"]["selected_report"])
    assert str(selected.get("json_ref") or "").endswith(".json")
    assert str(selected.get("report_ref") or "").endswith(".md")


def test_helpcenter_detail_open_and_followup_suggestion(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    state = _state().with_updates(header_logo_game={"helpcenter_mock_github_rows": _mock_github_rows()})
    write = execute_command(":helpcenter ingest github-failures --repo acme/repo --limit 1", state)
    payload = json.loads(write.message)
    analysis_id = str(payload["payload"]["selected_analysis_id"])

    opened = execute_command(f":helpcenter open {analysis_id}", write.state)
    assert opened.handled is True
    opened_payload = json.loads(opened.message)
    assert opened_payload["selected_analysis_id"] == analysis_id
    assert opened_payload["selected_analysis"]["no_auto_fix"] is True

    suggestion = execute_command(":helpcenter suggest-followup", opened.state)
    assert suggestion.handled is True
    suggestion_payload = json.loads(suggestion.message)
    assert suggestion_payload["auto_create"] is False
    assert "followup_suggestion" in suggestion_payload

    rendered = render_operator_shell(suggestion.state.with_updates(section_id="artifacts"), width=170, height=44)
    assert "no_auto_fix=True" in rendered
    assert "Follow-up suggestion:" in rendered
