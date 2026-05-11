"""TaskFinalizationService — artifact-first task finalization with audit.

Finalizes tasks from artifact evidence and emits required audit events.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.common.audit import (
    log_audit,
    AUDIT_ARTIFACT_MANIFEST_COLLECTED,
    AUDIT_ARTIFACT_MANIFEST_SYNTHESIZED,
    AUDIT_ARTIFACT_COMPLETION_DECIDED,
    AUDIT_TASK_FINALIZED_FROM_ARTIFACTS,
    AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED,
)
from agent.services.task_completion_policy_service import get_task_completion_policy_service
from agent.services.task_runtime_service import update_local_task_status
from agent.services.worker_output_collector_service import get_worker_output_collector_service

log = logging.getLogger(__name__)


class TaskFinalizationService:
    def finalize_from_artifacts(
        self,
        *,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        workspace_root: Path,
        manifest_relative_path: str,
        advisory_parse_result: dict[str, Any] | None = None,
        exit_code: int | None = None,
        retry_count: int = 0,
        expected_paths: list[str] | None = None,
        verification_required: bool = False,
        allow_synthesized_manifest: bool = False,
        before_snapshot: dict[str, str] | None = None,
        after_snapshot: dict[str, str] | None = None,
        before_snapshot_id: str | None = None,
        after_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Finalize task based on artifact evidence. Emits audit events."""
        collector = get_worker_output_collector_service()
        collection = collector.collect(
            task_id=task_id,
            goal_id=goal_id,
            execution_id=execution_id,
            trace_id=trace_id,
            workspace_root=workspace_root,
            manifest_relative_path=manifest_relative_path,
            allow_synthesized_fallback=allow_synthesized_manifest,
            before_snapshot_id=before_snapshot_id,
            before_snapshot=before_snapshot,
            after_snapshot_id=after_snapshot_id,
            after_snapshot=after_snapshot,
        )

        # Audit: manifest collected or synthesized
        if collection.get("synthesized"):
            log_audit(AUDIT_ARTIFACT_MANIFEST_SYNTHESIZED, {
                "task_id": task_id, "goal_id": goal_id, "execution_id": execution_id,
                "trace_id": trace_id, "artifact_count": len(collection.get("artifacts") or []),
            })
        else:
            log_audit(AUDIT_ARTIFACT_MANIFEST_COLLECTED, {
                "task_id": task_id, "goal_id": goal_id, "execution_id": execution_id,
                "trace_id": trace_id, "manifest_id": collection.get("manifest_id", ""),
                "manifest_valid": collection.get("manifest_valid"),
                "artifact_count": len(collection.get("artifacts") or []),
            })

        # Audit: advisory parse failure — logged separately, not authoritative
        if advisory_parse_result and advisory_parse_result.get("parse_error"):
            log_audit(AUDIT_ADVISORY_JSON_PARSE_FAILED_IGNORED, {
                "task_id": task_id, "goal_id": goal_id, "execution_id": execution_id,
                "trace_id": trace_id,
                "error_classification": advisory_parse_result.get("error_classification"),
                "has_valid_artifacts": bool(collection.get("manifest_valid")),
            })

        completion_svc = get_task_completion_policy_service()
        decision = completion_svc.evaluate(
            task_id=task_id,
            goal_id=goal_id,
            collection_result=collection,
            advisory_parse_result=advisory_parse_result,
            exit_code=exit_code,
            retry_count=retry_count,
            expected_paths=expected_paths,
            verification_required=verification_required,
            allow_synthesized_manifest=allow_synthesized_manifest,
        )
        final_status = completion_svc.to_status(decision)

        log_audit(AUDIT_ARTIFACT_COMPLETION_DECIDED, {
            "task_id": task_id, "goal_id": goal_id, "execution_id": execution_id,
            "trace_id": trace_id, "decision": decision.decision,
            "reason_codes": decision.reason_codes,
            "manifest_id": decision.manifest_id,
            "artifact_ids": decision.artifact_ids,
        })

        update_local_task_status(
            task_id,
            final_status,
            event_type="artifact_first_finalization",
            event_actor="system",
            event_details={
                "completion_decision": decision.decision,
                "reason_codes": decision.reason_codes,
                "advisory_parse_status": decision.advisory_parse_status,
                "manifest_id": decision.manifest_id,
                "artifact_ids": decision.artifact_ids,
                "execution_id": execution_id,
            },
        )

        log_audit(AUDIT_TASK_FINALIZED_FROM_ARTIFACTS, {
            "task_id": task_id, "goal_id": goal_id, "execution_id": execution_id,
            "trace_id": trace_id, "final_status": final_status,
            "decision": decision.decision, "reason_codes": decision.reason_codes,
            "artifact_ids": decision.artifact_ids,
        })

        log.info(
            "TaskFinalizationService: task %s finalized as %s (decision=%s)",
            task_id, final_status, decision.decision,
        )
        return {
            "task_id": task_id,
            "final_status": final_status,
            "decision": decision.to_dict(),
            "collection": collection,
        }


task_finalization_service = TaskFinalizationService()


def get_task_finalization_service() -> TaskFinalizationService:
    return task_finalization_service
