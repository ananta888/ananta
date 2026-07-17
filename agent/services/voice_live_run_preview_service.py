from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.repositories.voice_live_runs import VoiceLiveRunRepository
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTaskService,
    get_voice_delegation_task_service,
)
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    validate_identifier,
)
from agent.services.voice_runtime_cleanup_service import (
    VoiceRuntimeCleanupService,
    VoiceRuntimeCleanupTarget,
    get_voice_runtime_cleanup_service,
)
from agent.services.voice_stream_session_service import (
    HubVoiceStreamSession,
    VoiceStreamSessionService,
    get_voice_stream_session_service,
)


@dataclass(frozen=True)
class VoiceLiveRunPreviewBinding:
    """Validated, ephemeral binding between one live run segment and a stream."""

    live_run_id: str
    live_run_segment_sequence: int
    profile_id: str
    configuration_session_id: str | None
    language: str | None
    parent_task_id: str
    segment_duration_seconds: int
    run_created_at: float


class VoiceLiveRunPreviewService:
    """Validate and clean up Hub-owned, non-authoritative live previews."""

    def __init__(
        self,
        *,
        repository: VoiceLiveRunRepository | None = None,
        sessions: VoiceStreamSessionService | None = None,
        runtime_cleanup: VoiceRuntimeCleanupService | None = None,
        delegations: VoiceDelegationTaskService | None = None,
        clock: Callable[[], float] = time.time,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._repository = repository or VoiceLiveRunRepository()
        self._sessions = sessions or get_voice_stream_session_service()
        self._runtime_cleanup = runtime_cleanup or get_voice_runtime_cleanup_service()
        self._delegations = delegations or get_voice_delegation_task_service()
        self._clock = clock
        self._audit = audit_sink

    def resolve_optional(
        self,
        principal: VoicePrincipal,
        *,
        live_run_id: Any,
        live_run_segment_sequence: Any,
    ) -> VoiceLiveRunPreviewBinding | None:
        run_missing = live_run_id is None
        sequence_missing = live_run_segment_sequence is None
        if run_missing and sequence_missing:
            return None
        if run_missing != sequence_missing:
            raise VoiceGovernanceError(
                code="voice_stream.live_preview_binding_incomplete",
                message=(
                    "live_run_id and live_run_segment_sequence must be provided together"
                ),
                status_code=422,
            )

        normalized_run_id = validate_identifier(
            live_run_id,
            field="live_run_id",
            max_length=200,
        )
        sequence = self._segment_sequence(live_run_segment_sequence)
        run = self._repository.get(principal, normalized_run_id)
        if run is None:
            raise VoiceGovernanceError(
                code="voice_stream.live_preview_run_not_found",
                message="voice live run not found",
                status_code=404,
            )
        if run.status != "active" or run.capture_deadline_at <= self._clock():
            raise VoiceGovernanceError(
                code="voice_stream.live_preview_run_inactive",
                message="voice live run is not active for preview capture",
                status_code=409,
            )
        if sequence > self._maximum_sequence(
            max_duration_seconds=run.max_duration_seconds,
            segment_duration_seconds=run.segment_duration_seconds,
            overlap_milliseconds=run.overlap_milliseconds,
        ):
            raise VoiceGovernanceError(
                code="voice_stream.invalid_live_run_segment_sequence",
                message="live run segment sequence exceeds the configured run duration",
                status_code=422,
            )
        return VoiceLiveRunPreviewBinding(
            live_run_id=run.id,
            live_run_segment_sequence=sequence,
            profile_id=run.profile_id,
            configuration_session_id=run.configuration_session_id,
            language=run.language,
            parent_task_id=run.parent_task_id,
            segment_duration_seconds=run.segment_duration_seconds,
            run_created_at=run.created_at,
        )

    def assert_context(
        self,
        binding: VoiceLiveRunPreviewBinding,
        *,
        profile_id: Any,
        configuration_session_id: Any,
        language: Any,
    ) -> None:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        normalized_session_id = (
            validate_identifier(
                configuration_session_id,
                field="configuration_session_id",
                max_length=200,
            )
            if configuration_session_id
            else None
        )
        normalized_language = str(language or "").strip() or None
        if (
            normalized_profile_id != binding.profile_id
            or normalized_session_id != binding.configuration_session_id
            or normalized_language != binding.language
        ):
            raise VoiceGovernanceError(
                code="voice_stream.live_preview_context_mismatch",
                message="voice stream profile/session/language context does not match the live run",
                status_code=409,
            )

    def assert_current(
        self,
        principal: VoicePrincipal,
        binding: VoiceLiveRunPreviewBinding,
    ) -> None:
        current = self.resolve_optional(
            principal,
            live_run_id=binding.live_run_id,
            live_run_segment_sequence=binding.live_run_segment_sequence,
        )
        if current != binding:
            raise VoiceGovernanceError(
                code="voice_stream.live_preview_binding_changed",
                message="voice live run preview binding changed during stream creation",
                status_code=409,
            )

    def assert_available(
        self,
        principal: VoicePrincipal,
        binding: VoiceLiveRunPreviewBinding,
    ) -> None:
        self._sessions.assert_live_run_preview_available(
            principal,
            binding.live_run_id,
            binding.live_run_segment_sequence,
        )

    def cleanup_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
    ) -> int:
        return self._cleanup(
            principal,
            run_id,
            sequence=sequence,
            reason_code="voice_live_preview_segment_closed",
        )

    def cleanup_run(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        reason_code: str = "voice_live_preview_run_stopped",
    ) -> int:
        return self._cleanup(
            principal,
            run_id,
            sequence=None,
            reason_code=reason_code,
        )

    def _cleanup(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int | None,
        reason_code: str,
    ) -> int:
        try:
            removed = self._sessions.remove_live_run_previews(
                principal,
                run_id,
                segment_sequence=sequence,
                before_remove=lambda sessions: self._stage_runtime_cleanup(
                    principal,
                    sessions,
                ),
            )
        except Exception as exc:
            # Fail closed: if durable cleanup staging fails, keep the ephemeral
            # capability for retry but fence it against additional audio.
            try:
                retained = self._sessions.fail_live_run_previews(
                    principal,
                    run_id,
                    segment_sequence=sequence,
                )
            except Exception:
                retained = ()
            for session in retained:
                self._cancel_delegation(session, reason_code=reason_code)
            self._audit(
                "voice_live_run_preview_cleanup",
                {
                    "status": "failed",
                    "reason_code": reason_code,
                    "error_type": type(exc).__name__,
                    "removed_count": 0,
                    "retained_count": len(retained),
                },
            )
            return 0

        for session in removed:
            self._cancel_delegation(session, reason_code=reason_code)
            try:
                self._runtime_cleanup.retry_target(
                    principal,
                    session.profile_id,
                    session.session_id,
                )
            except Exception:
                # The target is already durable and will be retried by normal
                # runtime-cleanup maintenance.
                pass

        if removed:
            self._audit(
                "voice_live_run_preview_cleanup",
                {
                    "status": "completed",
                    "reason_code": reason_code,
                    "removed_count": len(removed),
                },
            )
        return len(removed)

    def _cancel_delegation(
        self,
        session: HubVoiceStreamSession,
        *,
        reason_code: str,
    ) -> None:
        if not session.task_id:
            return
        try:
            self._delegations.cancel(
                session.task_id,
                reason_code=reason_code,
            )
        except Exception as exc:
            self._audit(
                "voice_live_run_preview_task_cleanup",
                {
                    "status": "failed",
                    "reason_code": reason_code,
                    "error_type": type(exc).__name__,
                },
            )

    def _stage_runtime_cleanup(
        self,
        principal: VoicePrincipal,
        sessions: tuple[HubVoiceStreamSession, ...],
    ) -> None:
        by_profile: dict[str, list[VoiceRuntimeCleanupTarget]] = {}
        for session in sessions:
            by_profile.setdefault(session.profile_id, []).append(
                VoiceRuntimeCleanupTarget(
                    source_session_id=session.session_id,
                    runtime_session_id=session.runtime_session_id,
                )
            )
        for profile_id, targets in by_profile.items():
            self._runtime_cleanup.stage(
                principal,
                profile_id=profile_id,
                operation="stream_orphan",
                targets=tuple(targets),
            )

    @staticmethod
    def _segment_sequence(value: Any) -> int:
        if isinstance(value, bool):
            raise VoiceGovernanceError(
                code="voice_stream.invalid_live_run_segment_sequence",
                message="live_run_segment_sequence must be a non-negative integer",
                status_code=422,
            )
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise VoiceGovernanceError(
                code="voice_stream.invalid_live_run_segment_sequence",
                message="live_run_segment_sequence must be a non-negative integer",
                status_code=422,
            ) from exc
        if normalized < 0 or (isinstance(value, float) and not value.is_integer()):
            raise VoiceGovernanceError(
                code="voice_stream.invalid_live_run_segment_sequence",
                message="live_run_segment_sequence must be a non-negative integer",
                status_code=422,
            )
        return normalized

    @staticmethod
    def _maximum_sequence(
        *,
        max_duration_seconds: int,
        segment_duration_seconds: int,
        overlap_milliseconds: int,
    ) -> int:
        advance_ms = segment_duration_seconds * 1_000 - overlap_milliseconds
        return max(0, math.ceil(max_duration_seconds * 1_000 / advance_ms) - 1)


voice_live_run_preview_service = VoiceLiveRunPreviewService()


def get_voice_live_run_preview_service() -> VoiceLiveRunPreviewService:
    return voice_live_run_preview_service
