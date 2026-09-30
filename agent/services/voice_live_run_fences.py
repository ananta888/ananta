"""Ownership fences that guard live-run create, segment execution and finalization."""

from __future__ import annotations

from agent.db_models import VoiceLiveRunDB
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.repositories.voice_live_runs import VoiceLiveRunRepository
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_contracts import VoiceLiveRunError
from agent.services.voice_live_run_start_lease_service import (
    VoiceLiveRunStartLeaseError,
    VoiceLiveRunStartLeaseService,
)


class VoiceLiveRunFences:
    """Reject work that crossed a profile deletion, stop, expiry or takeover."""

    def __init__(
        self,
        *,
        repository: VoiceLiveRunRepository,
        tombstones: VoiceDeletionTombstoneRepository,
        start_leases: VoiceLiveRunStartLeaseService,
    ) -> None:
        self._repository = repository
        self._tombstones = tombstones
        self._start_leases = start_leases

    def assert_segment_execution_allowed(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        profile_id: str,
        run_created_at: float,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int,
        expected_task_id: str | None,
    ) -> None:
        """Fence completion against deletion, expiry, stop, or retry takeover."""

        deleted_at = self._tombstones.deleted_at(principal, profile_id)
        run = self._repository.get(principal, run_id)
        segment = self._repository.get_segment(principal, run_id, sequence)
        deleted = (deleted_at is not None and deleted_at >= run_created_at) or run is None
        owns_execution = bool(
            run is not None
            and run.status == "active"
            and segment is not None
            and segment.status == "processing"
            and segment.idempotency_key_digest == idempotency_key_digest
            and segment.attempt_count == attempt_count
            and segment.task_id == expected_task_id
        )
        if deleted:
            raise VoiceLiveRunError(
                "voice_live_run.deleted_during_processing",
                "voice live run was deleted while the segment was processing",
                409,
            )
        if run.profile_id != profile_id or run.created_at != run_created_at:
            raise VoiceLiveRunError(
                "voice_live_run.completion_fence_conflict",
                "voice live run identity changed while the segment was processing",
                409,
            )
        if not owns_execution:
            raise VoiceLiveRunError(
                "voice_live_run.execution_no_longer_owned",
                "voice live segment was stopped, expired, or superseded while processing",
                409,
            )

    def assert_finalization_allowed(
        self,
        principal: VoicePrincipal,
        expected: VoiceLiveRunDB,
        *,
        expected_version: int,
    ) -> None:
        deleted_at = self._tombstones.deleted_at(principal, expected.profile_id)
        current = self._repository.get(principal, expected.id)
        if (deleted_at is not None and deleted_at >= expected.created_at) or current is None:
            raise VoiceLiveRunError(
                "voice_live_run.deleted_during_finalization",
                "voice live run was deleted while finalizing",
                409,
            )
        if (
            current.status != "finalizing"
            or current.profile_id != expected.profile_id
            or current.created_at != expected.created_at
            or current.version != expected_version
        ):
            raise VoiceLiveRunError(
                "voice_live_run.finalization_fence_conflict",
                "voice live run finalization ownership changed",
                409,
            )

    def assert_create_allowed(
        self,
        principal: VoicePrincipal,
        expected: VoiceLiveRunDB,
        *,
        expected_generation: str,
    ) -> None:
        try:
            self._start_leases.assert_generation(
                principal,
                expected.profile_id,
                expected_generation=expected_generation,
            )
        except VoiceLiveRunStartLeaseError as exc:
            raise VoiceLiveRunError(
                "voice_live_run.deleted_during_create",
                "voice live run profile was deleted while creating its parent task",
                409,
            ) from exc
        deleted_at = self._tombstones.deleted_at(principal, expected.profile_id)
        current = self._repository.get(principal, expected.id)
        if (deleted_at is not None and deleted_at >= expected.created_at) or current is None:
            raise VoiceLiveRunError(
                "voice_live_run.deleted_during_create",
                "voice live run profile was deleted while creating its parent task",
                409,
            )
        if (
            current.profile_id != expected.profile_id
            or current.created_at != expected.created_at
            or current.parent_task_id != expected.parent_task_id
        ):
            raise VoiceLiveRunError(
                "voice_live_run.create_fence_conflict",
                "voice live run identity changed while creating its parent task",
                409,
            )


__all__ = ["VoiceLiveRunFences"]
