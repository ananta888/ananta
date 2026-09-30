from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.repositories.voice_live_runs import (
    VoiceLiveRunRepository,
    VoiceLiveRunRepositoryConflict,
    VoiceLiveRunRepositoryInProgress,
    VoiceLiveSegmentReservation,
)
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
)
from agent.services.voice_governance_domain import (
    VoicePrincipal,
    validate_identifier,
    voice_idempotency_audio_binding,
    voice_idempotency_key_digest,
    voice_scope_digest,
)
from agent.services.voice_live_run_compensator import VoiceLiveRunCompensator
from agent.services.voice_live_run_contracts import (
    FINALIZATION_GRACE_SECONDS,
    MAX_TIMELINE_ITEMS,
    VoiceLiveRunError,
    VoiceLiveSegmentClaim,
    bounded_int,
    maximum_sequence,
    raise_not_found,
    segment_conflict,
)
from agent.services.voice_live_run_fences import VoiceLiveRunFences
from agent.services.voice_live_run_finalizer import VoiceLiveRunFinalizer
from agent.services.voice_live_run_input_validator import (
    validate_audio_duration,
    validate_run_configuration,
    validate_segment_metadata,
)
from agent.services.voice_live_run_preview_service import (
    VoiceLiveRunPreviewService,
    get_voice_live_run_preview_service,
)
from agent.services.voice_live_run_snapshot_builder import VoiceLiveRunSnapshotBuilder
from agent.services.voice_live_run_start_lease_service import (
    VoiceLiveRunStartLeaseError,
    VoiceLiveRunStartLeaseService,
)
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort
from agent.services.voice_result_artifact_service import (
    VoiceResultArtifactService,
    get_voice_result_artifact_service,
)

_RUN_LOCKS = tuple(threading.Lock() for _index in range(64))


