from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from worker.core.redaction import sanitize_subprocess_environment
from worker.core.tool_registry import ResourceLimits, ToolInvocationEnvelope, ToolResult, WorkerToolRegistry
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
    sanitized = sanitize_subprocess_environment(dict(environment or {}), explicitly_allowed_sensitive_keys=set())
    for key, value in sanitized.items():
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
    execution_profile: str | None = "balanced",
    tool_registry: WorkerToolRegistry | None = None,  # T009
    execution_id: str | None = None,                  # T008
) -> ToolResult:  # T010: returns ToolResult instead of flat dict
    command = str(command_plan_artifact.get("command") or "").strip()
    if not command:
        raise ValueError("command_missing")

    exec_id = str(execution_id or uuid.uuid4())

    # T009: Gate through WorkerToolRegistry — run_shell must be registered
    if tool_registry is not None and not tool_registry.is_registered("run_shell"):
        return ToolResult.denied("run_shell", exec_id, "tool_not_registered")

    # T008: Build ToolInvocationEnvelope to carry resource limits per call
    registry_limits = (
        tool_registry.get("run_shell").resource_limits
        if tool_registry and tool_registry.get("run_shell")
        else ResourceLimits()
    )
    effective_timeout = min(float(timeout_seconds), registry_limits.timeout_seconds)
    invocation = ToolInvocationEnvelope(
        execution_id=exec_id,
        tool_id="run_shell",
        arguments={
            "command": command,
            "cwd": str(command_plan_artifact.get("working_directory") or "."),
        },
        capability_ref=str(capability_id),
        resource_limits=ResourceLimits(
            timeout_seconds=effective_timeout,
            max_output_chars=registry_limits.max_output_chars,
            max_artifact_bytes=registry_limits.max_artifact_bytes,
            max_files_touched=registry_limits.max_files_touched,
        ),
    )

    command_decision = classify_command(
        command=command,
        policy=shell_policy,
        hub_policy_decision=hub_policy_decision,
        execution_profile=execution_profile,
    )
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
            timeout=invocation.resource_limits.timeout_seconds,  # T008: from envelope
            check=False,
            env=_bounded_environment(environment),
        )
        raw_stdout = completed.stdout or ""
        raw_stderr = completed.stderr or ""
        exit_code = int(completed.returncode)
        # T008: apply output limit from ToolInvocationEnvelope
        stdout_val, truncated = invocation.apply_output_limit(raw_stdout)
        stderr_val, _ = invocation.apply_output_limit(raw_stderr)
    except subprocess.TimeoutExpired as exc:
        return ToolResult.timeout("run_shell", exec_id, partial_stdout=str(exc.stdout or ""))
    duration_s = time.monotonic() - start
    # T010: return ToolResult (callers use .to_test_result_artifact() for legacy dict format)
    return ToolResult(
        tool_id="run_shell",
        execution_id=exec_id,
        success=exit_code == 0,
        stdout=stdout_val or "<empty>",
        stderr=stderr_val or "<empty>",
        exit_code=exit_code,
        truncated=truncated,
        duration_seconds=round(duration_s, 3),
    )
