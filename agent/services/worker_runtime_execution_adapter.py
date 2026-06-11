"""HDW-002/HDW-004: execution plane for hub-direct tool calls.

The hub is control plane only (HDW-DD-001): it decides, authorizes and
audits, but never runs tool/shell/workspace logic in its own process.
This adapter is the single dispatch seam between the two planes. The
control plane (``agent/services/hub_tool_execution_adapter.py``) hands a
fully policy-checked call to ``dispatch``; the configured runtime
executes it inside an explicit workspace scope and returns an
``ananta_tool_result.v1`` payload.

Relationship to existing runtime infrastructure (HDW-002 decision):
``NativeWorkerRuntimeService`` stays the command-plan runtime for
worker-LLM flows; this adapter is the tool-execution plane. The default
backend is ``LocalProcessWorkerRuntime`` — a task-scoped local-process
runtime that reuses the deterministic tool executors the worker tool
loop already runs in (``agent/services/tools``). Remote/docker targets
from ``worker_runtime_target_service`` can be plugged in as additional
backends without changing the control plane.

Contract: ``docs/contracts/hub-direct-execution.md``.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Protocol

from agent.common.audit import AUDIT_WORKER_RUNTIME_DISPATCH, audit_hub_direct_event
from agent.services.tools._evidence import build_tool_result

_DEFAULT_MAX_RESULT_CHARS = 8000

# HDW-004: hub config keys that must never reach the runtime config.
_SENSITIVE_CONFIG_MARKERS = ("key", "secret", "token", "password", "credential")


class WorkerRuntime(Protocol):
    """One execution-plane backend (local process, sandbox, remote)."""

    runtime_kind: str

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_dir: str,
        tool_call_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]: ...


class LocalProcessWorkerRuntime:
    """Default execution plane: task-scoped local-process runtime.

    Reuses the deterministic tool executors of the worker tool loop and
    routes ``custom.*``/``project.*`` tools to the sandboxed
    ``CustomToolExecutor``. This is the same execution boundary the
    worker tool loop runs in today; it is *not* the hub control plane —
    the adapter is the only entry and enforces the workspace scope.
    """

    runtime_kind = "local_process"

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_dir: str,
        tool_call_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(tool_name or "").strip()
        if name.startswith("custom.") or name.startswith("project."):
            from agent.services.custom_tool_executor import execute_custom_tool

            return execute_custom_tool(
                tool_name=name,
                arguments=arguments,
                workspace_dir=workspace_dir,
                tool_call_id=tool_call_id,
                config=config,
            )
        from agent.services.tools import execute_ananta_tool

        return execute_ananta_tool(
            tool_name=name,
            arguments=arguments,
            workspace_dir=workspace_dir,
            tool_call_id=tool_call_id,
            config=config,
        )


class ConfiguredWorkerRuntimeUnavailable:
    """Fail-closed placeholder for configured non-local runtime targets.

    Docker/remote transports are selected through the existing runtime
    target contracts, but this adapter must not silently fall back to
    local hub-process execution when that transport is not implemented
    or unavailable.
    """

    def __init__(self, runtime_kind: str, runtime_target_id: str) -> None:
        self.runtime_kind = runtime_kind
        self._runtime_target_id = runtime_target_id

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_dir: str,
        tool_call_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return _error_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            error=f"worker_runtime_backend_unavailable:{self.runtime_kind}:{self._runtime_target_id}",
        )


def _error_result(*, tool_name: str, tool_call_id: str, error: str) -> dict[str, Any]:
    return build_tool_result(tool_name=tool_name, tool_call_id=tool_call_id, status="error", error=error)


class WorkerRuntimeExecutionAdapter:
    """Dispatches one policy-checked tool call into a worker runtime.

    HDW-004 boundaries enforced here, independent of the backend:
    - ``workspace_ref`` must be explicit; there is no fallback to the
      hub working directory.
    - The workspace path is normalized and must be an existing
      directory.
    - Hub config is sanitized before it reaches the runtime; only
      ``env_allowlist`` names may pass environment values through.
    - Results are bounded to ``max_result_chars``.
    """

    def __init__(self, runtime: WorkerRuntime | None = None) -> None:
        self._runtime = runtime

    def dispatch(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        task_id: str | None = None,
        goal_id: str | None = None,
        workspace_ref: str | None = None,
        mutation_mode: str = "read_only",
        policy_decision: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        config: dict[str, Any] | None = None,
        audit_enabled: bool = True,
    ) -> dict[str, Any]:
        name = str(tool_name or "").strip()
        call_id = str(tool_call_id or f"hub-direct-{uuid.uuid4().hex[:12]}")
        decision = dict(policy_decision or {})
        if str(decision.get("decision") or "") != "allow":
            # Defense in depth: the control plane must never dispatch an
            # unapproved call; refuse instead of trusting the caller.
            return _error_result(tool_name=name, tool_call_id=call_id, error="dispatch_without_allow_decision")

        workspace_dir = self._resolve_workspace(workspace_ref)
        if workspace_dir is None:
            return _error_result(tool_name=name, tool_call_id=call_id, error="missing_or_invalid_workspace_ref")

        runtime_config = self._sanitize_runtime_config(config)
        runtime_config["mutation_mode"] = str(mutation_mode or "read_only")
        max_chars = int(runtime_config.get("max_result_chars") or _DEFAULT_MAX_RESULT_CHARS)

        runtime = self._runtime or self._runtime_from_config(runtime_config)

        if audit_enabled:
            audit_hub_direct_event(
                AUDIT_WORKER_RUNTIME_DISPATCH,
                tool_name=name,
                policy_decision=str(decision.get("decision") or "allow"),
                risk_class=str(decision.get("risk_class") or "unknown"),
                task_id=task_id,
                goal_id=goal_id,
                runtime_kind=getattr(runtime, "runtime_kind", "unknown"),
                workspace_ref=str(workspace_dir),
                tool_call_id=call_id,
            )

        try:
            result = runtime.execute_tool(
                tool_name=name,
                arguments=dict(arguments or {}),
                workspace_dir=str(workspace_dir),
                tool_call_id=call_id,
                config=runtime_config,
            )
        except Exception as exc:  # runtime bugs must not crash the hub
            return _error_result(tool_name=name, tool_call_id=call_id, error=f"worker_runtime_failed:{exc}")
        if not isinstance(result, dict):
            return _error_result(tool_name=name, tool_call_id=call_id, error="worker_runtime_invalid_result")
        return self._bound_result(result, max_chars)

    @staticmethod
    def _resolve_workspace(workspace_ref: str | None) -> Path | None:
        """HDW-004: explicit, normalized workspace only — never hub cwd."""
        raw = str(workspace_ref or "").strip()
        if not raw:
            return None
        try:
            resolved = Path(raw).resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not resolved.is_dir():
            return None
        return resolved

    @staticmethod
    def _sanitize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
        """Drop hub secrets; pass env values only via env_allowlist."""
        cfg = dict(config or {})
        allowlist = [str(item or "").strip() for item in (cfg.get("env_allowlist") or []) if str(item or "").strip()]
        sanitized: dict[str, Any] = {}
        for key, value in cfg.items():
            normalized = str(key or "").strip().lower()
            if any(marker in normalized for marker in _SENSITIVE_CONFIG_MARKERS):
                continue
            sanitized[key] = value
        sanitized["env_allowlist"] = allowlist
        sanitized["env"] = {name: os.environ[name] for name in allowlist if name in os.environ}
        return sanitized

    @staticmethod
    def _runtime_from_config(config: dict[str, Any]) -> WorkerRuntime:
        target_payload = config.get("worker_runtime_target")
        worker_runtime_cfg = config.get("worker_runtime") if isinstance(config.get("worker_runtime"), dict) else {}
        if not target_payload and isinstance(worker_runtime_cfg, dict):
            target_payload = worker_runtime_cfg.get("hub_direct_execution_target") or worker_runtime_cfg.get("runtime_target")
        if not isinstance(target_payload, dict):
            return LocalProcessWorkerRuntime()
        try:
            from agent.services.worker_runtime_target_service import WorkerRuntimeKind, WorkerRuntimeTargetService

            target = WorkerRuntimeTargetService().from_config(target_payload)
            kind = getattr(target.runtime_kind, "value", str(target.runtime_kind))
            if target.runtime_kind == WorkerRuntimeKind.local_process:
                return LocalProcessWorkerRuntime()
            return ConfiguredWorkerRuntimeUnavailable(kind, target.runtime_target_id)
        except Exception as exc:
            return ConfiguredWorkerRuntimeUnavailable("misconfigured", str(exc)[:120])

    @staticmethod
    def _bound_result(result: dict[str, Any], max_chars: int) -> dict[str, Any]:
        """Enforce the output limit on evidence excerpts (HDW-004)."""
        if max_chars <= 0:
            return result
        evidence = list(result.get("evidence") or [])
        used = 0
        bounded: list[dict[str, Any]] = []
        truncated = False
        for row in evidence:
            entry = dict(row)
            excerpt = str(entry.get("excerpt") or "")
            remaining = max_chars - used
            if remaining <= 0:
                truncated = True
                break
            if len(excerpt) > remaining:
                entry["excerpt"] = excerpt[: max(1, remaining - 12)].rstrip() + "\n[truncated]"
                entry["truncated"] = True
                truncated = True
            used += len(str(entry.get("excerpt") or "")) + 100
            bounded.append(entry)
        if truncated:
            warnings = set(result.get("warnings") or [])
            warnings.add("evidence_truncated")
            result = dict(result, evidence=bounded, warnings=sorted(warnings))
        return result


worker_runtime_execution_adapter = WorkerRuntimeExecutionAdapter()


def get_worker_runtime_execution_adapter() -> WorkerRuntimeExecutionAdapter:
    return worker_runtime_execution_adapter
