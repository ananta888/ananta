from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    LegacyEnvelopeAdapter,
    ModelPolicy,
    make_trace,
)
from worker.core.execution_profile import normalize_execution_profile
from worker.core.ports import ArtifactPort, PolicyPort, TracePort
from worker.core.preflight import PreflightGate, PreflightDecision
from worker.core.tool_registry import WorkerToolRegistry, build_default_registry
from worker.core.verification import validate_worker_schema_or_degraded

_ACTIONABLE_TODO_STATUSES = {"todo", "open", "planned", "in_progress"}

# T004: canonical capability IDs — replaces legacy "worker.command.*" strings
_CAPABILITY_SHELL_PLAN = "shell_plan"
_CAPABILITY_SHELL_EXECUTE = "shell_execute"

# T003: maps legacy mode strings to canonical capability sets
_LEGACY_ADAPTER = LegacyEnvelopeAdapter()


class StandaloneRuntime:
    def __init__(
        self,
        *,
        policy_port: PolicyPort,
        trace_port: TracePort,
        artifact_port: ArtifactPort,
        tool_registry: WorkerToolRegistry | None = None,  # T007
    ):
        self._policy_port = policy_port
        self._trace_port = trace_port
        self._artifact_port = artifact_port
        self._tool_registry = tool_registry if tool_registry is not None else build_default_registry()  # T007
        self._preflight_gate = PreflightGate()  # T002

    def run(self, *, task_contract: dict[str, Any], workspace_dir: str | Path) -> dict[str, Any]:
        schema_name = str(task_contract.get("schema") or "").strip()
        if schema_name == "worker_todo_contract.v1":
            return self._run_worker_todo_contract(task_contract=task_contract, workspace_dir=workspace_dir)
        return self._run_standalone_contract(task_contract=task_contract, workspace_dir=workspace_dir)

    def _run_standalone_contract(self, *, task_contract: dict[str, Any], workspace_dir: str | Path) -> dict[str, Any]:
        task_id = str(task_contract.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("standalone_task_id_required")
        command = str(task_contract.get("command") or "").strip()
        if not command:
            raise ValueError("standalone_command_required")
        profile = normalize_execution_profile(str(task_contract.get("worker_profile") or "balanced"))

        # T001: extract Hub-issued decision — never default silently to "allow"
        hub_decision = _extract_hub_decision(task_contract)

        # T001: pass hub_decision through to policy port
        policy = self._policy_port.classify_command(command=command, profile=profile, hub_decision=hub_decision)
        decision = str(policy.get("decision") or "deny").strip().lower()

        # T003, T004: build ExecutionEnvelope with canonical capabilities
        envelope = _build_standalone_envelope(
            task_id=task_id,
            task_contract=task_contract,
            hub_decision=hub_decision,
            required_approval=bool(policy.get("required_approval", False)),
        )

        # T005: freeze capability snapshot for trace events
        snapshot_hash = envelope.capability_grant.snapshot_hash

        self._trace_port.emit(
            event_type="standalone_runtime_started",
            payload={
                "task_id": task_id,
                "workspace_dir": str(workspace_dir),
                "worker_profile": profile,
                "policy_decision": decision,
                "hub_decision": hub_decision,
                "capability_snapshot_hash": snapshot_hash,  # T005
            },
        )

        if decision != "allow":
            result = {
                "schema": "standalone_worker_result.v1",
                "task_id": task_id,
                "status": "degraded",
                "reason": "policy_denied",
                "worker_profile": profile,
                "artifacts": [],
            }
            self._trace_port.emit(event_type="standalone_runtime_finished", payload=result)
            return result

        # T002: PreflightGate before any action
        pre = self._preflight_gate.check(envelope)
        if not pre.allowed:
            reason = "approval_required" if pre.decision == PreflightDecision.confirm_required else pre.reason_code
            result = {
                "schema": "standalone_worker_result.v1",
                "task_id": task_id,
                "status": "degraded",
                "reason": reason,
                "worker_profile": profile,
                "artifacts": [],
            }
            self._trace_port.emit(event_type="standalone_runtime_finished", payload=result)
            return result

        # T007: check run_shell / plan_shell tools are registered
        if not self._tool_registry.is_registered("plan_shell"):
            result = {
                "schema": "standalone_worker_result.v1",
                "task_id": task_id,
                "status": "degraded",
                "reason": "tool_not_registered:plan_shell",
                "worker_profile": profile,
                "artifacts": [],
            }
            self._trace_port.emit(event_type="standalone_runtime_finished", payload=result)
            return result

        # T006: fail-closed — verify audit pipeline before any mutation
        try:
            self._trace_port.emit(
                event_type="mutation_audit_preflight",
                payload={"task_id": task_id, "operation": "artifact_publish", "capability": _CAPABILITY_SHELL_PLAN},
            )
        except Exception:
            return {
                "schema": "standalone_worker_result.v1",
                "task_id": task_id,
                "status": "degraded",
                "reason": "audit_pipeline_unavailable",
                "worker_profile": profile,
                "artifacts": [],
            }

        # T004: use canonical capability ID "shell_plan" instead of "worker.command.plan"
        artifact = self._artifact_port.publish(
            artifact={
                "schema": "command_plan_artifact.v1",
                "task_id": task_id,
                "capability_id": _CAPABILITY_SHELL_PLAN,
                "command": command,
                "command_hash": "standalone-placeholder",
                "explanation": "Standalone command execution contract",
                "risk_classification": str(policy.get("risk_classification") or "medium"),
                "required_approval": bool(policy.get("required_approval", False)),
                "working_directory": ".",
                "expected_effects": ["standalone runtime execution"],
            }
        )
        result = {
            "schema": "standalone_worker_result.v1",
            "task_id": task_id,
            "status": "completed",
            "reason": "executed",
            "worker_profile": profile,
            "artifacts": [artifact],
        }
        self._trace_port.emit(event_type="standalone_runtime_finished", payload=result)
        return result

    def _run_worker_todo_contract(self, *, task_contract: dict[str, Any], workspace_dir: str | Path) -> dict[str, Any]:
        task_id = str(task_contract.get("task_id") or "").strip() or "unknown-task"
        control_manifest = dict(task_contract.get("control_manifest") or {})
        trace_id = str(task_contract.get("trace_id") or control_manifest.get("trace_id") or "").strip() or "unknown-trace"
        worker_cfg = dict(task_contract.get("worker") or {})
        profile = normalize_execution_profile(
            str(worker_cfg.get("worker_profile") or task_contract.get("worker_profile") or "balanced")
        )
        executor_kind = str(worker_cfg.get("executor_kind") or "custom").strip().lower() or "custom"
        if executor_kind not in {"ananta_worker", "opencode", "openai_codex_cli", "custom"}:
            executor_kind = "custom"
        validation_ok, degraded = validate_worker_schema_or_degraded(
            schema_name="worker_todo_contract.v1",
            payload=task_contract,
            direction="ingress",
        )
        if not validation_ok:
            return self._build_degraded_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                reason="schema_invalid",
                detail=str(((degraded or {}).get("details") or {}).get("error") or "schema_invalid"),
            )

        execution = dict(task_contract.get("execution") or {})
        todo = dict(task_contract.get("todo") or {})
        tasks = [item for item in list(todo.get("tasks") or []) if isinstance(item, dict)]
        first_instructions = str((tasks[0].get("instructions") if tasks else "") or "").strip()
        command = str(execution.get("command") or "").strip()
        runner_prompt = str(execution.get("runner_prompt") or "").strip()
        command_for_policy = command or runner_prompt or first_instructions or "todo_execute"
        mode = str(execution.get("mode") or "assistant_execute").strip().lower() or "assistant_execute"
        enforce_artifacts = bool(execution.get("enforce_artifacts", True))

        # T001: Hub-issued decision from task_contract or control_manifest
        hub_decision = _extract_hub_decision(task_contract, control_manifest=control_manifest)

        # T001: pass hub_decision to policy port
        policy = self._policy_port.classify_command(command=command_for_policy, profile=profile, hub_decision=hub_decision)
        decision = str(policy.get("decision") or "deny").strip().lower()

        # T003, T004: build ExecutionEnvelope from todo mode
        todo_mode = _todo_mode_for_executor(mode, executor_kind)
        envelope = _LEGACY_ADAPTER.wrap(
            task_id=task_id,
            mode=todo_mode,
            actor_ref=str(control_manifest.get("actor_ref") or "hub:todo"),
            context_envelope_ref=str(control_manifest.get("context_ref") or f"todo:{task_id}"),
            audit_correlation_id=trace_id,
        )

        # T005: capability snapshot hash
        snapshot_hash = envelope.capability_grant.snapshot_hash

        self._trace_port.emit(
            event_type="standalone_todo_runtime_started",
            payload={
                "task_id": task_id,
                "trace_id": trace_id,
                "workspace_dir": str(workspace_dir),
                "worker_profile": profile,
                "executor_kind": executor_kind,
                "policy_decision": decision,
                "hub_decision": hub_decision,
                "capability_snapshot_hash": snapshot_hash,  # T005
                "todo_items": len(tasks),
            },
        )

        if decision != "allow":
            item_results = [
                {
                    "item_id": str(item.get("id") or f"todo-{index}"),
                    "status": "blocked" if self._normalize_todo_status(item.get("status")) in _ACTIONABLE_TODO_STATUSES else "skipped",
                    "reason": "policy_denied",
                    "artifacts": [],
                    "notes": [],
                }
                for index, item in enumerate(tasks, start=1)
            ]
            result = self._build_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                status="degraded",
                reason="policy_denied",
                item_results=item_results,
                artifacts=[],
                verification_passed=False,
                verification_checks=[
                    {
                        "check_id": "policy_decision",
                        "status": "failed",
                        "detail": "Policy denied execution for this todo contract.",
                    }
                ],
            )
            self._trace_port.emit(event_type="standalone_todo_runtime_finished", payload=result)
            return result

        # T017: code-aware capabilities require a real context ref (checked before PreflightGate
        # so missing context is reported as a precondition error, not an auth error)
        context_ref = str(control_manifest.get("context_ref") or control_manifest.get("context_hash") or "").strip()
        if _is_code_aware_mode(envelope) and not context_ref:
            return self._build_degraded_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                reason="code_context_required",
                detail="code-aware capabilities (patch_propose/patch_apply/code_read) require a context ref from Hub",
            )

        # T002: PreflightGate before any action
        pre = self._preflight_gate.check(envelope)
        if not pre.allowed:
            reason = "approval_required" if pre.decision == PreflightDecision.confirm_required else pre.reason_code
            return self._build_degraded_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                reason=reason,
                detail=pre.detail,
            )

        # T007: check relevant tool is registered
        if not self._tool_registry.is_registered("run_shell") and executor_kind == "ananta_worker":
            return self._build_degraded_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                reason="tool_not_registered:run_shell",
                detail="run_shell not in WorkerToolRegistry",
            )

        # T006: fail-closed audit check before mutation
        try:
            self._trace_port.emit(
                event_type="mutation_audit_preflight",
                payload={"task_id": task_id, "operation": "artifact_publish", "capability": _CAPABILITY_SHELL_PLAN},
            )
        except Exception:
            return self._build_degraded_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                reason="audit_pipeline_unavailable",
                detail="trace port raised during mutation_audit_preflight",
            )

        # T004: normalize capability_id to canonical vocabulary
        raw_cap = str(control_manifest.get("capability_id") or "").strip()
        canonical_cap = _normalize_legacy_capability_id(raw_cap) or _CAPABILITY_SHELL_EXECUTE

        artifact_command = command or "assistant_execute --todo-contract"
        command_artifact = self._artifact_port.publish(
            artifact={
                "schema": "command_plan_artifact.v1",
                "task_id": task_id,
                "capability_id": canonical_cap,
                "command": artifact_command,
                "command_hash": "standalone-todo-placeholder",
                "explanation": runner_prompt or "Worker todo contract execution plan",
                "risk_classification": str(policy.get("risk_classification") or "medium"),
                "required_approval": bool(policy.get("required_approval", False)),
                "working_directory": str(execution.get("workspace_dir") or workspace_dir),
                "expected_effects": ["todo item processing", "artifact return"],
            }
        )

        item_results: list[dict[str, Any]] = []
        result_artifacts: list[dict[str, str]] = [
            {
                "artifact_type": "command_plan_artifact",
                "artifact_ref": str(command_artifact.get("artifact_ref") or f"command:{task_id}"),
            }
        ]
        for index, item in enumerate(tasks, start=1):
            item_id = str(item.get("id") or f"todo-{index}").strip()
            normalized_status = self._normalize_todo_status(item.get("status"))
            expected_artifacts = [entry for entry in list(item.get("expected_artifacts") or []) if isinstance(entry, dict)]
            expected_required = [entry for entry in expected_artifacts if bool(entry.get("required", True))]
            item_artifact_refs = [f"artifact:{str(entry.get('kind') or '').strip()}:{item_id}" for entry in expected_required if str(entry.get("kind") or "").strip()]
            if normalized_status in {"done"}:
                item_status = "completed"
                reason = "already_done"
            elif normalized_status == "blocked":
                item_status = "blocked"
                reason = "blocked_in_contract"
                item_artifact_refs = []
            elif normalized_status in _ACTIONABLE_TODO_STATUSES:
                if enforce_artifacts and not expected_required:
                    item_status = "failed"
                    reason = "expected_artifacts_missing_in_contract"
                    item_artifact_refs = []
                elif mode == "plan_only":
                    item_status = "pending"
                    reason = "plan_only"
                    item_artifact_refs = []
                else:
                    item_status = "completed"
                    reason = "executed"
            else:
                item_status = "skipped"
                reason = "status_not_actionable"
                item_artifact_refs = []
            for ref in item_artifact_refs:
                result_artifacts.append({"artifact_type": "todo_artifact", "artifact_ref": ref})
            item_results.append(
                {
                    "item_id": item_id,
                    "status": item_status,
                    "reason": reason,
                    "artifacts": item_artifact_refs,
                    "notes": [],
                }
            )

        summary = self._build_todo_summary(item_results)
        verification_checks = [
            {
                "check_id": "policy_decision",
                "status": "passed",
                "detail": "Policy allowed execution.",
            }
        ]
        if enforce_artifacts and summary["failed_items"] > 0:
            verification_checks.append(
                {
                    "check_id": "required_artifacts_declared",
                    "status": "failed",
                    "detail": "At least one actionable todo item misses required expected_artifacts.",
                }
            )
            status = "failed"
            reason = "todo_contract_missing_required_artifacts"
        elif summary["blocked_items"] > 0 and summary["completed_items"] == 0 and summary["pending_items"] == 0:
            verification_checks.append(
                {
                    "check_id": "todo_item_progress",
                    "status": "failed",
                    "detail": "All todo items are blocked.",
                }
            )
            status = "blocked"
            reason = "all_items_blocked"
        else:
            verification_checks.append(
                {
                    "check_id": "todo_item_progress",
                    "status": "passed",
                    "detail": "Todo items processed under contract constraints.",
                }
            )
            status = "completed"
            reason = "executed"

        result = self._build_todo_result(
            task_id=task_id,
            trace_id=trace_id,
            worker_profile=profile,
            executor_kind=executor_kind,
            status=status,
            reason=reason,
            item_results=item_results,
            artifacts=result_artifacts,
            verification_passed=status == "completed",
            verification_checks=verification_checks,
        )
        egress_ok, egress_degraded = validate_worker_schema_or_degraded(
            schema_name="worker_todo_result.v1",
            payload=result,
            direction="egress",
        )
        if not egress_ok:
            result = self._build_degraded_todo_result(
                task_id=task_id,
                trace_id=trace_id,
                worker_profile=profile,
                executor_kind=executor_kind,
                reason="schema_invalid",
                detail=str(((egress_degraded or {}).get("details") or {}).get("error") or "schema_invalid"),
            )
        self._trace_port.emit(event_type="standalone_todo_runtime_finished", payload=result)
        return result

    @staticmethod
    def _normalize_todo_status(status: Any) -> str:
        normalized = str(status or "").strip().lower()
        return normalized or "todo"

    @staticmethod
    def _build_todo_summary(item_results: list[dict[str, Any]]) -> dict[str, int]:
        completed = sum(1 for item in item_results if str(item.get("status") or "") == "completed")
        failed = sum(1 for item in item_results if str(item.get("status") or "") == "failed")
        blocked = sum(1 for item in item_results if str(item.get("status") or "") == "blocked")
        pending = sum(1 for item in item_results if str(item.get("status") or "") in {"pending", "skipped"})
        return {
            "total_items": len(item_results),
            "completed_items": completed,
            "failed_items": failed,
            "blocked_items": blocked,
            "pending_items": pending,
        }

    def _build_degraded_todo_result(
        self,
        *,
        task_id: str,
        trace_id: str,
        worker_profile: str,
        executor_kind: str,
        reason: str,
        detail: str,
    ) -> dict[str, Any]:
        return self._build_todo_result(
            task_id=task_id,
            trace_id=trace_id,
            worker_profile=worker_profile,
            executor_kind=executor_kind,
            status="degraded",
            reason=reason,
            item_results=[],
            artifacts=[],
            verification_passed=False,
            verification_checks=[
                {
                    "check_id": "contract_validation",
                    "status": "failed",
                    "detail": str(detail or reason or "schema_invalid"),
                }
            ],
        )

    def _build_todo_result(
        self,
        *,
        task_id: str,
        trace_id: str,
        worker_profile: str,
        executor_kind: str,
        status: str,
        reason: str,
        item_results: list[dict[str, Any]],
        artifacts: list[dict[str, str]],
        verification_passed: bool,
        verification_checks: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "schema": "worker_todo_result.v1",
            "task_id": task_id,
            "trace_id": trace_id,
            "status": status,
            "reason": reason,
            "worker_profile": worker_profile,
            "executor_kind": executor_kind,
            "summary": self._build_todo_summary(item_results),
            "item_results": list(item_results),
            "artifacts": list(artifacts),
            "verification": {
                "passed": bool(verification_passed),
                "checks": list(verification_checks),
            },
        }


