from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from sqlalchemy.exc import IntegrityError

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
    VoiceGovernanceError,
    VoicePrincipal,
    validate_identifier,
    validate_text,
    voice_idempotency_audio_binding,
    voice_idempotency_key_digest,
    voice_scope_digest,
)
from agent.services.voice_live_run_start_lease_service import (
    VoiceLiveRunStartLeaseError,
    VoiceLiveRunStartLeaseService,
)
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort
from agent.services.voice_result_artifact_service import (
    VoiceResultArtifactService,
    get_voice_result_artifact_service,
)

_MAX_RUN_SECONDS = 28_800
_MIN_SEGMENT_SECONDS = 60
_MAX_SEGMENT_SECONDS = 120
_MAX_OVERLAP_MILLISECONDS = 5_000
_FINALIZATION_GRACE_SECONDS = 3_600
_MAX_TIMELINE_ITEMS = 600
_RUN_LOCKS = tuple(threading.Lock() for _index in range(64))
_WORD_NORMALIZER = re.compile(r"(^\W+|\W+$)", re.UNICODE)


class VoiceLiveRunError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        *,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retriable = retriable


@dataclass(frozen=True)
class VoiceLiveSegmentClaim:
    run: VoiceLiveRunDB
    reservation: VoiceLiveSegmentReservation
    idempotency_key_digest: str
    effective_idempotency_key: str


