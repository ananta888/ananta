"""Fenced finalization of a live run into its bounded result manifest artifact."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.repositories.voice_live_runs import (
    VoiceLiveRunRepository,
    VoiceLiveRunRepositoryConflict,
    VoiceLiveRunRepositoryInProgress,
)
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_contracts import VoiceLiveRunError, segment_conflict
from agent.services.voice_live_run_fences import VoiceLiveRunFences
from agent.services.voice_live_run_preview_service import VoiceLiveRunPreviewService
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort
from agent.services.voice_live_run_timeline import gap_sequences
from agent.services.voice_result_artifact_service import VoiceResultArtifactService


class VoiceLiveRunFinalizer:
    """Claim finalization, write the manifest artifact and complete the parent task.

    The caller owns the per-run process lock and the snapshot projection; this
    collaborator owns the claim/complete/abort sequence and its compensation.
    """

    def __init__(
        self,
        *,
        repository: VoiceLiveRunRepository,
        artifacts: VoiceResultArtifactService,
        tasks: VoiceLiveRunTaskPort,
        previews: VoiceLiveRunPreviewService,
        fences: VoiceLiveRunFences,
        now: Callable[[], float],
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._tasks = tasks
        self._previews = previews
        self._fences = fences
        self._now = now

    def finalize(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        *,
        expected_last_sequence: int | None,
        reason: str,
    ) -> None:
        self._previews.cleanup_run(principal, run.id)
        try:
            run, replayed = self._repository.begin_finalize(
                principal,
                run.id,
                expected_last_sequence=expected_last_sequence,
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
            raise segment_conflict(str(exc)) from exc
        if replayed:
            if run.final_result_ref:
                self._tasks.complete_parent(run, result_ref=run.final_result_ref)
            return
        finalization_version = run.version
        artifact: dict[str, Any] | None = None
        try:
            self._fences.assert_finalization_allowed(
                principal,
                run,
                expected_version=finalization_version,
            )
            segments = self._repository.list_segments(principal, run.id)
            self._cancel_lease_expired_tasks(segments)
            gaps = gap_sequences(run, segments)
            artifact = self.get_or_create_final_artifact(
                principal,
                run,
                segments=segments,
                gaps=gaps,
            )
            self._fences.assert_finalization_allowed(
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

    def _cancel_lease_expired_tasks(self, segments: tuple[VoiceLiveRunSegmentDB, ...]) -> None:
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

    def get_or_create_final_artifact(
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


__all__ = ["VoiceLiveRunFinalizer"]
