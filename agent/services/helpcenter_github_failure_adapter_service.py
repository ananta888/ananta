from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from agent.services.helpcenter_contract_service import validate_helpcenter_message
from agent.services.helpcenter_source_adapter_service import HelpcenterSourceAdapter


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class GithubWorkflowApiClient(Protocol):
    def list_failed_workflow_runs(self, *, owner: str, repo: str, limit: int = 20) -> list[dict[str, Any]]:
        ...

    def list_failed_workflow_jobs(self, *, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        ...


class GithubWorkflowFailureAdapter(HelpcenterSourceAdapter):
    adapter_id = "github_workflow_failure"

    def __init__(self, *, owner: str, repo: str, api_client: GithubWorkflowApiClient) -> None:
        self._owner = str(owner).strip()
        self._repo = str(repo).strip()
        self._api = api_client

    def list_messages(self, *, limit: int = 50) -> list[dict[str, Any]]:
        runs = self._api.list_failed_workflow_runs(owner=self._owner, repo=self._repo, limit=max(1, int(limit)))
        messages: list[dict[str, Any]] = []
        for run in runs:
            run_id = int(run.get("run_id") or 0)
            if run_id <= 0:
                continue
            jobs = self._api.list_failed_workflow_jobs(owner=self._owner, repo=self._repo, run_id=run_id)
            if not jobs:
                messages.append({"run": dict(run), "job": {}})
                continue
            for job in jobs:
                messages.append({"run": dict(run), "job": dict(job)})
        return messages[: max(1, int(limit))]

    def fetch_message_detail(self, source_ref: str) -> dict[str, Any]:
        return {"source_ref": str(source_ref).strip()}

    def normalize_message(self, raw_message: dict[str, Any]) -> dict[str, Any]:
        run = dict(raw_message.get("run") or {})
        job = dict(raw_message.get("job") or {})
        run_id = int(run.get("run_id") or 0)
        job_id = int(job.get("job_id") or 0)
        workflow_name = str(run.get("workflow_name") or "workflow").strip() or "workflow"
        branch = str(run.get("branch") or "").strip()
        commit_sha = str(run.get("commit_sha") or "").strip()
        conclusion = str(job.get("conclusion") or run.get("conclusion") or "failed").strip() or "failed"
        job_name = str(job.get("job_name") or "").strip()
        source_ref = f"github://{self._owner}/{self._repo}/runs/{run_id}"
        if job_id > 0:
            source_ref = f"{source_ref}/jobs/{job_id}"
        title_suffix = f" / {job_name}" if job_name else ""
        summary = (
            f"workflow={workflow_name} "
            f"branch={branch or '-'} "
            f"sha={commit_sha[:12] or '-'} "
            f"conclusion={conclusion}"
        )
        payload = {
            "message_id": f"gh-{self._owner}-{self._repo}-{run_id}-{job_id or 'run'}",
            "source_kind": "github_workflow_failure",
            "source_ref": source_ref,
            "received_at": str(run.get("updated_at") or _now_iso()),
            "title": f"{workflow_name}{title_suffix} failed",
            "severity": "error",
            "normalized_summary": summary,
            "labels": ["github", "workflow", "failure"],
            "privacy_class": "internal",
            "redaction_status": "not_required",
            "raw_ref": str(job.get("html_url") or run.get("html_url") or "").strip(),
            "meta": {
                "workflow_name": workflow_name,
                "run_id": run_id,
                "job_id": job_id,
                "job_name": job_name,
                "branch": branch,
                "commit_sha": commit_sha,
                "conclusion": conclusion,
                "url": str(job.get("html_url") or run.get("html_url") or "").strip(),
            },
        }
        issues = validate_helpcenter_message(payload)
        if issues:
            raise ValueError(f"github_failure_normalization_invalid:{issues[0]['reason_code']}")
        return payload
