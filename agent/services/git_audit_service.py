from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agent.common.audit import log_audit


def git_workspace_fingerprint(workspace_dir: str | Path) -> str:
    """Return a stable opaque workspace identifier without logging its path."""

    resolved = str(Path(workspace_dir).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def record_git_activity(
    event: str,
    *,
    workspace_dir: str | Path,
    operation: str,
    outcome: str,
    branch: str = "",
    task_id: str | None = None,
    commit_sha: str = "",
    summary: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Write normalized, credential-free Git provenance to the hub audit log."""

    details: dict[str, Any] = {
        "workspace_fingerprint": git_workspace_fingerprint(workspace_dir),
        "operation": str(operation or "git")[:80],
        "outcome": str(outcome or "unknown")[:80],
        "branch": str(branch or "")[:240],
        "task_id": str(task_id or ""),
        "commit_sha": str(commit_sha or "")[:40],
        "summary": " ".join(str(summary or "").split())[:240],
    }
    for key, value in dict(extra or {}).items():
        if key not in {"workspace_path", "remote_url", "credentials", "token", "password"}:
            details[str(key)] = value
    log_audit(str(event or "git_activity"), details)
