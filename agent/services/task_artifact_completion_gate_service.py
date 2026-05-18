"""Central artifact-first completion gate.

Single source-of-truth facade for evaluating artifact evidence, mapping to final
status, and applying status transitions with unified event payload.
"""
from __future__ import annotations

from typing import Any

from agent.services.task_completion_policy_service import get_task_completion_policy_service


class TaskArtifactCompletionGateService:
    def decide(
        self,
        *,
        task_id: str,
        goal_id: str | None = None,
        collection_result: dict[str, Any],
        advisory_parse_result: dict[str, Any] | None = None,
        exit_code: int | None = None,
        retry_count: int = 0,
        expected_paths: list[str] | None = None,
        verification_required: bool = False,
        allow_synthesized_manifest: bool = False,
    ) -> tuple[str, Any]:
        completion_svc = get_task_completion_policy_service()
        decision = completion_svc.evaluate(
            task_id=task_id,
            goal_id=goal_id,
            collection_result=collection_result,
            advisory_parse_result=advisory_parse_result,
            exit_code=exit_code,
            retry_count=retry_count,
            expected_paths=expected_paths,
            verification_required=verification_required,
            allow_synthesized_manifest=allow_synthesized_manifest,
        )
        final_status = completion_svc.to_status(decision)
        return final_status, decision

    def event_details(self, *, decision: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "completion_decision": decision.decision,
            "reason_codes": list(decision.reason_codes or []),
            "advisory_parse_status": decision.advisory_parse_status,
            "artifact_ids": list(decision.artifact_ids or []),
            "manifest_id": decision.manifest_id,
        }
        if extra:
            payload.update(dict(extra))
        return payload


_SERVICE = TaskArtifactCompletionGateService()


def get_task_artifact_completion_gate_service() -> TaskArtifactCompletionGateService:
    return _SERVICE
