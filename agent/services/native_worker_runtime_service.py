"""Hub compatibility facade for the delegated Native Worker runtime.

The Hub may prepare a bounded command-plan contract, but it must never import
or execute Worker implementations in the Flask process.  Execution methods are
kept for API compatibility and fail closed with an explicit delegation result.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_ALLOWLIST = (
    "cat",
    "echo",
    "git",
    "grep",
    "head",
    "id",
    "ls",
    "pwd",
    "pytest",
    "python",
    "tail",
    "wc",
    "whoami",
)
_DEFAULT_APPROVAL_REQUIRED = ("chmod", "chown", "mv", "rm", "sudo")
_DEFAULT_DENYLIST = ("rm -rf /", ":(){", "mkfs")


def _build_context_hash(*, context_bundle_id: str, task_id: str, command: str) -> str:
    source = f"{context_bundle_id}:{task_id}:{command}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def _normalized_profile(value: str | None) -> str:
    profile = str(value or "balanced").strip().lower()
    return profile if profile in {"fast", "balanced", "thorough"} else "balanced"


@dataclass(frozen=True)
class _CompatibilityResourceLimits:
    timeout_seconds: float = 30.0
    max_output_chars: int = 32_000
    max_artifact_bytes: int = 10 * 1024 * 1024
    max_files_touched: int = 50


class _CompatibilityResourceEnforcer:
    """Read-only compatibility projection; it performs no Worker execution."""

    _limits = _CompatibilityResourceLimits()

    def limits_for(self, tool_id: str) -> _CompatibilityResourceLimits:
        del tool_id
        return self._limits

    def effective_timeout(self, tool_id: str, requested_seconds: float) -> float:
        del tool_id
        return min(max(0.001, float(requested_seconds)), self._limits.timeout_seconds)

    def bound_output(self, raw: str, tool_id: str) -> tuple[str, bool]:
        del tool_id
        text = str(raw or "")
        if len(text) <= self._limits.max_output_chars:
            return text, False
        return text[: self._limits.max_output_chars], True


_RESOURCE_ENFORCER = _CompatibilityResourceEnforcer()


@dataclass(frozen=True)
class AiSnakeProviderConfig:
    """Compatibility DTO; provider selection remains a Worker responsibility."""

    provider_preference: str = "lmstudio"
    model: str = "ananta-smoke"
    max_latency_ms: int = 2000
    budgets: dict[str, Any] = field(default_factory=dict)
    cloud_allowed: bool = False
    allowed_providers: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "AiSnakeProviderConfig":
        source = dict(payload or {})
        return cls(
            provider_preference=(
                str(source.get("provider_preference") or "lmstudio").strip()
                or "lmstudio"
            ),
            model=str(source.get("model") or "ananta-smoke").strip() or "ananta-smoke",
            max_latency_ms=max(250, int(source.get("max_latency_ms") or 2000)),
            budgets=dict(source.get("budgets") or {})
            if isinstance(source.get("budgets"), dict)
            else {},
            cloud_allowed=bool(source.get("cloud_allowed", False)),
            allowed_providers=[
                str(item).strip()
                for item in list(source.get("allowed_providers") or [])
                if str(item).strip()
            ],
        )


class NativeWorkerRuntimeService:
    """Prepare contracts in the Hub and reject all in-process execution."""

    @staticmethod
    def _native_runtime_cfg(agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        runtime_cfg = (agent_cfg or {}).get("worker_runtime")
        runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
        native_cfg = runtime_cfg.get("native_worker_runtime")
        return dict(native_cfg) if isinstance(native_cfg, dict) else {}

    def is_enabled(self, *, agent_cfg: dict[str, Any] | None) -> bool:
        return bool(self._native_runtime_cfg(agent_cfg).get("enabled", False))

    def fallback_backend(self, *, agent_cfg: dict[str, Any] | None) -> str:
        value = str(
            self._native_runtime_cfg(agent_cfg).get("fallback_backend") or "sgpt"
        ).strip().lower()
        return value or "sgpt"

    def shell_policy(self, *, agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        native_cfg = self._native_runtime_cfg(agent_cfg)
        configured = native_cfg.get("shell_policy")
        configured = configured if isinstance(configured, dict) else {}

        def values(key: str, fallback: tuple[str, ...]) -> list[str]:
            return sorted(
                {
                    str(item).strip()
                    for item in list(configured.get(key) or fallback)
                    if str(item).strip()
                }
            )

        return {
            "allowlist": values("allowlist", _DEFAULT_ALLOWLIST),
            "approval_required_commands": values(
                "approval_required_commands",
                _DEFAULT_APPROVAL_REQUIRED,
            ),
            "denylist_tokens": values("denylist_tokens", _DEFAULT_DENYLIST),
        }

    def ai_snake_provider_config(
        self,
        *,
        agent_cfg: dict[str, Any] | None,
    ) -> AiSnakeProviderConfig:
        runtime_cfg = (agent_cfg or {}).get("worker_runtime")
        runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
        raw = runtime_cfg.get("ai_snake")
        return AiSnakeProviderConfig.from_mapping(raw if isinstance(raw, dict) else {})

    def prepare_native_command_plan(
        self,
        *,
        tid: str,
        task: dict[str, Any],
        command: str | None,
        reason: str | None,
        worker_profile: str | None,
        profile_source: str | None,
        trace_id: str | None,
        context_bundle_id: str | None,
        agent_cfg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not self.is_enabled(agent_cfg=agent_cfg):
            return {
                "runtime_path": "delegated_worker_not_configured",
                "policy_classification_summary": "denied:native_worker_not_configured",
                "worker_context_updates": {},
            }

        normalized_command = str(command or "").strip()
        if not normalized_command:
            return {
                "runtime_path": "native_worker_pipeline",
                "policy_classification_summary": "no_command",
                "worker_context_updates": {
                    "native_runtime": {
                        "runtime_path": "native_worker_pipeline",
                        "mode": "command_plan_skipped",
                    }
                },
            }

        profile = _normalized_profile(worker_profile)
        source = str(profile_source or "agent_default").strip().lower() or "agent_default"
        bundle_id = str(
            context_bundle_id or task.get("context_bundle_id") or f"ctx-{tid}"
        ).strip()
        context_hash = _build_context_hash(
            context_bundle_id=bundle_id,
            task_id=tid,
            command=normalized_command,
        )
        classification, policy_reason, risk, approval_required = self._classify_plan(
            normalized_command,
            self.shell_policy(agent_cfg=agent_cfg),
        )
        command_plan = {
            "schema": "command_plan_artifact.v1",
            "task_id": str(tid).strip(),
            "capability_id": "shell_plan",
            "command": normalized_command,
            "command_hash": hashlib.sha256(
                normalized_command.encode("utf-8")
            ).hexdigest(),
            "explanation": str(reason or "Native Worker command plan.").strip(),
            "risk_classification": risk,
            "required_approval": approval_required,
            "working_directory": ".",
            "expected_effects": [
                "Delegate bounded execution to an authenticated Worker container."
            ],
        }
        ingress_request = self._build_request_payload(
            tid=tid,
            goal_id=str(task.get("goal_id") or "goal-unknown"),
            trace_id=str(trace_id or f"native-plan-{tid}"),
            capability_id="shell_plan",
            mode="command_plan",
            context_bundle_id=bundle_id,
            context_hash=context_hash,
            policy_decision_ref={
                "decision_id": f"native-plan-{tid}",
                "decision": "allow",
                "policy_version": "native_worker_runtime_v1",
            },
            worker_profile=profile,
            profile_source=source,
            requested_outputs=["command_plan_artifact", "trace_metadata"],
        )
        policy_summary = f"{classification}:{policy_reason}"
        return {
            "runtime_path": "native_worker_pipeline",
            "policy_classification_summary": policy_summary,
            "worker_context_updates": {
                "native_runtime": {
                    "schema": "ananta.native-worker-delegation.v1",
                    "runtime_path": "native_worker_pipeline",
                    "mode": "command_plan",
                    "context_hash": context_hash,
                    "ingress_request": ingress_request,
                    "command_plan_artifact": command_plan,
                    "policy_classification_summary": policy_summary,
                    "execution_boundary": "dedicated_worker_container",
                }
            },
        }

    def execute_and_verify_command(
        self,
        *,
        tid: str,
        task: dict[str, Any],
        command: str,
        trace_id: str,
        worker_profile: str | None,
        profile_source: str | None,
        timeout_seconds: int,
        workspace_dir: Path,
        native_runtime_payload: dict[str, Any] | None,
        agent_cfg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del task, command, worker_profile, profile_source, timeout_seconds
        del workspace_dir, native_runtime_payload, agent_cfg
        degraded = {
            "schema": "degraded_state_artifact.v1",
            "state": "worker_delegation_required",
            "machine_reason": "native_worker_in_process_execution_disabled",
            "details": {
                "execution_boundary": "dedicated_worker_container",
                "task_id": str(tid),
            },
        }
        return self._degraded_execution_outcome(
            tid=tid,
            trace_id=trace_id,
            failure_type="runtime_failure",
            degraded=degraded,
            policy_classification_summary="denied:hub_in_process_execution_disabled",
        )

    @staticmethod
    def _classify_plan(
        command: str,
        policy: dict[str, Any],
    ) -> tuple[str, str, str, bool]:
        if any(token in command for token in policy["denylist_tokens"]):
            return "denied", "denylist_token", "critical", True
        try:
            parts = shlex.split(command)
        except ValueError:
            return "unknown", "command_parse_failed", "high", True
        if not parts or re.search(r"(?:&&|\|\||[|;<>])", command):
            return "unknown", "shell_composition_not_allowed", "high", True
        if any(".." in argument for argument in parts[1:]):
            return "denied", "path_escape_detected", "critical", True
        binary = parts[0]
        if binary in set(policy["approval_required_commands"]):
            return "approval_required", "command_requires_approval", "high", True
        if binary in set(policy["allowlist"]):
            return "safe", "command_allowlisted", "low", False
        return "unknown", "command_not_classified", "medium", True

    @staticmethod
    def _degraded_execution_outcome(
        *,
        tid: str,
        trace_id: str,
        failure_type: str,
        degraded: dict[str, Any] | None,
        policy_classification_summary: str,
    ) -> dict[str, Any]:
        payload = dict(degraded or {})
        return {
            "status": "failed",
            "exit_code": 1,
            "failure_type": str(failure_type or "runtime_failure"),
            "output": json.dumps(
                {"trace_id": trace_id, "degraded": payload},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "policy_classification_summary": policy_classification_summary,
            "native_runtime": {
                "runtime_path": "delegated_worker_required",
                "degraded": payload,
            },
            "artifact_refs": [
                {
                    "kind": "native_worker_degraded_state",
                    "task_id": tid,
                    "trace_bundle_ref": "native_worker_runtime:delegation_required",
                }
            ],
            "approval_decision": {
                "classification": "blocked",
                "reason_code": "native_worker_in_process_execution_disabled",
            },
        }

    @staticmethod
    def _build_request_payload(
        *,
        tid: str,
        goal_id: str,
        trace_id: str,
        capability_id: str,
        mode: str,
        context_bundle_id: str,
        context_hash: str,
        policy_decision_ref: dict[str, Any],
        worker_profile: str,
        profile_source: str,
        requested_outputs: list[str],
    ) -> dict[str, Any]:
        return {
            "schema": "worker_execution_request.v1",
            "task_id": str(tid).strip(),
            "goal_id": str(goal_id).strip() or "goal-unknown",
            "trace_id": str(trace_id).strip(),
            "capability_id": str(capability_id).strip(),
            "mode": str(mode).strip(),
            "context_envelope_ref": {
                "context_bundle_id": str(context_bundle_id).strip(),
                "context_hash": str(context_hash).strip(),
                "retrieval_refs": [
                    {
                        "source_id": "task_context",
                        "path": f"tasks/{tid}",
                        "reason": "task_scoped_execution",
                    }
                ],
                "context_chunk_limit": 32,
                "context_byte_limit": 120_000,
            },
            "policy_decision_ref": dict(policy_decision_ref or {}),
            "workspace_constraints_ref": {"constraint_id": f"workspace-{tid}"},
            "worker_profile": str(worker_profile).strip(),
            "profile_source": str(profile_source).strip(),
            "requested_outputs": list(requested_outputs or []),
            "requested_state_on_policy_denied": "degraded",
            "requested_state_on_missing_approval": "degraded",
        }


native_worker_runtime_service = NativeWorkerRuntimeService()


def get_native_worker_runtime_service() -> NativeWorkerRuntimeService:
    return native_worker_runtime_service


def execute_repair_procedure_plan(
    *,
    task_id: str,
    procedure_id: str,
    repair_procedure_dict: dict[str, Any],
    approval_ref: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compatibility endpoint: repairs must be delegated to a Worker."""

    del repair_procedure_dict, approval_ref, dry_run
    return {
        "schema": "ananta.repair-worker-delegation.v1",
        "runtime_path": "delegated_worker_required",
        "status": "degraded",
        "reason_code": "repair_worker_in_process_execution_disabled",
        "task_id": str(task_id),
        "procedure_id": str(procedure_id),
    }


__all__ = [
    "AiSnakeProviderConfig",
    "NativeWorkerRuntimeService",
    "_RESOURCE_ENFORCER",
    "execute_repair_procedure_plan",
    "get_native_worker_runtime_service",
]
