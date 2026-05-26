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
                "run_id": 401,
                "workflow_name": "CI",
                "branch": "main",
                "commit_sha": "1234abcdef5678",
                "conclusion": "failure",
                "html_url": "https://example/runs/401",
                "updated_at": "2026-05-27T00:00:00Z",
            },
            "jobs": [
                {
                    "job_id": 4401,
                    "job_name": "tests",
                    "conclusion": "failure",
                    "html_url": "https://example/runs/401/jobs/4401",
                    "log_excerpt": "FAILURES\nAssertionError\ntoken=supersecret\n",
                }
            ],
        }
    ]


def test_helpcenter_ingest_github_failure_end_to_end(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    state = _state().with_updates(header_logo_game={"helpcenter_mock_github_rows": _mock_github_rows()})
    result = execute_command(":helpcenter ingest github-failures --repo acme/repo --limit 1", state)
    assert result.handled is True
    payload = json.loads(result.message)
    ingest = dict(payload["ingest"])
    panel = dict(payload["payload"])

    assert ingest["found"] == 1
    assert ingest["written"] == 1
    selected = dict(panel["selected_report"])
    analysis = dict(panel["selected_analysis"])
    assert str(selected.get("report_ref") or "").startswith("helpcenter/reports/")
    assert str(selected.get("json_ref") or "").startswith("helpcenter/reports/")
    assert analysis["no_auto_fix"] is True

    index_path = tmp_path / "helpcenter/index/helpcenter.index.json"
    assert index_path.exists()
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(list(index_payload.get("reports") or [])) >= 1

    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files
    assert all(str(path.relative_to(tmp_path)).startswith("helpcenter/") for path in files)

    opened = execute_command(":helpcenter", result.state)
    shell = render_operator_shell(opened.state.with_updates(section_id="artifacts"), width=170, height=44)
    assert "Helpcenter" in shell
    assert "Reports: 1" in shell
