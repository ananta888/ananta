from __future__ import annotations

from typing import Any

from worker.runtime.standalone_runtime import StandaloneRuntime


class _Policy:
    def classify_command(self, *, command: str, profile: str) -> dict[str, Any]:
        return {"decision": "allow", "risk_classification": "low", "required_approval": False}


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


class _Artifacts:
    def publish(self, *, artifact: dict[str, Any]) -> dict[str, Any]:
        return {**artifact, "artifact_ref": "artifact:1"}


def test_standalone_runtime_executes_core_loop_with_ports() -> None:
    runtime = StandaloneRuntime(policy_port=_Policy(), trace_port=_Trace(), artifact_port=_Artifacts())
    result = runtime.run(
        task_contract={
            "schema": "standalone_task_contract.v1",
            "task_id": "task-1",
            "goal": "run tests",
            "command": "pytest -q",
            "worker_profile": "balanced",
        },
        workspace_dir=".",
    )
    assert result["status"] == "completed"
    assert result["worker_profile"] == "balanced"
    assert result["artifacts"][0]["schema"] == "command_plan_artifact.v1"

