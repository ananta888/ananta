from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


def _run_git(args: list[str], *, cwd: Path, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        input=input_text,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout or completed.stderr or "").strip() or f"git {' '.join(args)} failed")
    return completed.stdout or ""


def _approval_required(policy_decision: str) -> bool:
    normalized = str(policy_decision or "").strip().lower()
    return normalized in {"approval_required", "default_deny"}


def _validate_patch_artifact(patch_artifact: dict[str, Any]) -> None:
    patch = str(patch_artifact.get("patch") or "")
    expected_hash = str(patch_artifact.get("patch_hash") or "").strip()
    if not patch or not expected_hash:
        raise ValueError("patch_or_hash_missing")
    actual_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("patch_hash_mismatch")


def _validate_approval_binding(
    *,
    approval: dict[str, Any] | None,
    patch_artifact: dict[str, Any],
    task_id: str,
    capability_id: str,
    context_hash: str,
) -> None:
    if not isinstance(approval, dict):
        raise PermissionError("approval_required")
    if str(approval.get("status") or "").strip().lower() != "approved":
        raise PermissionError("approval_not_approved")
    expected_bindings = {
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "context_hash": str(context_hash).strip(),
        "patch_hash": str(patch_artifact.get("patch_hash") or "").strip(),
    }
    for key, expected in expected_bindings.items():
        if str(approval.get(key) or "").strip() != expected:
            raise PermissionError(f"approval_binding_mismatch:{key}")


def apply_patch_artifact(
    *,
    repository_root: Path,
    patch_artifact: dict[str, Any],
    task_id: str,
    capability_id: str,
    context_hash: str,
    policy_decision: str,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_policy = str(policy_decision or "").strip().lower()
    if normalized_policy == "deny":
        raise PermissionError("policy_denied")
    if _approval_required(normalized_policy):
        _validate_approval_binding(
            approval=approval,
            patch_artifact=patch_artifact,
            task_id=task_id,
            capability_id=capability_id,
            context_hash=context_hash,
        )
    _validate_patch_artifact(patch_artifact)
    patch = str(patch_artifact.get("patch") or "")
    repo = repository_root.resolve()
    _run_git(["apply", "--whitespace=nowarn", "-"], cwd=repo, input_text=patch)
    status_lines = _run_git(["status", "--porcelain"], cwd=repo)
    changed_files = [_status_path(line) for line in status_lines.splitlines() if line.strip()]
    return {
        "status": "applied",
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "patch_hash": str(patch_artifact.get("patch_hash") or "").strip(),
        "changed_files": changed_files,
        "artifact_refs": [f"patch:{str(patch_artifact.get('patch_hash') or '').strip()}"],
    }


def _status_path(line: str) -> str:
    if len(line) >= 4 and line[2] == " ":
        return line[3:].strip()
    if len(line) >= 3:
        return line[2:].strip()
    return line.strip()