class VoiceLiveRunService:
    """Hub orchestration service for resumable, rolling Voice transcription.

    Snapshot projection, ownership fences, compensation and finalization are
    delegated to focused collaborators built from the same injected ports.
    """

    def __init__(
        self,
        repository: VoiceLiveRunRepository | None = None,
        artifacts: VoiceResultArtifactService | None = None,
        tasks: VoiceLiveRunTaskPort | None = None,
        tombstones: VoiceDeletionTombstoneRepository | None = None,
        start_leases: VoiceLiveRunStartLeaseService | None = None,
        previews: VoiceLiveRunPreviewService | None = None,
        now: Callable[[], float] = time.time,
        *,
        snapshots: VoiceLiveRunSnapshotBuilder | None = None,
        fences: VoiceLiveRunFences | None = None,
        compensator: VoiceLiveRunCompensator | None = None,
        finalizer: VoiceLiveRunFinalizer | None = None,
    ) -> None:
        self._repository = repository or VoiceLiveRunRepository()
        self._artifacts = artifacts or get_voice_result_artifact_service()
        self._tasks = tasks or VoiceLiveRunTaskPort()
        self._tombstones = tombstones or VoiceDeletionTombstoneRepository()
        self._start_leases = start_leases or VoiceLiveRunStartLeaseService(
            tombstones=self._tombstones,
        )
        self._previews = previews or get_voice_live_run_preview_service()
        self._now = now
        self._snapshots = snapshots or VoiceLiveRunSnapshotBuilder(artifacts=self._artifacts)
        self._fences = fences or VoiceLiveRunFences(
            repository=self._repository,
            tombstones=self._tombstones,
            start_leases=self._start_leases,
        )
        self._compensator = compensator or VoiceLiveRunCompensator(
            repository=self._repository,
            tombstones=self._tombstones,
            artifacts=self._artifacts,
            tasks=self._tasks,
        )
        self._finalizer = finalizer or VoiceLiveRunFinalizer(
            repository=self._repository,
            artifacts=self._artifacts,
            tasks=self._tasks,
            previews=self._previews,
            fences=self._fences,
            now=self._now,
        )

    def create(
        self,
        principal: VoicePrincipal,
        *,
        idempotency_key: str,
        lease_token: str,
        source: str,
        profile_id: str,
        configuration_session_id: str | None,
        language: str | None,
        segment_duration_seconds: int,
        max_duration_seconds: int,
        overlap_milliseconds: int,
    ) -> tuple[dict[str, Any], bool]:
        normalized_source = str(source or "").strip().lower()
        if normalized_source not in {"microphone", "system_audio"}:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_source",
                "source must be microphone or system_audio",
                422,
            )
        normalized_profile_id = validate_identifier(profile_id or "default", field="profile_id")
        try:
            start_lease = self._start_leases.verify(
                principal,
                normalized_profile_id,
                lease_token,
            )
        except VoiceLiveRunStartLeaseError as exc:
            raise VoiceLiveRunError(exc.code, exc.message, exc.status_code) from exc
        configuration = validate_run_configuration(
            configuration_session_id=configuration_session_id,
            language=language,
            segment_duration_seconds=segment_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            overlap_milliseconds=overlap_milliseconds,
        )
        normalized_session_id = configuration.configuration_session_id
        normalized_language = configuration.language
        segment_seconds = configuration.segment_duration_seconds
        max_seconds = configuration.max_duration_seconds
        overlap_ms = configuration.overlap_milliseconds
        scope_digest = voice_scope_digest(principal, normalized_profile_id)
        key_digest = voice_idempotency_key_digest(
            idempotency_key,
            scope_digest=scope_digest,
            operation="voice.live_run.create",
        )
        now = self._now()
        parent_task_id = f"voice-live-run-task-{key_digest[:32]}"
        run = VoiceLiveRunDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            profile_id=normalized_profile_id,
            configuration_session_id=normalized_session_id,
            idempotency_key_digest=key_digest,
            parent_task_id=parent_task_id,
            source=normalized_source,
            language=normalized_language,
            segment_duration_seconds=segment_seconds,
            max_duration_seconds=max_seconds,
            overlap_milliseconds=overlap_ms,
            last_heartbeat_at=now,
            capture_deadline_at=now + max_seconds,
            expires_at=now + max_seconds + FINALIZATION_GRACE_SECONDS,
            created_at=now,
            updated_at=now,
        )
        persisted, replayed = self._repository.create(run)
        self._assert_create_replay_matches(
            persisted,
            source=normalized_source,
            profile_id=normalized_profile_id,
            configuration_session_id=normalized_session_id,
            language=normalized_language,
            segment_duration_seconds=segment_seconds,
            max_duration_seconds=max_seconds,
            overlap_milliseconds=overlap_ms,
        )
        try:
            self._fences.assert_create_allowed(
                principal,
                persisted,
                expected_generation=start_lease.generation,
            )
            # Idempotent replays may outlive task retention.  Only an active
            # orchestration can recover a missing parent; terminal runs must
            # never be resurrected as fresh in-progress work.
            if persisted.status in {"active", "finalizing"}:
                self._tasks.ensure_parent(principal, persisted)
            self._fences.assert_create_allowed(
                principal,
                persisted,
                expected_generation=start_lease.generation,
            )
            snapshot = self.snapshot(principal, persisted.id)
            self._fences.assert_create_allowed(
                principal,
                persisted,
                expected_generation=start_lease.generation,
            )
            return snapshot, replayed
        except VoiceLiveRunError:
            self._tasks.delete_child_tree(
                principal,
                profile_id=persisted.profile_id,
                root_task_id=persisted.parent_task_id,
            )
            self._repository.delete_run_identity(
                principal,
                run_id=persisted.id,
                profile_id=persisted.profile_id,
                parent_task_id=persisted.parent_task_id,
                created_at=persisted.created_at,
            )
            raise

    def snapshot(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        after_sequence: int = -1,
        after_revision: int | None = None,
        limit: int = MAX_TIMELINE_ITEMS,
        include_text: bool = True,
    ) -> dict[str, Any]:
        run = self._require_active_or_terminal(principal, run_id)
        segments = self._repository.list_segments(principal, run.id)
        return self._snapshots.build(
            principal,
            run,
            segments,
            after_sequence=after_sequence,
            after_revision=after_revision,
            limit=limit,
            include_text=include_text,
        )

    def heartbeat(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        last_local_sequence: int | None,
        gaps: list[int] | tuple[int, ...],
    ) -> dict[str, Any]:
        run = self._require_active_or_terminal(principal, run_id)
        if run.status != "active":
            raise VoiceLiveRunError(
                "voice_live_run.not_active",
                "voice live run is not active",
                409,
            )
        if not isinstance(gaps, (list, tuple)):
            raise VoiceLiveRunError(
                "voice_live_run.invalid_gaps",
                "gaps must be an array of segment sequence integers",
                422,
            )
        max_sequence = maximum_sequence(run)
        normalized_last = (
            bounded_int(
                last_local_sequence,
                field="last_local_sequence",
                minimum=-1,
                maximum=max_sequence,
            )
            if last_local_sequence is not None
            else None
        )
        normalized_gaps = tuple(
            sorted(
                {
                    bounded_int(
                        item,
                        field="gap_sequence",
                        minimum=0,
                        maximum=max_sequence,
                    )
                    for item in list(gaps or [])[:MAX_TIMELINE_ITEMS]
                }
            )
        )
        updated = self._repository.heartbeat(
            principal,
            run.id,
            last_local_sequence=normalized_last,
            reported_gap_sequences=normalized_gaps,
            now=self._now(),
        )
        if updated is None:
            raise_not_found()
        return self.snapshot(principal, run.id, include_text=False)

    def reserve_audio_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key: str,
        audio: bytes,
        started_at_ms: int,
        ended_at_ms: int,
        duration_ms: int,
        overlap_milliseconds: int,
    ) -> VoiceLiveSegmentClaim:
        run = self._require_active(principal, run_id)
        metadata = validate_segment_metadata(
            run,
            sequence=sequence,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            duration_ms=duration_ms,
            overlap_milliseconds=overlap_milliseconds,
        )
        operation = f"voice.live_run.segment:{run.id}:{metadata['sequence']}"
        scope_digest = voice_scope_digest(principal, run.profile_id)
        key_digest = voice_idempotency_key_digest(
            idempotency_key,
            scope_digest=scope_digest,
            operation=operation,
        )
        audio_binding = voice_idempotency_audio_binding(
            principal,
            operation=operation,
            idempotency_key=idempotency_key,
            audio=audio,
        )
        validate_audio_duration(audio, metadata["duration_ms"])
        reservation = self._reserve(
            principal,
            run,
            idempotency_key_digest=key_digest,
            audio_binding=audio_binding,
            **metadata,
        )
        self._previews.cleanup_segment(
            principal,
            run.id,
            metadata["sequence"],
        )
        return VoiceLiveSegmentClaim(
            run=run,
            reservation=reservation,
            idempotency_key_digest=key_digest,
            effective_idempotency_key=(f"live-segment-{key_digest}-attempt-{reservation.segment.attempt_count}"),
        )

    def register_result_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key: str,
        result_ref: str,
        started_at_ms: int,
        ended_at_ms: int,
        duration_ms: int,
        overlap_milliseconds: int,
    ) -> dict[str, Any]:
        run = self._require_active(principal, run_id)
        normalized_ref = validate_identifier(result_ref, field="result_ref", max_length=200)
        linked_artifact = self._artifacts.get(principal, normalized_ref)
        if linked_artifact.get("profile_id") != run.profile_id:
            raise VoiceLiveRunError(
                "voice_live_run.result_profile_conflict",
                "result_ref belongs to a different Voice profile",
                409,
            )
        metadata = validate_segment_metadata(
            run,
            sequence=sequence,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            duration_ms=duration_ms,
            overlap_milliseconds=overlap_milliseconds,
        )
        operation = f"voice.live_run.segment:{run.id}:{metadata['sequence']}"
        key_digest = voice_idempotency_key_digest(
            idempotency_key,
            scope_digest=voice_scope_digest(principal, run.profile_id),
            operation=operation,
        )
        reservation = self._reserve(
            principal,
            run,
            idempotency_key_digest=key_digest,
            audio_binding=None,
            **metadata,
        )
        if reservation.replayed:
            if reservation.segment.result_ref != normalized_ref:
                raise VoiceLiveRunError(
                    "voice_live_run.segment_conflict",
                    "segment sequence is already bound to a different result",
                    409,
                )
            self._previews.cleanup_segment(
                principal,
                run.id,
                metadata["sequence"],
            )
            return self.snapshot(principal, run.id, include_text=False)
        task: VoiceDelegationTask | None = None
        try:
            self.assert_segment_execution_allowed(
                principal,
                run.id,
                profile_id=run.profile_id,
                run_created_at=run.created_at,
                sequence=metadata["sequence"],
                idempotency_key_digest=key_digest,
                attempt_count=reservation.segment.attempt_count,
                expected_task_id=None,
            )
            task = self._tasks.create_link_child(
                principal,
                run,
                sequence=metadata["sequence"],
                result_ref=normalized_ref,
                idempotency_key=(f"live-link-{key_digest}-attempt-{reservation.segment.attempt_count}"),
            )
            self.bind_segment_task(
                principal,
                run.id,
                sequence=metadata["sequence"],
                idempotency_key_digest=key_digest,
                attempt_count=reservation.segment.attempt_count,
                task_id=task.task_id,
            )
            self.assert_segment_execution_allowed(
                principal,
                run.id,
                profile_id=run.profile_id,
                run_created_at=run.created_at,
                sequence=metadata["sequence"],
                idempotency_key_digest=key_digest,
                attempt_count=reservation.segment.attempt_count,
                expected_task_id=task.task_id,
            )
            self._tasks.complete_child(task, result_ref=normalized_ref)
            self.assert_segment_execution_allowed(
                principal,
                run.id,
                profile_id=run.profile_id,
                run_created_at=run.created_at,
                sequence=metadata["sequence"],
                idempotency_key_digest=key_digest,
                attempt_count=reservation.segment.attempt_count,
                expected_task_id=task.task_id,
            )
            self.complete_segment(
                principal,
                run.id,
                sequence=metadata["sequence"],
                idempotency_key_digest=key_digest,
                attempt_count=reservation.segment.attempt_count,
                task_id=task.task_id,
                result_ref=normalized_ref,
            )
            self._previews.cleanup_segment(
                principal,
                run.id,
                metadata["sequence"],
            )
        except Exception as exc:
            if task is not None:
                try:
                    self.assert_segment_execution_allowed(
                        principal,
                        run.id,
                        profile_id=run.profile_id,
                        run_created_at=run.created_at,
                        sequence=metadata["sequence"],
                        idempotency_key_digest=key_digest,
                        attempt_count=reservation.segment.attempt_count,
                        expected_task_id=task.task_id,
                    )
                except VoiceLiveRunError:
                    self._tasks.delete_child_tree(
                        principal,
                        profile_id=run.profile_id,
                        root_task_id=task.task_id,
                        expected_result_ref=normalized_ref,
                    )
            self.fail_segment(
                principal,
                run.id,
                sequence=metadata["sequence"],
                idempotency_key_digest=key_digest,
                attempt_count=reservation.segment.attempt_count,
                failure_code=self.failure_code(exc),
            )
            raise
        return self.snapshot(principal, run.id, include_text=False)

    def complete_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
        result_ref: str,
    ) -> VoiceLiveRunSegmentDB:
        try:
            return self._repository.complete_segment(
                principal,
                run_id,
                sequence,
                idempotency_key_digest=idempotency_key_digest,
                attempt_count=attempt_count,
                task_id=task_id,
                result_ref=result_ref,
            )
        except LookupError:
            raise_not_found()
        except VoiceLiveRunRepositoryConflict as exc:
            raise segment_conflict(str(exc)) from exc

    def publish_provisional(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
        result_ref: str,
        correction_configuration_digest: str | None,
        correction_spec_ref: str | None,
        correction_requested: bool,
    ) -> VoiceLiveRunSegmentDB:
        try:
            return self._repository.publish_provisional(
                principal,
                run_id,
                sequence,
                idempotency_key_digest=idempotency_key_digest,
                attempt_count=attempt_count,
                task_id=task_id,
                result_ref=result_ref,
                correction_configuration_digest=correction_configuration_digest,
                correction_spec_ref=correction_spec_ref,
                correction_requested=correction_requested,
                now=self._now(),
            )
        except LookupError:
            raise_not_found()
        except VoiceLiveRunRepositoryConflict as exc:
            raise segment_conflict(str(exc)) from exc

    def bind_segment_task(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
    ) -> None:
        try:
            self._repository.bind_segment_task(
                principal,
                run_id,
                sequence,
                idempotency_key_digest=idempotency_key_digest,
                attempt_count=attempt_count,
                task_id=task_id,
            )
        except LookupError:
            raise_not_found()
        except VoiceLiveRunRepositoryConflict as exc:
            raise segment_conflict(str(exc)) from exc

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

        self._compensator.discard_orphaned_execution(
            principal,
            profile_id=profile_id,
            task_id=task_id,
            result_ref=result_ref,
            idempotency_service=idempotency_service,
            idempotency_claim=idempotency_claim,
        )

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

        self._fences.assert_segment_execution_allowed(
            principal,
            run_id,
            profile_id=profile_id,
            run_created_at=run_created_at,
            sequence=sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            expected_task_id=expected_task_id,
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

        return self._compensator.compensate_failed_execution_if_unowned(
            principal,
            run_id,
            profile_id=profile_id,
            run_created_at=run_created_at,
            sequence=sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            request_ref=request_ref,
            task_id=task_id,
            result_ref=result_ref,
            idempotency_service=idempotency_service,
            idempotency_claim=idempotency_claim,
        )

    def compensate_completed_execution_if_unowned(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        profile_id: str,
        run_created_at: float,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
        result_ref: str,
        idempotency_service: Any,
        idempotency_claim: Any,
    ) -> bool:
        """Compensate a late provider result rejected by the segment ledger."""

        return self.compensate_failed_execution_if_unowned(
            principal,
            run_id,
            profile_id=profile_id,
            run_created_at=run_created_at,
            sequence=sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            request_ref="",
            task_id=task_id,
            result_ref=result_ref,
            idempotency_service=idempotency_service,
            idempotency_claim=idempotency_claim,
        )

    def discard_unbound_tasks_if_run_deleted(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        profile_id: str,
        parent_task_id: str,
    ) -> int:
        return self._compensator.discard_unbound_tasks_if_run_deleted(
            principal,
            run_id,
            profile_id=profile_id,
            parent_task_id=parent_task_id,
        )

    def fail_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key_digest: str,
        attempt_count: int | None = None,
        failure_code: str,
        task_id: str | None = None,
    ) -> None:
        self._repository.fail_segment(
            principal,
            run_id,
            sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            failure_code=failure_code,
            task_id=task_id,
        )

    def stop(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        last_sequence: int | None,
        reason: str,
    ) -> dict[str, Any]:
        lock = _RUN_LOCKS[int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) % len(_RUN_LOCKS)]
        with lock:
            run = self._require_active_or_terminal(principal, run_id)
            expected = (
                bounded_int(
                    last_sequence,
                    field="last_sequence",
                    minimum=-1,
                    maximum=maximum_sequence(run),
                )
                if last_sequence is not None
                else run.last_local_sequence
            )
            self._finalizer.finalize(
                principal,
                run,
                expected_last_sequence=expected,
                reason=reason,
            )
            return self.snapshot(principal, run.id)

    @staticmethod
    def failure_code(exc: BaseException) -> str:
        value = str(getattr(exc, "code", "") or "").strip()
        if value:
            return value[:120]
        return f"segment_{type(exc).__name__.lower()}"[:120]

    def _reserve(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        **values: Any,
    ) -> VoiceLiveSegmentReservation:
        try:
            return self._repository.reserve_segment(
                principal,
                run.id,
                now=self._now(),
                **values,
            )
        except VoiceLiveRunRepositoryInProgress as exc:
            raise VoiceLiveRunError(
                "voice_live_run.segment_in_progress",
                str(exc),
                409,
                retriable=True,
            ) from exc
        except VoiceLiveRunRepositoryConflict as exc:
            raise segment_conflict(str(exc)) from exc

    def _require_active(self, principal: VoicePrincipal, run_id: str) -> VoiceLiveRunDB:
        run = self._require_active_or_terminal(principal, run_id)
        if run.status != "active":
            raise VoiceLiveRunError(
                "voice_live_run.not_active",
                "voice live run is not active",
                409,
            )
        return run

    def _require_active_or_terminal(
        self,
        principal: VoicePrincipal,
        run_id: str,
    ) -> VoiceLiveRunDB:
        normalized_id = validate_identifier(run_id, field="run_id", max_length=200)
        run = self._repository.get(principal, normalized_id)
        if run is None:
            raise_not_found()
        if run.status in {"active", "finalizing"} and run.expires_at <= self._now():
            expired = self._repository.mark_expired(
                principal,
                run.id,
                now=self._now(),
            )
            if expired is not None and expired.status == "expired":
                self._previews.cleanup_run(
                    principal,
                    expired.id,
                    reason_code="voice_live_preview_run_expired",
                )
                self._cancel_expired_segment_tasks(principal, expired)
                self._tasks.expire_parent(expired)
                run = expired
        elif run.status == "expired":
            self._previews.cleanup_run(
                principal,
                run.id,
                reason_code="voice_live_preview_run_expired",
            )
            self._cancel_expired_segment_tasks(principal, run)
            self._tasks.expire_parent(run)
        return run

    def _cancel_expired_segment_tasks(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
    ) -> None:
        for segment in self._repository.list_segments(principal, run.id):
            if segment.failure_code == "run_expired" and segment.task_id:
                self._tasks.cancel_child(
                    segment.task_id,
                    reason_code="voice_live_run_expired",
                )

    @staticmethod
    def _assert_create_replay_matches(run: VoiceLiveRunDB, **expected: Any) -> None:
        if any(getattr(run, key) != value for key, value in expected.items()):
            raise VoiceLiveRunError(
                "voice_live_run.idempotency_conflict",
                "Idempotency-Key was already used with a different live-run configuration",
                409,
            )


voice_live_run_service = VoiceLiveRunService()


def get_voice_live_run_service() -> VoiceLiveRunService:
    return voice_live_run_service


__all__ = [
    "VoiceLiveRunError",
    "VoiceLiveRunService",
    "VoiceLiveSegmentClaim",
    "get_voice_live_run_service",
    "voice_live_run_service",
]
