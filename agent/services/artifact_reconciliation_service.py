"""ArtifactRealityReconciliationService — reconcile Hub state with filesystem/artifact reality.

Used during migration when Hub state disagrees with existing valid artifacts.
Requires audit and permission; never exposes a blind mark-completed shortcut.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from agent.common.audit import log_audit
from agent.services.task_completion_policy_service import get_task_completion_policy_service
from agent.services.worker_output_collector_service import get_worker_output_collector_service

log = logging.getLogger(__name__)


class ArtifactReconciliationService:
    def dry_run(
        self,
        *,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        workspace_root: Path,
        manifest_relative_path: str,
        expected_paths: list[str] | None = None,
        allow_synthesized_manifest: bool = True,
        before_snapshot: dict[str, str] | None = None,
        after_snapshot: dict[str, str] | None = None,
        before_snapshot_id: str | None = None,
        after_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Compare Hub expected artifacts with workspace reality. No state changes."""
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
        completion_svc = get_task_completion_policy_service()
        decision = completion_svc.evaluate(
            task_id=task_id,
            goal_id=goal_id,
            collection_result=collection,
            expected_paths=list(expected_paths or []),
            allow_synthesized_manifest=allow_synthesized_manifest,
        )
        return {
            "dry_run": True,
            "task_id": task_id,
            "collection": collection,
            "would_apply_decision": decision.decision,
            "would_apply_status": completion_svc.to_status(decision),
            "reason_codes": decision.reason_codes,
            "artifact_ids": decision.artifact_ids,
        }

    def apply(
        self,
        *,
        task_id: str,
        goal_id: str,
        execution_id: str,
        trace_id: str,
        workspace_root: Path,
        manifest_relative_path: str,
        actor: str,
        reason: str,
        expected_paths: list[str] | None = None,
        allow_synthesized_manifest: bool = True,
        before_snapshot: dict[str, str] | None = None,
        after_snapshot: dict[str, str] | None = None,
        before_snapshot_id: str | None = None,
        after_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile task state from artifacts. Requires actor and reason for audit."""
        if not str(actor or "").strip():
            raise ValueError("actor is required for artifact reconciliation")
        if not str(reason or "").strip():
            raise ValueError("reason is required for artifact reconciliation")

        # Refuse invalid paths
        try:
            resolved = (workspace_root / manifest_relative_path).resolve()
            if not resolved.is_relative_to(workspace_root.resolve()):
                return {
                    "applied": False,
                    "error": "manifest_path_escapes_workspace",
                    "task_id": task_id,
                }
        except (ValueError, OSError) as exc:
            return {"applied": False, "error": f"path_error:{exc}", "task_id": task_id}

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
        completion_svc = get_task_completion_policy_service()
        decision = completion_svc.evaluate(
            task_id=task_id,
            goal_id=goal_id,
            collection_result=collection,
            expected_paths=list(expected_paths or []),
            allow_synthesized_manifest=allow_synthesized_manifest,
        )
        final_status = completion_svc.to_status(decision)

        from agent.services.task_runtime_service import update_local_task_status
        update_local_task_status(
            task_id,
            final_status,
            event_type="artifact_reconciliation_applied",
            event_actor=actor,
            event_details={
                "reason": reason,
                "completion_decision": decision.decision,
                "reason_codes": decision.reason_codes,
                "artifact_ids": decision.artifact_ids,
                "manifest_id": decision.manifest_id,
            },
        )
        log_audit(
            "artifact_reconciliation_applied",
            {
                "task_id": task_id,
                "goal_id": goal_id,
                "actor": actor,
                "reason": reason,
                "final_status": final_status,
                "decision": decision.decision,
                "reason_codes": decision.reason_codes,
            },
        )
        log.info(
            "ArtifactReconciliationService: task %s reconciled to %s by %s",
            task_id, final_status, actor,
        )
        return {
            "applied": True,
            "task_id": task_id,
            "final_status": final_status,
            "decision": decision.decision,
            "reason_codes": decision.reason_codes,
            "artifact_ids": decision.artifact_ids,
        }


artifact_reconciliation_service = ArtifactReconciliationService()


def get_artifact_reconciliation_service() -> ArtifactReconciliationService:
    return artifact_reconciliation_service
