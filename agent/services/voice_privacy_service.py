from __future__ import annotations

from typing import Callable

from agent.common.audit import log_audit
from agent.repositories.voice_deletion_reconciliation import VoiceDeletionReconciliationRepository
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.repositories.voice_privacy import VoicePrivacyRepository
from agent.services.voice_governance_domain import VoicePrincipal, validate_identifier, voice_scope_digest
from agent.services.voice_runtime_cleanup_service import (
    RuntimeStreamDelete,
    VoiceRuntimeCleanupService,
    VoiceRuntimeCleanupTarget,
    get_voice_runtime_cleanup_service,
)
from agent.services.voice_stream_session_service import (
    HubVoiceStreamSession,
    VoiceStreamSessionService,
    get_voice_stream_session_service,
)


class VoicePrivacyService:
    def __init__(
        self,
        repository: VoicePrivacyRepository | None = None,
        reconciliation: VoiceDeletionReconciliationRepository | None = None,
        stream_sessions: VoiceStreamSessionService | None = None,
        tombstones: VoiceDeletionTombstoneRepository | None = None,
        runtime_cleanup: VoiceRuntimeCleanupService | None = None,
        runtime_stream_delete: RuntimeStreamDelete | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        if runtime_cleanup is not None and runtime_stream_delete is not None:
            raise ValueError("runtime_cleanup and runtime_stream_delete are mutually exclusive")
        self._repository = repository or VoicePrivacyRepository()
        self._reconciliation = reconciliation or VoiceDeletionReconciliationRepository()
        self._stream_sessions = stream_sessions or get_voice_stream_session_service()
        self._tombstones = tombstones or VoiceDeletionTombstoneRepository()
        self._runtime_cleanup = (
            runtime_cleanup
            or (
                VoiceRuntimeCleanupService(runtime_stream_delete=runtime_stream_delete)
                if runtime_stream_delete is not None
                else get_voice_runtime_cleanup_service()
            )
        )
        self._audit = audit_sink

    def delete_profile(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        idempotency_key: str,
    ) -> dict:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        claim = self._tombstones.claim(
            principal,
            normalized_profile_id,
            idempotency_key=idempotency_key,
        )
        if claim.replayed:
            replay_cleanup = self._cleanup_profile(
                principal,
                normalized_profile_id,
                deleted_at=claim.deleted_at,
                restored_only=True,
            )
            result = {
                **replay_cleanup,
                "replay_cleanup_deleted_count": replay_cleanup["deleted_count"],
                "replay_cleanup_stream_count": replay_cleanup["revoked_stream_count"],
                "idempotent_replay": True,
            }
            self._audit_deletion(principal, normalized_profile_id, result, status="idempotent_replay")
            return result
        result = self._cleanup_profile(
            principal,
            normalized_profile_id,
            deleted_at=claim.deleted_at,
            restored_only=False,
        )
        self._audit_deletion(principal, normalized_profile_id, result, status="deleted")
        return {**result, "idempotent_replay": False}

    def _cleanup_profile(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        deleted_at: float,
        restored_only: bool,
    ) -> dict:
        recovered_cleanup = self._runtime_cleanup.retry_pseudonymous_profile(
            principal,
            profile_id,
        )
        self._runtime_cleanup.stage_cache_gc(
            principal,
            profile_id=profile_id,
            operation="profile_delete",
        )
        def remove_hook(removed: HubVoiceStreamSession) -> None:
            self._stage_runtime_cleanup(
                principal,
                profile_id,
                removed,
                operation="profile_delete",
            )
        streams = (
            self._stream_sessions.delete_profile_before(
                principal,
                profile_id,
                deleted_at=deleted_at,
                before_remove=remove_hook,
            )
            if restored_only
            else self._stream_sessions.delete_profile(
                principal,
                profile_id,
                before_remove=remove_hook,
            )
        )
        session_ids = {
            identifier
            for session in streams
            for identifier in (session.session_id, session.configuration_session_id)
            if identifier
        }
        task_ids = {session.task_id for session in streams if session.task_id}
        runtime_cleanup = self._runtime_cleanup.retry_profile(principal, profile_id)
        self._runtime_cleanup.pseudonymize_profile_scope(principal, profile_id)
        for session in streams:
            session.runtime_session_id = ""
            session.configuration_session_id = None
            session.task_id = None
            session.result_ref = None
        counts = (
            self._reconciliation.delete_before(
                principal,
                profile_id,
                deleted_at=deleted_at,
                session_ids=session_ids,
            )
            if restored_only
            else self._repository.delete_profile(
                principal,
                profile_id,
                session_ids=session_ids,
                task_ids=task_ids,
            )
        )
        counts["hub_voice_stream_sessions"] = len(streams)
        return {
            "profile_id": profile_id,
            "deleted_count": sum(counts.values()),
            "deleted_by_store": counts,
            "snapshots_revoked": True,
            "revoked_stream_count": len(streams),
            "runtime_cleanup_pending": (
                runtime_cleanup.status.pending_count > 0
                or recovered_cleanup.status.pending_count > 0
            ),
            "runtime_cleanup_failed_count": (
                runtime_cleanup.status.failed_count
                + recovered_cleanup.status.failed_count
            ),
        }

    def _stage_runtime_cleanup(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        sessions: tuple[HubVoiceStreamSession, ...],
        *,
        operation: str,
    ) -> None:
        self._runtime_cleanup.stage(
            principal,
            profile_id=profile_id,
            operation=operation,
            targets=tuple(
                VoiceRuntimeCleanupTarget(
                    source_session_id=session.session_id,
                    runtime_session_id=session.runtime_session_id,
                )
                for session in sessions
                if session.runtime_session_id
            ),
        )

    def _audit_deletion(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        result: dict,
        *,
        status: str,
    ) -> None:
        self._audit(
            "voice_profile_deleted",
            {
                "scope_digest": voice_scope_digest(principal, profile_id),
                "deleted_count": int(result.get("deleted_count") or 0),
                "revoked_stream_count": int(result.get("revoked_stream_count") or 0),
                "runtime_cleanup_pending": bool(result.get("runtime_cleanup_pending")),
                "runtime_cleanup_failed_count": int(result.get("runtime_cleanup_failed_count") or 0),
                "status": status,
            },
        )


voice_privacy_service = VoicePrivacyService()


def get_voice_privacy_service() -> VoicePrivacyService:
    return voice_privacy_service
