from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from worker.shell.command_policy import classify_command


def _needs_approval(policy_decision: str, command_requires_approval: bool) -> bool:
    normalized = str(policy_decision or "").strip().lower()
    return normalized in {"approval_required", "default_deny"} or bool(command_requires_approval)


def _coerce_timestamp(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _validate_approval_freshness(approval: dict[str, Any]) -> None:
    now_ts = time.time()
    expiry_ts = _coerce_timestamp(approval.get("expires_at") or approval.get("valid_until") or approval.get("expiry"))
    if expiry_ts is not None and now_ts >= expiry_ts:
        raise PermissionError("approval_expired")
    approved_at_ts = _coerce_timestamp(approval.get("approved_at") or approval.get("issued_at"))
    max_age_raw = approval.get("max_age_seconds")
    if approved_at_ts is not None and max_age_raw is not None:
        max_age = float(max_age_raw)
        if max_age < 0:
            raise PermissionError("approval_max_age_invalid")
        if (now_ts - approved_at_ts) > max_age:
            raise PermissionError("approval_stale")


def _validate_approval_binding(
    *,
    approval: dict[str, Any] | None,
    command_plan_artifact: dict[str, Any],
    task_id: str,
    capability_id: str,
    context_hash: str,
) -> None:
    if not isinstance(approval, dict):
        raise PermissionError("approval_required")
    if str(approval.get("status") or "").strip().lower() != "approved":
        raise PermissionError("approval_not_approved")
    command = str(command_plan_artifact.get("command") or "").strip()
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    expected_bindings = {
        "task_id": str(task_id).strip(),
        "capability_id": str(capability_id).strip(),
        "context_hash": str(context_hash).strip(),
        "command_hash": command_hash,
    }
    for key, expected in expected_bindings.items():
        if str(approval.get(key) or "").strip() != expected:
            raise PermissionError(f"approval_binding_mismatch:{key}")
    _validate_approval_freshness(approval)


def _resolve_cwd(repository_root: Path, working_directory: str) -> Path:
    root = repository_root.resolve()
    candidate = (root / working_directory).resolve()
    if candidate != root and root not in candidate.parents:
        raise PermissionError("working_directory_outside_workspace")
    return candidate


def _bounded_environment(environment: dict[str, str] | None) -> dict[str, str]:
    bounded: dict[str, str] = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
    }
    for key, value in dict(environment or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        bounded[normalized_key] = str(value)
    return bounded


def execute_command_plan(
    *,
    repository_root: Path,
    command_plan_artifact: dict[str, Any],
    task_id: str,
    capability_id: str,
    context_hash: str,
    shell_policy: dict[str, Any],
    hub_policy_decision: str,
    approval: dict[str, Any] | None = None,
    timeout_seconds: int = 120,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    command = str(command_plan_artifact.get("command") or "").strip()
    if not command:
        raise ValueError("command_missing")
    command_decision = classify_command(command=command, policy=shell_policy, hub_policy_decision=hub_policy_decision)
    if command_decision.classification == "denied":
        raise PermissionError("policy_denied")
    if _needs_approval(hub_policy_decision, command_decision.required_approval):
        _validate_approval_binding(
            approval=approval,
            command_plan_artifact=command_plan_artifact,
            task_id=task_id,
            capability_id=capability_id,
            context_hash=context_hash,
        )
    cwd = _resolve_cwd(repository_root, str(command_plan_artifact.get("working_directory") or "."))
    start = time.monotonic()
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=int(timeout_seconds),
            check=False,
            env=_bounded_environment(environment),
        )
        status = "passed" if completed.returncode == 0 else "failed"
        exit_code = int(completed.returncode)
        stdout_value = completed.stdout or ""
        stderr_value = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        status = "degraded"
        exit_code = 124
        stdout_value = str(exc.stdout or "")
        stderr_value = (str(exc.stderr or "") + "\ncommand_timed_out").strip()
    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "schema": "test_result_artifact.v1",
        "task_id": str(task_id).strip(),
        "command": command,
        "exit_code": exit_code,
        "status": status,
        "stdout_ref": stdout_value,
        "stderr_ref": stderr_value,
        "output_summary": (
            f"Execution status={status}, duration_ms={duration_ms}, "
            f"working_directory={str(cwd.relative_to(repository_root.resolve()) or '.')}, "
            f"environment_keys={','.join(sorted(_bounded_environment(environment).keys()))}"
        ),
        "failure_hints": [],
    }
