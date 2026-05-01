from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

RUNTIME_STATES = {"connected", "degraded", "unauthorized", "approval_required", "policy_limited"}


@dataclass(frozen=True)
class BlenderRuntimeState:
    state: str
    connected: bool
    capabilities: list[str]
    problems: list[str]
    last_error: str | None = None
    selected_task_id: str | None = None
    selected_artifact_id: str | None = None
    selected_approval_id: str | None = None
    cached_tasks: list[dict[str, Any]] | None = None
    cached_artifacts: list[dict[str, Any]] | None = None
    cached_approvals: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cached_tasks"] = list(self.cached_tasks or [])
        payload["cached_artifacts"] = list(self.cached_artifacts or [])
        payload["cached_approvals"] = list(self.cached_approvals or [])
        return payload


def normalize_runtime_state(value: str | None, *, connected: bool = False) -> str:
    state = str(value or "").strip().lower()
    if state in RUNTIME_STATES:
        return state
    return "connected" if connected else "degraded"


def evaluate_health(connected: bool, capabilities: list[str] | None = None, problems: list[str] | None = None) -> dict:
    state = "connected" if connected and not problems else "degraded"
    return {
        "connected": bool(connected),
        "capabilities": list(capabilities or []),
        "state": state,
        "problems": list(problems or []),
    }


def build_runtime_state(
    *,
    connected: bool,
    capabilities: list[str] | None = None,
    problems: list[str] | None = None,
    state: str | None = None,
    last_error: str | None = None,
    tasks: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    approvals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runtime = BlenderRuntimeState(
        state=normalize_runtime_state(state, connected=connected),
        connected=bool(connected),
        capabilities=list(capabilities or []),
        problems=list(problems or []),
        last_error=last_error,
        cached_tasks=[dict(item) for item in list(tasks or [])],
        cached_artifacts=[dict(item) for item in list(artifacts or [])],
        cached_approvals=[dict(item) for item in list(approvals or [])],
    )
    return runtime.as_dict()