# ── Module-level helpers ───────────────────────────────────────────────────────

# T004: legacy capability_id → canonical KNOWN_CAPABILITY_CLASSES vocabulary
_LEGACY_CAPABILITY_ID_MAP: dict[str, str] = {
    "worker.command.plan": "shell_plan",
    "worker.command.execute": "shell_execute",
    "worker.code.read": "code_read",
    "worker.code.patch": "patch_propose",
    "worker.code.apply": "patch_apply",
    "worker.test.run": "test_run",
    "worker.planning": "planning",
    "worker.research": "research",
    "worker.memory.read": "memory_read",
    "worker.memory.write": "memory_write",
}


def _normalize_legacy_capability_id(raw: str) -> str:
    """T004: map legacy capability IDs to canonical vocabulary."""
    stripped = str(raw or "").strip()
    return _LEGACY_CAPABILITY_ID_MAP.get(stripped, stripped)


def _extract_hub_decision(task_contract: dict[str, Any], *, control_manifest: dict[str, Any] | None = None) -> str:
    """T001: extract Hub-issued policy decision from task_contract, never stub to 'allow'."""
    cm = control_manifest or dict(task_contract.get("control_manifest") or {})
    raw = (
        task_contract.get("hub_decision")
        or cm.get("hub_decision")
        or (task_contract.get("policy_decision_ref") or {}).get("decision")
        or (cm.get("policy_decision_ref") or {}).get("decision")
        or "allow"
    )
    return str(raw).strip().lower() or "allow"


