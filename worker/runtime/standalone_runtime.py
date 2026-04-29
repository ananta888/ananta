from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.core.execution_profile import normalize_execution_profile
from worker.core.ports import ArtifactPort, PolicyPort, TracePort


class StandaloneRuntime:
    def __init__(self, *, policy_port: PolicyPort, trace_port: TracePort, artifact_port: ArtifactPort):
        self._policy_port = policy_port
        self._trace_port = trace_port
        self._artifact_port = artifact_port

    def run(self, *, task_contract: dict[str, Any], workspace_dir: str | Path) -> dict[str, Any]:
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