class VoiceLiveRunService:
    """Hub orchestration service for resumable, rolling Voice transcription."""

    def __init__(
        self,
        repository: VoiceLiveRunRepository | None = None,
        artifacts: VoiceResultArtifactService | None = None,
        tasks: VoiceLiveRunTaskPort | None = None,
        tombstones: VoiceDeletionTombstoneRepository | None = None,
        start_leases: VoiceLiveRunStartLeaseService | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or VoiceLiveRunRepository()
        self._artifacts = artifacts or get_voice_result_artifact_service()
        self._tasks = tasks or VoiceLiveRunTaskPort()
        self._tombstones = tombstones or VoiceDeletionTombstoneRepository()
        self._start_leases = start_leases or VoiceLiveRunStartLeaseService(
            tombstones=self._tombstones,
        )
        self._now = now

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
        normalized_session_id = (
            validate_identifier(
                configuration_session_id,
                field="configuration_session_id",
                max_length=128,
            )
            if configuration_session_id
            else None
        )
        normalized_language = validate_text(
            language,
            field="language",
            max_length=32,
            required=False,
        )
        segment_seconds = self._bounded_int(
            segment_duration_seconds,
            field="segment_duration_seconds",
            minimum=_MIN_SEGMENT_SECONDS,
            maximum=_MAX_SEGMENT_SECONDS,
        )
        max_seconds = self._bounded_int(
            max_duration_seconds,
            field="max_duration_seconds",
            minimum=_MIN_SEGMENT_SECONDS,
            maximum=_MAX_RUN_SECONDS,
        )
        if max_seconds < segment_seconds:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_max_duration_seconds",
                "max_duration_seconds must be at least one segment duration",
                422,
            )
        overlap_ms = self._bounded_int(
            overlap_milliseconds,
            field="overlap_milliseconds",
            minimum=0,
            maximum=_MAX_OVERLAP_MILLISECONDS,
        )
        if overlap_ms >= segment_seconds * 1000:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_overlap",
                "overlap_milliseconds must be shorter than one segment",
                422,
            )
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
            expires_at=now + max_seconds + _FINALIZATION_GRACE_SECONDS,
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
            self._assert_create_allowed(
                principal,
                persisted,
                expected_generation=start_lease.generation,
            )
            # Idempotent replays may outlive task retention.  Only an active
            # orchestration can recover a missing parent; terminal runs must
            # never be resurrected as fresh in-progress work.
            if persisted.status in {"active", "finalizing"}:
                self._tasks.ensure_parent(principal, persisted)
            self._assert_create_allowed(
                principal,
                persisted,
                expected_generation=start_lease.generation,
            )
            snapshot = self.snapshot(principal, persisted.id)
            self._assert_create_allowed(
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
        limit: int = _MAX_TIMELINE_ITEMS,
        include_text: bool = True,
    ) -> dict[str, Any]:
        run = self._require_active_or_terminal(principal, run_id)
        segments = self._repository.list_segments(principal, run.id)
        gaps = self._gap_sequences(run, segments)
        bounded_limit = max(1, min(int(limit), _MAX_TIMELINE_ITEMS))
        if after_revision is None:
            result_by_sequence = self._load_segment_results(
                principal,
                segments,
                include_text=include_text,
            )
            all_items = self._timeline_items(
                segments,
                result_by_sequence,
                gaps,
                gap_timeline_revision=run.timeline_revision,
            )
            page_candidates = [
                item for item in all_items if int(item["sequence"]) > int(after_sequence)
            ]
            page = page_candidates[:bounded_limit]
            next_after_revision = None
            completed_texts = [
                (
                    segment,
                    str((result_by_sequence.get(segment.sequence) or {}).get("text") or ""),
                )
                for segment in segments
                if segment.status == "completed"
            ]
            composed = self._compose_transcript(completed_texts)
        else:
            normalized_revision = max(-1, int(after_revision))
            # Revision polling deliberately excludes synthetic gaps: the full
            # gap set is returned separately and has no stable row identity.
            metadata_items = self._timeline_items(
                segments,
                {},
                [],
                gap_timeline_revision=run.timeline_revision,
            )
            page_candidates = [
                item
                for item in metadata_items
                if int(item.get("timeline_revision") or 0) > normalized_revision
            ]
            page_candidates.sort(
                key=lambda item: (
                    int(item.get("timeline_revision") or 0),
                    int(item["sequence"]),
                )
            )
            page = page_candidates[:bounded_limit]
            selected_sequences = {int(item["sequence"]) for item in page}
            selected_segments = tuple(
                segment for segment in segments if segment.sequence in selected_sequences
            )
            result_by_sequence = self._load_segment_results(
                principal,
                selected_segments,
                include_text=include_text,
            )
            for item in page:
                result = result_by_sequence.get(int(item["sequence"])) or {}
                item["text"] = result.get("text")
                item["provider"] = result.get("provider")
                item["model"] = result.get("model")
            next_after_revision = (
                max(int(item.get("timeline_revision") or normalized_revision) for item in page)
                if page
                else normalized_revision
            )
            composed = None
        acknowledged = self._acknowledged_through(segments)
        next_sequence = acknowledged + 1
        max_sequence = max(
            [
                *[segment.sequence for segment in segments],
                *gaps,
                int(run.last_local_sequence if run.last_local_sequence is not None else -1),
            ],
            default=-1,
        )
        return {
            "run": self._public_run(run),
            "segments": page,
            "composed_transcript": composed if include_text and after_revision is None else None,
            "gaps": gaps,
            "resume": {
                "acknowledged_through_sequence": acknowledged,
                "next_sequence": next_sequence,
                "last_seen_sequence": max_sequence,
                "pending_sequences": [segment.sequence for segment in segments if segment.status == "processing"],
                "failed_sequences": [segment.sequence for segment in segments if segment.status == "failed"],
                "pending_correction_sequences": [
                    segment.sequence
                    for segment in segments
                    if segment.correction_status in {"queued", "processing"}
                ],
            },
            "page": {
                "after_sequence": int(after_sequence),
                "after_revision": int(after_revision) if after_revision is not None else None,
                "limit": bounded_limit,
                "has_more": len(page_candidates) > len(page),
                "next_after_sequence": int(page[-1]["sequence"]) if page else int(after_sequence),
                "next_after_revision": next_after_revision,
            },
        }

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
        max_sequence = self._maximum_sequence(run)
        normalized_last = (
            self._bounded_int(
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
                    self._bounded_int(
                        item,
                        field="gap_sequence",
                        minimum=0,
                        maximum=max_sequence,
                    )
                    for item in list(gaps or [])[:_MAX_TIMELINE_ITEMS]
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
            self._not_found()
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
        metadata = self._validate_segment_metadata(
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
        self._validate_audio_duration(audio, metadata["duration_ms"])
        reservation = self._reserve(
            principal,
            run,
            idempotency_key_digest=key_digest,
            audio_binding=audio_binding,
            **metadata,
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
        metadata = self._validate_segment_metadata(
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
            self._not_found()
        except VoiceLiveRunRepositoryConflict as exc:
            raise self._conflict(str(exc)) from exc

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
            self._not_found()
        except VoiceLiveRunRepositoryConflict as exc:
            raise self._conflict(str(exc)) from exc

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
            self._not_found()
        except VoiceLiveRunRepositoryConflict as exc:
            raise self._conflict(str(exc)) from exc

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
        if self._repository.get(principal, run_id) is not None:
            return 0
        return self._tasks.delete_children_for_parent(
            principal,
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
                self._bounded_int(
                    last_sequence,
                    field="last_sequence",
                    minimum=-1,
                    maximum=self._maximum_sequence(run),
                )
                if last_sequence is not None
                else run.last_local_sequence
            )
            try:
                run, replayed = self._repository.begin_finalize(
                    principal,
                    run.id,
                    expected_last_sequence=expected,
                    now=self._now(),
                )
            except VoiceLiveRunRepositoryInProgress as exc:
                raise VoiceLiveRunError(
                    "voice_live_run.segments_in_flight",
                    str(exc),
                    409,
                    retriable=True,
                ) from exc
            except VoiceLiveRunRepositoryConflict as exc:
                raise self._conflict(str(exc)) from exc
            if replayed:
                if run.final_result_ref:
                    self._tasks.complete_parent(run, result_ref=run.final_result_ref)
                return self.snapshot(principal, run.id)
            finalization_version = run.version
            artifact: dict[str, Any] | None = None
            try:
                self._assert_finalization_allowed(
                    principal,
                    run,
                    expected_version=finalization_version,
                )
                segments = self._repository.list_segments(principal, run.id)
                for segment in segments:
                    if (
                        segment.status == "failed"
                        and segment.failure_code == "processing_lease_expired"
                        and segment.task_id
                    ):
                        self._tasks.cancel_child(
                            segment.task_id,
                            reason_code="voice_live_segment_lease_expired",
                        )
                    if (
                        segment.correction_status == "failed"
                        and segment.correction_failure_code == "correction_lease_expired"
                        and segment.correction_task_id
                    ):
                        self._tasks.cancel_child(
                            segment.correction_task_id,
                            reason_code="voice_live_correction_lease_expired",
                        )
                gaps = self._gap_sequences(run, segments)
                artifact = self._get_or_create_final_artifact(
                    principal,
                    run,
                    segments=segments,
                    gaps=gaps,
                )
                self._assert_finalization_allowed(
                    principal,
                    run,
                    expected_version=finalization_version,
                )
                completed = self._repository.complete_finalize(
                    principal,
                    run.id,
                    expected_version=finalization_version,
                    result_ref=str(artifact["id"]),
                    has_gaps=bool(gaps),
                    stop_reason=str(reason or "user_stop"),
                    now=self._now(),
                )
                self._tasks.complete_parent(completed, result_ref=str(artifact["id"]))
            except Exception:
                current = self._repository.get(principal, run.id)
                ownership_transferred = bool(
                    current is not None and current.status == "finalizing" and current.version != finalization_version
                )
                if (
                    artifact is not None
                    and not (
                        current is not None
                        and current.status in {"completed", "completed_with_gaps"}
                        and current.final_result_ref == str(artifact["id"])
                    )
                    and not ownership_transferred
                ):
                    self._artifacts.delete(principal, str(artifact["id"]))
                self._repository.abort_finalize(
                    principal,
                    run.id,
                    expected_version=finalization_version,
                    now=self._now(),
                )
                raise
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
            raise self._conflict(str(exc)) from exc

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
            self._not_found()
        if run.status in {"active", "finalizing"} and run.expires_at <= self._now():
            expired = self._repository.mark_expired(
                principal,
                run.id,
                now=self._now(),
            )
            if expired is not None and expired.status == "expired":
                self._cancel_expired_segment_tasks(principal, expired)
                self._tasks.expire_parent(expired)
                run = expired
        elif run.status == "expired":
            self._cancel_expired_segment_tasks(principal, run)
            self._tasks.expire_parent(run)
        return run

    def _assert_finalization_allowed(
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

    def _assert_create_allowed(
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

    def _validate_segment_metadata(
        self,
        run: VoiceLiveRunDB,
        *,
        sequence: int,
        started_at_ms: int,
        ended_at_ms: int,
        duration_ms: int,
        overlap_milliseconds: int,
    ) -> dict[str, int]:
        normalized_sequence = self._bounded_int(
            sequence,
            field="sequence",
            minimum=0,
            maximum=self._maximum_sequence(run),
        )
        started = self._bounded_int(
            started_at_ms,
            field="started_at_ms",
            minimum=0,
            maximum=run.max_duration_seconds * 1000,
        )
        ended = self._bounded_int(
            ended_at_ms,
            field="ended_at_ms",
            minimum=1,
            maximum=run.max_duration_seconds * 1000,
        )
        duration = self._bounded_int(
            duration_ms,
            field="duration_ms",
            minimum=1,
            maximum=run.segment_duration_seconds * 1000,
        )
        overlap = self._bounded_int(
            overlap_milliseconds,
            field="overlap_milliseconds",
            minimum=0,
            maximum=run.overlap_milliseconds,
        )
        if ended <= started or ended - started > run.segment_duration_seconds * 1000:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_segment_timeline",
                "segment timeline is invalid or exceeds the configured duration",
                422,
            )
        if abs(duration - (ended - started)) > 500:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_segment_duration",
                "duration_ms must match the segment timeline",
                422,
            )
        return {
            "sequence": normalized_sequence,
            "started_at_ms": started,
            "ended_at_ms": ended,
            "duration_ms": duration,
            "overlap_milliseconds": overlap,
        }

    @staticmethod
    def _validate_audio_duration(audio: bytes, declared_duration_ms: int) -> None:
        """Validate the supported WAV/PCM segment against its timeline metadata."""

        if not audio:
            raise VoiceLiveRunError(
                "voice_live_run.empty_audio",
                "segment audio must not be empty",
                422,
            )
        actual_duration_ms: float
        if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
            try:
                with wave.open(io.BytesIO(audio), "rb") as source:
                    if source.getnchannels() != 1 or source.getsampwidth() != 2:
                        raise VoiceLiveRunError(
                            "voice_live_run.invalid_audio_format",
                            "WAV segments must contain mono 16-bit PCM",
                            422,
                        )
                    frame_rate = source.getframerate()
                    if frame_rate <= 0:
                        raise ValueError("invalid WAV frame rate")
                    actual_duration_ms = source.getnframes() * 1000.0 / frame_rate
            except (EOFError, wave.Error, ValueError) as exc:
                raise VoiceLiveRunError(
                    "voice_live_run.invalid_audio_format",
                    "segment must be a valid PCM WAV or raw PCM16/16kHz/mono payload",
                    422,
                ) from exc
        else:
            # The direct raw path is deliberately strict and matches the live
            # capture transport contract: PCM16, mono, 16 kHz.
            if len(audio) % 2:
                raise VoiceLiveRunError(
                    "voice_live_run.invalid_audio_format",
                    "raw PCM16 payload must contain complete samples",
                    422,
                )
            actual_duration_ms = len(audio) * 1000.0 / (16_000 * 2)
        if actual_duration_ms > _MAX_SEGMENT_SECONDS * 1000 + 1:
            raise VoiceLiveRunError(
                "voice_live_run.audio_too_long",
                "segment audio exceeds 120 seconds",
                422,
            )
        if abs(actual_duration_ms - declared_duration_ms) > 750:
            raise VoiceLiveRunError(
                "voice_live_run.audio_duration_mismatch",
                "segment audio duration does not match duration_ms",
                422,
            )

    def _load_segment_results(
        self,
        principal: VoicePrincipal,
        segments: tuple[VoiceLiveRunSegmentDB, ...],
        *,
        include_text: bool,
    ) -> dict[int, dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        if not include_text:
            return results
        for segment in segments:
            if segment.status != "completed" or not segment.result_ref:
                continue
            try:
                artifact = self._artifacts.get(principal, segment.result_ref)
            except VoiceGovernanceError:
                continue
            result_value = artifact.get("result")
            result = dict(result_value) if isinstance(result_value, Mapping) else {}
            results[segment.sequence] = {
                "text": str(result.get("transcript") or result.get("text") or "").strip(),
                "provider": result.get("provider"),
                "model": result.get("model"),
            }
        return results

    @staticmethod
    def _timeline_items(
        segments: tuple[VoiceLiveRunSegmentDB, ...],
        result_by_sequence: Mapping[int, Mapping[str, Any]],
        gaps: list[int],
        *,
        gap_timeline_revision: int,
    ) -> list[dict[str, Any]]:
        items = [
            {
                "id": segment.id,
                "sequence": segment.sequence,
                "status": segment.status,
                "task_id": segment.task_id,
                "result_ref": segment.result_ref,
                "provisional_result_ref": segment.provisional_result_ref,
                "correction_task_id": segment.correction_task_id,
                "correction_status": segment.correction_status,
                "correction_failure_code": segment.correction_failure_code,
                "revision": segment.text_revision,
                "text_revision": segment.text_revision,
                "timeline_revision": segment.timeline_revision,
                "text_state": VoiceLiveRunService._text_state(segment),
                "started_at_ms": segment.started_at_ms,
                "ended_at_ms": segment.ended_at_ms,
                "duration_ms": segment.duration_ms,
                "overlap_milliseconds": segment.overlap_milliseconds,
                "attempt_count": segment.attempt_count,
                "failure_code": segment.failure_code,
                "correction_started_at": segment.correction_started_at,
                "correction_completed_at": segment.correction_completed_at,
                "text": (result_by_sequence.get(segment.sequence) or {}).get("text"),
                "provider": (result_by_sequence.get(segment.sequence) or {}).get("provider"),
                "model": (result_by_sequence.get(segment.sequence) or {}).get("model"),
            }
            for segment in segments
        ]
        present = {segment.sequence for segment in segments}
        items.extend(
            {
                "id": None,
                "sequence": sequence,
                "status": "gap",
                "task_id": None,
                "result_ref": None,
                "provisional_result_ref": None,
                "correction_task_id": None,
                "correction_status": "not_requested",
                "correction_failure_code": None,
                "revision": 0,
                "text_revision": 0,
                "timeline_revision": gap_timeline_revision,
                "text_state": "none",
                "started_at_ms": None,
                "ended_at_ms": None,
                "duration_ms": None,
                "overlap_milliseconds": None,
                "attempt_count": 0,
                "failure_code": "segment_missing",
                "correction_started_at": None,
                "correction_completed_at": None,
                "text": None,
                "provider": None,
                "model": None,
            }
            for sequence in gaps
            if sequence not in present
        )
        return sorted(items, key=lambda item: int(item["sequence"]))

    @staticmethod
    def _text_state(segment: VoiceLiveRunSegmentDB) -> str:
        if not segment.result_ref or segment.text_revision <= 0:
            return "none"
        if segment.text_revision == 1:
            return "provisional"
        if segment.correction_status == "completed":
            return "final"
        return "final_uncorrected"

    @staticmethod
    def _gap_sequences(
        run: VoiceLiveRunDB,
        segments: tuple[VoiceLiveRunSegmentDB, ...],
    ) -> list[int]:
        by_sequence = {segment.sequence: segment for segment in segments}
        highest_expected = max(
            int(run.expected_last_sequence if run.expected_last_sequence is not None else -1),
            int(run.last_local_sequence if run.last_local_sequence is not None else -1),
            max(by_sequence, default=-1),
        )
        missing = {
            sequence
            for sequence in range(highest_expected + 1)
            if sequence not in by_sequence or by_sequence[sequence].status == "failed"
        }
        completed = {sequence for sequence, segment in by_sequence.items() if segment.status == "completed"}
        missing.update(int(item) for item in (run.reported_gap_sequences or []) if int(item) not in completed)
        return sorted(item for item in missing if 0 <= item <= highest_expected)

    @staticmethod
    def _acknowledged_through(segments: tuple[VoiceLiveRunSegmentDB, ...]) -> int:
        completed = {segment.sequence for segment in segments if segment.status == "completed"}
        sequence = 0
        while sequence in completed:
            sequence += 1
        return sequence - 1

    @staticmethod
    def _compose_transcript(
        values: list[tuple[VoiceLiveRunSegmentDB, str]],
    ) -> str:
        composed: list[str] = []
        previous_segment: VoiceLiveRunSegmentDB | None = None
        for segment, text in values:
            words = text.split()
            if not words:
                previous_segment = segment
                continue
            overlap = 0
            if composed and previous_segment is not None and segment.started_at_ms < previous_segment.ended_at_ms:
                maximum = min(32, len(composed), len(words))
                for width in range(maximum, 0, -1):
                    left = [VoiceLiveRunService._normalized_word(item) for item in composed[-width:]]
                    right = [VoiceLiveRunService._normalized_word(item) for item in words[:width]]
                    if left == right and any(left):
                        overlap = width
                        break
            composed.extend(words[overlap:])
            previous_segment = segment
        return " ".join(composed).strip()

    def _get_or_create_final_artifact(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        *,
        segments: tuple[VoiceLiveRunSegmentDB, ...],
        gaps: list[int],
    ) -> dict[str, Any]:
        manifest = [
            {
                "sequence": segment.sequence,
                "status": segment.status,
                "result_ref": segment.result_ref,
                "started_at_ms": segment.started_at_ms,
                "ended_at_ms": segment.ended_at_ms,
            }
            for segment in segments
        ]
        manifest_digest = hashlib.sha256(
            json.dumps(
                {
                    "segments": manifest,
                    "gaps": gaps,
                    "expected_last_sequence": run.expected_last_sequence,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request_ref = f"voice-live-run-final:{run.id}:{manifest_digest}"
        existing = self._artifacts.find_live_envelope(
            principal,
            request_ref=request_ref,
            profile_id=run.profile_id,
        )
        if existing is not None:
            return existing
        result = {
            "schema_version": "ananta.voice-live-run-result.v1",
            "provider": "voice-live-run",
            "model": "rolling-segments",
            # Full text stays split across encrypted segment artifacts. This
            # bounded manifest cannot trip the 2 MiB result limit or duplicate
            # an eight-hour transcript into one monolithic artifact.
            "text": "",
            "transcript_included": False,
            "transcript_source": "segment_result_refs",
            "segment_count": len(segments),
            "completed_segment_count": sum(segment.status == "completed" for segment in segments),
            "segments": manifest,
            "gaps": gaps,
            "candidates": [],
        }
        try:
            return self._artifacts.create(
                principal,
                request_hash=request_ref,
                result=result,
                profile_id=run.profile_id,
            )
        except IntegrityError:
            recovered = self._artifacts.find_live_envelope(
                principal,
                request_ref=request_ref,
                profile_id=run.profile_id,
            )
            if recovered is None:
                raise
            return recovered

    @staticmethod
    def _public_run(run: VoiceLiveRunDB) -> dict[str, Any]:
        now = time.time()
        return {
            "id": run.id,
            "status": run.status,
            "source": run.source,
            "profile_id": run.profile_id,
            "configuration_session_id": run.configuration_session_id,
            "language": run.language,
            "parent_task_id": run.parent_task_id,
            "segment_duration_seconds": run.segment_duration_seconds,
            "max_duration_seconds": run.max_duration_seconds,
            "overlap_milliseconds": run.overlap_milliseconds,
            "last_local_sequence": run.last_local_sequence,
            "expected_last_sequence": run.expected_last_sequence,
            "last_heartbeat_at": run.last_heartbeat_at,
            "heartbeat_stale": run.status == "active" and now - run.last_heartbeat_at > 30,
            "capture_deadline_at": run.capture_deadline_at,
            "expires_at": run.expires_at,
            "final_result_ref": run.final_result_ref,
            "stop_reason": run.stop_reason,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "stopped_at": run.stopped_at,
            "version": run.version,
            "timeline_revision": run.timeline_revision,
        }

    @staticmethod
    def _maximum_sequence(run: VoiceLiveRunDB) -> int:
        advance_ms = run.segment_duration_seconds * 1000 - run.overlap_milliseconds
        return max(0, math.ceil(run.max_duration_seconds * 1000 / advance_ms) - 1)

    @staticmethod
    def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise VoiceLiveRunError(
                f"voice_live_run.invalid_{field}",
                f"{field} must be an integer",
                422,
            ) from exc
        if normalized < minimum or normalized > maximum:
            raise VoiceLiveRunError(
                f"voice_live_run.invalid_{field}",
                f"{field} must be between {minimum} and {maximum}",
                422,
            )
        return normalized

    @staticmethod
    def _assert_create_replay_matches(run: VoiceLiveRunDB, **expected: Any) -> None:
        if any(getattr(run, key) != value for key, value in expected.items()):
            raise VoiceLiveRunError(
                "voice_live_run.idempotency_conflict",
                "Idempotency-Key was already used with a different live-run configuration",
                409,
            )

    @staticmethod
    def _normalized_word(value: str) -> str:
        return _WORD_NORMALIZER.sub("", value.casefold())

    @staticmethod
    def _not_found() -> None:
        raise VoiceLiveRunError(
            "voice_live_run.not_found",
            "voice live run not found",
            404,
        )

    @staticmethod
    def _conflict(message: str) -> VoiceLiveRunError:
        return VoiceLiveRunError(
            "voice_live_run.segment_conflict",
            message,
            409,
        )


voice_live_run_service = VoiceLiveRunService()


def get_voice_live_run_service() -> VoiceLiveRunService:
    return voice_live_run_service
