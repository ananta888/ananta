"""Worker materialization of an exact Hub-issued refresh plan."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class KnowledgeExpertRefreshPlanPort(Protocol):
    retrain: tuple[str, ...]
    plan_digest: str


class KnowledgeExpertDeltaPort(Protocol):
    def materialize(self, *, unit_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class KnowledgeExpertDeltaExecutor:
    def __init__(self, port: KnowledgeExpertDeltaPort) -> None:
        self._port = port

    def execute(
        self,
        plan: KnowledgeExpertRefreshPlanPort,
        *,
        inputs: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        expected = set(plan.retrain)
        if set(inputs) != expected:
            raise ValueError("knowledge_expert_delta_input_mismatch")
        artifacts = {
            unit_id: dict(self._port.materialize(unit_id=unit_id, payload=inputs[unit_id]))
            for unit_id in sorted(expected)
        }
        return {
            "schema": "ananta.knowledge-expert-delta-result.v1",
            "plan_digest": plan.plan_digest,
            "artifacts": artifacts,
            "activation_authorized": False,
        }


__all__ = ["KnowledgeExpertDeltaExecutor", "KnowledgeExpertDeltaPort", "KnowledgeExpertRefreshPlanPort"]
