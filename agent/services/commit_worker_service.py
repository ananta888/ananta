from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.services.commit_message_validator import get_commit_message_validator
from agent.services.commit_scope_resolver import get_commit_scope_resolver


@dataclass
class CommitResult:
    success: bool
    message: Optional[str] = None
    scope_confirmed: Optional[str] = None
    scope_from_diff: bool = False
    errors: list[str] = field(default_factory=list)


def _run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd)


def _staged_files(cwd: str) -> list[str]:
    result = _run(["git", "diff", "--name-only", "--cached"], cwd)
    if result.returncode != 0:
        return []
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def _build_subject(commit_metadata: dict[str, Any], scope: Optional[str]) -> str:
    hint = str(commit_metadata.get("commit_subject_hint") or "").strip()
    if hint:
        return hint
    return f"update {scope} service" if scope else "update"


def _build_message(commit_type: str, scope: Optional[str], subject: str) -> str:
    if scope:
        return f"{commit_type}({scope}): {subject}"
    return f"{commit_type}: {subject}"


class CommitWorkerService:
    def execute(self, task: dict[str, Any], repo_path: str) -> CommitResult:
        cwd = str(Path(repo_path).resolve())
        meta = dict((task or {}).get("commit_metadata") or {})
        commit_type = str(meta.get("commit_type") or "chore").strip().lower()

        staged = _staged_files(cwd)
        if not staged:
            return CommitResult(success=True, message=None)

        resolver = get_commit_scope_resolver()
        resolution = resolver.resolve(staged)
        policy = dict(meta.get("policy") or {})
        block_mixed_scope = bool(policy.get("block_mixed_scope", True))
        if resolution.is_mixed and block_mixed_scope:
            return CommitResult(
                success=False,
                errors=[
                    "mixed_scope_blocked",
                    f"detected_scopes={resolution.all_scopes}",
                ],
            )

        planned_scope = str(meta.get("commit_scope") or "").strip() or None
        actual_scope = resolution.primary_scope
        scope_from_diff = actual_scope is not None and actual_scope != planned_scope
        final_scope = actual_scope if actual_scope is not None else planned_scope

        subject = _build_subject(meta, final_scope)
        message = _build_message(commit_type, final_scope, subject)

        validator = get_commit_message_validator()
        result = validator.validate(message)
        if not result.valid:
            return CommitResult(success=False, errors=result.errors)

        proc = _run(["git", "commit", "-m", message], cwd)
        if proc.returncode != 0:
            return CommitResult(
                success=False,
                errors=[proc.stderr.strip() or f"git commit failed (rc={proc.returncode})"],
            )

        return CommitResult(
            success=True,
            message=message,
            scope_confirmed=final_scope,
            scope_from_diff=scope_from_diff,
        )


_SERVICE = CommitWorkerService()


def get_commit_worker_service() -> CommitWorkerService:
    return _SERVICE
