from __future__ import annotations

from typing import Any

from agent.services.helpcenter_analyzer_service import analyze_helpcenter_message
from agent.services.helpcenter_github_failure_adapter_service import (
    GithubWorkflowApiClient,
    GithubWorkflowFailureAdapter,
)
from agent.services.helpcenter_report_writer_service import write_helpcenter_report


class StaticGithubWorkflowApiClient(GithubWorkflowApiClient):
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = [dict(item) for item in list(rows or []) if isinstance(item, dict)]

    def list_failed_workflow_runs(self, *, owner: str, repo: str, limit: int = 20) -> list[dict[str, Any]]:
        if self._rows:
            return [
                dict(item.get("run") or {})
                for item in self._rows[: max(1, int(limit))]
                if isinstance(item.get("run"), dict)
            ]
        return [
            {
                "run_id": 1,
                "workflow_name": "ci",
                "branch": "main",
                "commit_sha": "0000000000000000",
                "conclusion": "failure",
                "html_url": f"https://github.com/{owner}/{repo}/actions/runs/1",
                "updated_at": "2026-05-27T00:00:00Z",
            }
        ]

    def list_failed_workflow_jobs(self, *, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        if self._rows:
            for item in self._rows:
                run = dict(item.get("run") or {})
                if int(run.get("run_id") or 0) == int(run_id):
                    jobs = [dict(job) for job in list(item.get("jobs") or []) if isinstance(job, dict)]
                    return jobs
            return []
        return [
            {
                "job_id": 1,
                "job_name": "tests",
                "conclusion": "failure",
                "html_url": f"https://github.com/{owner}/{repo}/actions/runs/{run_id}/job/1",
                "log_excerpt": "FAILURES\nAssertionError",
            }
        ]


def ingest_github_failures(
    *,
    repo: str,
    limit: int = 5,
    dry_run: bool = False,
    repo_root: str | None = None,
    api_client: GithubWorkflowApiClient | None = None,
) -> dict[str, Any]:
    owner_repo = str(repo).strip()
    if "/" not in owner_repo:
        raise ValueError("helpcenter_ingest_repo_must_be_owner_slash_repo")
    owner, repo_name = owner_repo.split("/", 1)
    resolved_limit = max(1, int(limit))
    client = api_client or StaticGithubWorkflowApiClient()
    adapter = GithubWorkflowFailureAdapter(owner=owner, repo=repo_name, api_client=client)
    raw_rows = adapter.list_messages(limit=resolved_limit)
    items: list[dict[str, Any]] = []
    written = 0
    for row in raw_rows[:resolved_limit]:
        normalized = adapter.normalize_message(dict(row))
        log_text = str(
            dict(row.get("job") or {}).get("log_excerpt")
            or dict(row.get("run") or {}).get("log_excerpt")
            or ""
        )
        analysis = analyze_helpcenter_message(normalized, log_text=log_text)
        if dry_run:
            items.append(
                {
                    "message_id": normalized.get("message_id"),
                    "source_ref": normalized.get("source_ref"),
                    "title": normalized.get("title"),
                    "would_write": True,
                }
            )
            continue
        written_payload = write_helpcenter_report(message=normalized, analysis=analysis, repo_root=repo_root)
        written += 1
        items.append(written_payload)
    return {
        "repo": owner_repo,
        "found": len(raw_rows[:resolved_limit]),
        "written": written,
        "dry_run": bool(dry_run),
        "items": items,
    }
