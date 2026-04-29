from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.core.execution_profile import normalize_execution_profile
from worker.core.ports import ArtifactPort, PolicyPort, TracePort
from worker.core.verification import validate_worker_schema_or_degraded

_ACTIONABLE_TODO_STATUSES = {"todo", "open", "planned", "in_progress"}


class StandaloneRuntime:
    def __init__(self, *, policy_port: PolicyPort, trace_port: TracePort, artifact_port: ArtifactPort):
        self._policy_port = policy_port
        self._trace_port = trace_port
        self._artifact_port = artifact_port

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
        policy = self._policy_port.classify_command(command=command, profile=profile)
        decision = str(policy.get("decision") or "deny").strip().lower()
        self._trace_port.emit(
            event_type="standalone_runtime_started",
            payload={"task_id": task_id, "workspace_dir": str(workspace_dir), "worker_profile": profile, "policy_decision": decision},
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
        artifact = self._artifact_port.publish(
            artifact={
                "schema": "command_plan_artifact.v1",
                "task_id": task_id,
                "capability_id": "worker.command.plan",
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

        policy = self._policy_port.classify_command(command=command_for_policy, profile=profile)
        decision = str(policy.get("decision") or "deny").strip().lower()
        self._trace_port.emit(
            event_type="standalone_todo_runtime_started",
            payload={
                "task_id": task_id,
                "trace_id": trace_id,
                "workspace_dir": str(workspace_dir),
                "worker_profile": profile,
                "executor_kind": executor_kind,
                "policy_decision": decision,
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

        artifact_command = command or "assistant_execute --todo-contract"
        command_artifact = self._artifact_port.publish(
            artifact={
                "schema": "command_plan_artifact.v1",
                "task_id": task_id,
                "capability_id": str(control_manifest.get("capability_id") or "worker.command.execute"),
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
