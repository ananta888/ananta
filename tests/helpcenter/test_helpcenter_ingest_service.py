from __future__ import annotations

import json
from pathlib import Path

from agent.services.helpcenter_ingest_service import StaticGithubWorkflowApiClient, ingest_github_failures


def _rows() -> list[dict]:
    return [
        {
            "run": {
                "run_id": 101,
                "workflow_name": "CI",
                "branch": "main",
                "commit_sha": "abcdef123456",
                "conclusion": "failure",
                "html_url": "https://github.com/acme/repo/actions/runs/101",
                "updated_at": "2026-05-27T00:00:00Z",
            },
            "jobs": [
                {
                    "job_id": 9001,
                    "job_name": "tests",
                    "conclusion": "failure",
                    "html_url": "https://github.com/acme/repo/actions/runs/101/job/9001",
                    "log_excerpt": (
                        "FAILURES\n"
                        "AssertionError: boom\n"
                        "token=supersecret\n"
                        "https://alice:secret@example.com/repo\n"
                        "-----BEGIN PRIVATE KEY-----\n"
                    ),
                }
            ],
        },
        {
            "run": {
                "run_id": 102,
                "workflow_name": "CI",
                "branch": "main",
                "commit_sha": "bbbbbb123456",
                "conclusion": "failure",
                "html_url": "https://github.com/acme/repo/actions/runs/102",
                "updated_at": "2026-05-27T00:00:00Z",
            },
            "jobs": [
                {
                    "job_id": 9002,
                    "job_name": "lint",
                    "conclusion": "failure",
                    "html_url": "https://github.com/acme/repo/actions/runs/102/job/9002",
                    "log_excerpt": "error: lint failure",
                }
            ],
        },
    ]


def test_ingest_github_failures_dry_run_respects_limit() -> None:
    result = ingest_github_failures(
        repo="acme/repo",
        limit=1,
        dry_run=True,
        api_client=StaticGithubWorkflowApiClient(rows=_rows()),
    )
    assert result["dry_run"] is True
    assert result["found"] == 1
    assert result["written"] == 0
    assert len(result["items"]) == 1
    assert result["items"][0]["would_write"] is True


def test_ingest_github_failures_writes_helpcenter_only_and_keeps_analysis_guardrail(tmp_path: Path) -> None:
    result = ingest_github_failures(
        repo="acme/repo",
        limit=1,
        dry_run=False,
        repo_root=str(tmp_path),
        api_client=StaticGithubWorkflowApiClient(rows=_rows()),
    )
    assert result["written"] == 1
    item = result["items"][0]
    payload = json.loads((tmp_path / item["json_ref"]).read_text(encoding="utf-8"))
    assert payload["no_auto_fix"] is True
    assert payload["redaction_status"] == "redacted"
    assert payload["workflow_run_id"] == 101
    assert payload["job_id"] == 9001
    assert payload["content_hash"]
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files
    assert all(str(path.relative_to(tmp_path)).startswith("helpcenter/") for path in files)