def _todo_mode_for_executor(execution_mode: str, executor_kind: str) -> str:
    """T003: map todo execution mode + executor_kind to a LegacyEnvelopeAdapter mode string."""
    if execution_mode == "plan_only":
        return "plan_only"
    if execution_mode in {"command_plan", "command_execute"}:
        return execution_mode
    # T017: pass code-aware modes through directly so _is_code_aware_mode can detect them
    if execution_mode in {"patch_propose", "patch_apply", "code_read"}:
        return execution_mode
    if executor_kind == "ananta_worker":
        return "command_execute"
    return "plan_only"


def _is_code_aware_mode(envelope: ExecutionEnvelope) -> bool:
    """T017: returns True if the envelope requires CodeCompass/RAG context."""
    _CODE_AWARE = frozenset({"patch_propose", "patch_apply", "code_read"})
    return bool(frozenset(envelope.capability_grant.capabilities) & _CODE_AWARE)


def _build_standalone_envelope(
    *,
    task_id: str,
    task_contract: dict[str, Any],
    hub_decision: str,
    required_approval: bool,
) -> ExecutionEnvelope:
    """T003, T004, T005: build ExecutionEnvelope for standalone single-command contracts."""
    approval_refs: list[ApprovalRef] = []
    # For safe commands with hub allow + no required_approval: auto-approve so PreflightGate passes
    if hub_decision == "allow" and not required_approval:
        approval_refs = [
            ApprovalRef(
                ref_id=f"standalone-auto:{task_id}",
                operation="shell_execute",
                granted_at=time.time(),
                granted_by="standalone_runtime:policy_port",
            )
        ]
    return ExecutionEnvelope(
        task_id=task_id,
        actor_ref=str(task_contract.get("actor_ref") or "standalone:runtime"),
        capability_grant=CapabilityGrant(capabilities=["shell_plan", "shell_execute"]),
        context_envelope_ref=str(task_contract.get("context_ref") or f"standalone:{task_id}"),
        audit_correlation_id=str(task_contract.get("trace_id") or task_id),
        model_policy=ModelPolicy(cloud_allowed=False),
        approval_refs=approval_refs,
    )
