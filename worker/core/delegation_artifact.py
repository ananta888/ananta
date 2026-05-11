"""DelegationArtifact: execution tree trace for delegated worker jobs. AWF-T033."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DelegationArtifact:
    """Records parent-child execution tree relationships. AWF-T033.

    Failed child results must not disappear from parent summary.
    Parent WorkerResult references this artifact via artifact_refs.
    """
    parent_execution_id: str
    child_execution_ids: list[str]
    child_task_ids: list[str]
    delegated_capabilities: list[str]
    context_refs: list[str]
    statuses: dict[str, str]      # child_execution_id → status string
    artifact_refs: list[str]
    trace_refs: list[str]
    created_at: float = field(default_factory=time.time)

    @property
    def all_succeeded(self) -> bool:
        return bool(self.statuses) and all(s == "success" for s in self.statuses.values())

    @property
    def any_failed(self) -> bool:
        return any(s in {"failed", "error", "timeout", "cancelled"} for s in self.statuses.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "delegation_artifact",
            "parent_execution_id": self.parent_execution_id,
            "child_execution_ids": list(self.child_execution_ids),
            "child_task_ids": list(self.child_task_ids),
            "delegated_capabilities": list(self.delegated_capabilities),
            "context_refs": list(self.context_refs),
            "statuses": dict(self.statuses),
            "artifact_refs": list(self.artifact_refs),
            "trace_refs": list(self.trace_refs),
            "created_at": self.created_at,
            "all_succeeded": self.all_succeeded,
            "any_failed": self.any_failed,
        }


def build_delegation_artifact(
    *,
    parent_execution_id: str,
    children: list[dict[str, Any]],
    delegated_capabilities: list[str],
    context_refs: list[str] | None = None,
) -> DelegationArtifact:
    """Build a DelegationArtifact from parent + child execution records. AWF-T033."""
    child_execution_ids = [
        str(c.get("execution_id") or c.get("id") or f"child-{i}")
        for i, c in enumerate(children)
    ]
    child_task_ids = [str(c.get("task_id") or "") for c in children]
    statuses = {
        eid: str(children[i].get("status") or "unknown")
        for i, eid in enumerate(child_execution_ids)
    }
    artifact_refs: list[str] = []
    trace_refs: list[str] = []
    for c in children:
        artifact_refs.extend(list(c.get("artifact_refs") or []))
        trace_refs.extend(list(c.get("trace_refs") or []))

    return DelegationArtifact(
        parent_execution_id=parent_execution_id,
        child_execution_ids=child_execution_ids,
        child_task_ids=child_task_ids,
        delegated_capabilities=list(delegated_capabilities),
        context_refs=list(context_refs or []),
        statuses=statuses,
        artifact_refs=artifact_refs,
        trace_refs=trace_refs,
    )
