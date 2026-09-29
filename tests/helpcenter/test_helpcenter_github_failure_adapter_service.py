from __future__ import annotations

from typing import Any

from agent.services.helpcenter_github_failure_adapter_service import GithubWorkflowFailureAdapter
from agent.services.helpcenter_source_adapter_service import scan_source_adapter


class _FakeGithubApi:
    def list_failed_workflow_runs(self, *, owner: str, repo: str, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "run_id": 101,
                "workflow_name": "CI",
                "branch": "main",
                "commit_sha": "abcdef1234567890",
                "conclusion": "failure",
                "html_url": "https://example/runs/101",
                "updated_at": "2026-05-26T22:00:00Z",
            }
        ][:limit]

    def list_failed_workflow_jobs(self, *, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        return [
            {
                "job_id": 9001,
                "job_name": "test",
                "conclusion": "failure",
                "html_url": "https://example/runs/101/jobs/9001",
            }
        ]


def test_github_failure_adapter_reads_runs_and_jobs_and_normalizes() -> None:
    adapter = GithubWorkflowFailureAdapter(owner="acme", repo="rocket", api_client=_FakeGithubApi())
    rows = adapter.list_messages(limit=10)
    assert rows
    normalized = adapter.normalize_message(rows[0])
    assert normalized["source_kind"] == "github_workflow_failure"
    assert normalized["meta"]["run_id"] == 101
    assert normalized["meta"]["job_id"] == 9001


def test_github_failure_adapter_scan_report_is_valid_message_payload() -> None:
    adapter = GithubWorkflowFailureAdapter(owner="acme", repo="rocket", api_client=_FakeGithubApi())
    report = scan_source_adapter(adapter, limit=10)
    assert report["ok"] is True
    assert report["messages"]
    assert report["messages"][0]["source_kind"] == "github_workflow_failure"


def test_github_failure_adapter_normalized_payload_omits_credentials() -> None:
    adapter = GithubWorkflowFailureAdapter(owner="acme", repo="rocket", api_client=_FakeGithubApi())
    rows = adapter.list_messages(limit=1)
    payload = adapter.normalize_message(rows[0])
    blob = str(payload)
    assert "token" not in blob.lower()
    assert "password" not in blob.lower()


def test_github_failure_adapter_respects_limit() -> None:
    adapter = GithubWorkflowFailureAdapter(owner="acme", repo="rocket", api_client=_FakeGithubApi())
    rows = adapter.list_messages(limit=1)
    assert len(rows) == 1
