"""Compensation of live-run executions that lost ownership of their segment."""

from __future__ import annotations

from typing import Any

from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.repositories.voice_live_runs import VoiceLiveRunRepository
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort
from agent.services.voice_result_artifact_service import VoiceResultArtifactService


class VoiceLiveRunCompensator:
    """Remove exact artifacts, tasks and idempotency claims of unowned executions."""

    def __init__(
        self,
        *,
        repository: VoiceLiveRunRepository,
        tombstones: VoiceDeletionTombstoneRepository,
        artifacts: VoiceResultArtifactService,
        tasks: VoiceLiveRunTaskPort,
    ) -> None:
        self._repository = repository
        self._tombstones = tombstones
        self._artifacts = artifacts
        self._tasks = tasks

    def discard_orphaned_execution(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        task_id: str,
        result_ref: str,
        idempotency_service: Any,
        idempotency_claim: Any,
    ) -> None:
        """Compensate an execution that crossed a concurrent profile deletion."""

        self._artifacts.delete(principal, result_ref)
        self._tasks.delete_child_tree(
            principal,
            profile_id=profile_id,
            root_task_id=task_id,
            expected_result_ref=result_ref,
        )
        idempotency_service.discard(
            principal,
            idempotency_claim,
            expected_result_ref=result_ref,
            expected_task_id=task_id,
        )

    def compensate_failed_execution_if_unowned(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        profile_id: str,
        run_created_at: float,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int,
        request_ref: str,
        task_id: str | None,
        result_ref: str | None,
        idempotency_service: Any,
        idempotency_claim: Any,
    ) -> bool:
        """Remove exact writes after deletion, stop, expiry, or retry takeover."""

        deleted_at = self._tombstones.deleted_at(principal, profile_id)
        run = self._repository.get(principal, run_id)
        segment = self._repository.get_segment(principal, run_id, sequence)
        deletion_applies = deleted_at is not None and deleted_at >= run_created_at
        owns_execution = bool(
            run is not None
            and not deletion_applies
            and run.status == "active"
            and segment is not None
            and segment.status == "processing"
            and segment.idempotency_key_digest == idempotency_key_digest
            and segment.attempt_count == attempt_count
            and (task_id is None or segment.task_id == task_id)
        )
        if owns_execution:
            return False
        if result_ref:
            self._artifacts.delete(principal, result_ref)
        if request_ref:
            self._artifacts.delete_request_bundle(
                principal,
                profile_id=profile_id,
                request_ref=request_ref,
            )
        if task_id:
            self._tasks.delete_child_tree(
                principal,
                profile_id=profile_id,
                root_task_id=task_id,
                expected_result_ref=result_ref,
            )
        idempotency_service.discard(
            principal,
            idempotency_claim,
            expected_result_ref=result_ref,
            expected_task_id=task_id,
        )
        return True

    def discard_unbound_tasks_if_run_deleted(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        profile_id: str,
        parent_task_id: str,
    ) -> int:
        if self._repository.get(principal, run_id) is not None:
            return 0
        return self._tasks.delete_children_for_parent(
            principal,
            profile_id=profile_id,
            parent_task_id=parent_task_id,
        )


__all__ = ["VoiceLiveRunCompensator"]
