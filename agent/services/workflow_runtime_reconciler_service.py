"""Hub-owned scheduler tick for active workflow runtime bindings."""

from __future__ import annotations

from typing import Any, Protocol


class WorkflowRuntimeReconcilePort(Protocol):
    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]: ...


class WorkflowRuntimeReconcilerService:
    """Advance bounded active runs without coupling progress to HTTP reads."""

    def __init__(self, control: WorkflowRuntimeReconcilePort) -> None:
        self._control = control

    def run_once(self, *, limit: int = 100) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 1000))
        result = dict(self._control.reconcile_active(limit=bounded))
        runtime_ids = [
            str(value)
            for value in result.get("runtime_ids") or ()
            if str(value).strip()
        ]
        reports = [
            dict(value)
            for value in result.get("reports") or ()
            if isinstance(value, dict)
        ]
        return {
            "runtime_id": str(result.get("runtime_id") or ""),
            "runtime_ids": runtime_ids,
            "processed": max(0, int(result.get("processed") or 0)),
            "failed": [
                dict(value)
                for value in result.get("failed") or ()
                if isinstance(value, dict)
            ],
            "reports": reports,
        }


def build_workflow_runtime_reconciler_service() -> WorkflowRuntimeReconcilerService:
    from agent.services.workflow_control_composition import (
        get_workflow_backend_control_facade,
    )

    return WorkflowRuntimeReconcilerService(get_workflow_backend_control_facade())


__all__ = [
    "WorkflowRuntimeReconcilePort",
    "WorkflowRuntimeReconcilerService",
    "build_workflow_runtime_reconciler_service",
]
