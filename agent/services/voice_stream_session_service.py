from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal, validate_identifier


@dataclass
class HubVoiceStreamSession:
    session_id: str
    runtime_session_id: str
    tenant_id: str
    owner_subject: str
    profile_id: str
    configuration_session_id: str | None
    language: str | None
    effective_configuration_json: str
    task_id: str | None
    request_id: str
    admission_lease_id: str | None
    state: str
    next_chunk_sequence: int
    max_audio_seconds: float
    max_audio_bytes: int
    accepted_audio_bytes: int
    created_at: float
    deadline_at: float
    result_ref: str | None = None
    accepted_chunk_digests: dict[int, str] = field(default_factory=dict, repr=False)
    inflight_chunk_sequence: int | None = field(default=None, repr=False)
    inflight_chunk_digest: str | None = field(default=None, repr=False)
    inflight_chunk_bytes: int | None = field(default=None, repr=False)
    finalize_token: str | None = field(default=None, repr=False)

    def public(self) -> dict:
        payload = asdict(self)
        payload.pop("runtime_session_id", None)
        payload.pop("tenant_id", None)
        payload.pop("owner_subject", None)
        payload.pop("effective_configuration_json", None)
        payload.pop("accepted_chunk_digests", None)
        payload.pop("inflight_chunk_sequence", None)
        payload.pop("inflight_chunk_digest", None)
        payload.pop("inflight_chunk_bytes", None)
        payload.pop("finalize_token", None)
        payload.pop("admission_lease_id", None)
        return payload


@dataclass(frozen=True)
class VoiceChunkReservation:
    session: HubVoiceStreamSession
    replayed: bool


@dataclass(frozen=True)
class VoiceFinalizeReservation:
    session: HubVoiceStreamSession
    token: str


ProfileRemovalHook = Callable[[tuple[HubVoiceStreamSession, ...]], None]
ExpirationHook = Callable[[tuple[HubVoiceStreamSession, ...]], None]


class VoiceStreamSessionService:
    """Hub-owned, ephemeral mapping from principals to opaque runtime sessions."""

    def __init__(
        self,
        *,
        max_sessions: int = 64,
        max_chunks_per_session: int = 65_536,
        replay_window_chunks: int = 256,
        admission_release: Callable[[str | None], None] | None = None,
        expiration_hook: ExpirationHook | None = None,
    ) -> None:
        self._max_sessions = max(1, max_sessions)
        self._max_chunks_per_session = max(1, max_chunks_per_session)
        self._replay_window_chunks = max(1, min(replay_window_chunks, self._max_chunks_per_session))
        self._admission_release = admission_release or (lambda _lease_id: None)
        self._expiration_hook = expiration_hook or (lambda _sessions: None)
        self._sessions: dict[str, HubVoiceStreamSession] = {}
        self._lock = threading.RLock()

    def create(
        self,
        principal: VoicePrincipal,
        *,
        runtime_session_id: str,
        session_id: str | None = None,
        deadline_seconds: float,
        profile_id: str = "default",
        configuration_session_id: str | None = None,
        language: str | None = None,
        effective_configuration: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        request_id: str = "hub-stream-legacy",
        admission_lease_id: str | None = None,
        max_audio_seconds: float = 819.2,
        max_audio_bytes: int = 25 * 1024 * 1024,
    ) -> HubVoiceStreamSession:
        validate_identifier(runtime_session_id, field="runtime_session_id", max_length=200)
        if float(max_audio_seconds) <= 0 or isinstance(max_audio_bytes, bool) or int(max_audio_bytes) <= 0:
            raise VoiceGovernanceError(
                code="voice_stream.invalid_audio_budget",
                message="voice stream audio budget must be positive",
                status_code=422,
            )
        try:
            configuration_snapshot = json.dumps(
                dict(effective_configuration or {}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            raise VoiceGovernanceError(
                code="voice_stream.invalid_configuration",
                message="voice stream configuration snapshot is invalid",
                status_code=422,
            ) from exc
        if len(configuration_snapshot.encode("utf-8")) > 64 * 1024:
            raise VoiceGovernanceError(
                code="voice_stream.invalid_configuration",
                message="voice stream configuration snapshot exceeds its bound",
                status_code=422,
            )
        with self._lock:
            self._purge_locked()
            active = sum(item.state not in {"final", "failed", "closed"} for item in self._sessions.values())
            if active >= self._max_sessions:
                raise VoiceGovernanceError(
                    code="voice_stream.capacity_exhausted",
                    message="Hub voice stream capacity exhausted",
                    status_code=429,
                )
            requested_session_id = (
                validate_identifier(session_id, field="stream_session_id", max_length=200)
                if session_id
                else self._new_session_id_locked()
            )
            if requested_session_id in self._sessions:
                raise VoiceGovernanceError(
                    code="voice_stream.session_id_conflict",
                    message="Hub voice stream session identifier is already active",
                    status_code=409,
                )
            session = HubVoiceStreamSession(
                session_id=requested_session_id,
                runtime_session_id=runtime_session_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                profile_id=validate_identifier(profile_id, field="profile_id"),
                configuration_session_id=(
                    validate_identifier(
                        configuration_session_id,
                        field="configuration_session_id",
                        max_length=200,
                    )
                    if configuration_session_id
                    else None
                ),
                language=(
                    validate_identifier(language, field="language", max_length=32)
                    if language
                    else None
                ),
                effective_configuration_json=configuration_snapshot,
                task_id=validate_identifier(task_id, field="task_id", max_length=200) if task_id else None,
                request_id=validate_identifier(request_id, field="request_id", max_length=200),
                admission_lease_id=(
                    validate_identifier(admission_lease_id, field="admission_lease_id", max_length=200)
                    if admission_lease_id
                    else None
                ),
                state="created",
                next_chunk_sequence=0,
                max_audio_seconds=float(max_audio_seconds),
                max_audio_bytes=int(max_audio_bytes),
                accepted_audio_bytes=0,
                created_at=time.time(),
                deadline_at=time.time() + max(1.0, min(float(deadline_seconds), 300.0)),
            )
            self._sessions[session.session_id] = session
            return session

    def _new_session_id_locked(self) -> str:
        for _attempt in range(8):
            candidate = f"voice-stream-{secrets.token_urlsafe(18)}"
            if candidate not in self._sessions:
                return candidate
        raise VoiceGovernanceError(
            code="voice_stream.session_id_exhausted",
            message="Hub could not allocate a unique voice stream capability",
            status_code=503,
        )

    def require(self, principal: VoicePrincipal, session_id: str) -> HubVoiceStreamSession:
        validate_identifier(session_id, field="stream_session_id", max_length=200)
        with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.tenant_id != principal.tenant_id
                or session.owner_subject != principal.subject
            ):
                raise VoiceGovernanceError(
                    code="voice_stream.not_found",
                    message="voice stream session not found",
                    status_code=404,
                )
            if session.deadline_at <= time.time():
                self._expire_locked((session,))
                raise VoiceGovernanceError(
                    code="voice_stream.deadline_exceeded",
                    message="voice stream deadline exceeded",
                    status_code=504,
                )
            return session

    def begin_chunk(
        self,
        principal: VoicePrincipal,
        session_id: str,
        *,
        chunk_sequence: int,
        chunk_digest: str,
        chunk_size: int = 0,
    ) -> VoiceChunkReservation:
        """Validate and reserve a chunk before crossing the Runtime boundary."""

        if isinstance(chunk_sequence, bool) or chunk_sequence < 0:
            raise VoiceGovernanceError(
                code="voice_stream.invalid_chunk_sequence",
                message="chunk sequence must be a non-negative integer",
                status_code=422,
            )
        if len(chunk_digest) != 64 or any(character not in "0123456789abcdef" for character in chunk_digest):
            raise VoiceGovernanceError(
                code="voice_stream.invalid_chunk_digest",
                message="chunk digest must be a lowercase SHA-256",
                status_code=422,
            )
        if isinstance(chunk_size, bool) or chunk_size < 0:
            raise VoiceGovernanceError(
                code="voice_stream.invalid_chunk_size",
                message="chunk size must be a non-negative integer",
                status_code=422,
            )
        with self._lock:
            session = self.require(principal, session_id)
            if session.state not in {"created", "active"}:
                raise VoiceGovernanceError(
                    code="voice_stream.invalid_state",
                    message=f"cannot add chunks in state {session.state}",
                    status_code=409,
                )
            if chunk_sequence < session.next_chunk_sequence:
                accepted_digest = session.accepted_chunk_digests.get(chunk_sequence)
                if accepted_digest == chunk_digest:
                    return VoiceChunkReservation(session=session, replayed=True)
                if accepted_digest is None:
                    raise VoiceGovernanceError(
                        code="voice_stream.replay_window_expired",
                        message="replayed chunk is outside the bounded replay window",
                        status_code=409,
                    )
                raise VoiceGovernanceError(
                    code="voice_stream.chunk_conflict",
                    message="replayed chunk content differs",
                    status_code=409,
                )
            if session.inflight_chunk_sequence is not None:
                raise VoiceGovernanceError(
                    code="voice_stream.backpressure",
                    message="another chunk is still being processed",
                    status_code=429,
                )
            if chunk_sequence != session.next_chunk_sequence:
                raise VoiceGovernanceError(
                    code="voice_stream.sequence_conflict",
                    message=f"expected chunk {session.next_chunk_sequence}",
                    status_code=409,
                )
            if session.next_chunk_sequence >= self._max_chunks_per_session:
                raise VoiceGovernanceError(
                    code="voice_stream.chunk_limit_exceeded",
                    message="stream exceeds its chunk-count budget",
                    status_code=413,
                )
            if session.accepted_audio_bytes + chunk_size > session.max_audio_bytes:
                raise VoiceGovernanceError(
                    code="voice_stream.audio_budget_exceeded",
                    message="stream exceeds its audio byte budget",
                    status_code=413,
                )
            session.inflight_chunk_sequence = chunk_sequence
            session.inflight_chunk_digest = chunk_digest
            session.inflight_chunk_bytes = chunk_size
            return VoiceChunkReservation(session=session, replayed=False)

    def complete_chunk(
        self,
        principal: VoicePrincipal,
        session_id: str,
        *,
        chunk_sequence: int,
        chunk_digest: str,
    ) -> HubVoiceStreamSession:
        with self._lock:
            session = self.require(principal, session_id)
            if session.state not in {"created", "active"}:
                raise VoiceGovernanceError(
                    code="voice_stream.chunk_reservation_lost",
                    message="stream state changed before chunk completion",
                    status_code=409,
                )
            if (
                session.inflight_chunk_sequence != chunk_sequence
                or session.inflight_chunk_digest != chunk_digest
                or session.inflight_chunk_bytes is None
                or session.next_chunk_sequence != chunk_sequence
            ):
                raise VoiceGovernanceError(
                    code="voice_stream.chunk_reservation_lost",
                    message="chunk reservation is no longer valid",
                    status_code=409,
                )
            session.accepted_chunk_digests[chunk_sequence] = chunk_digest
            session.next_chunk_sequence += 1
            oldest_retained = max(0, session.next_chunk_sequence - self._replay_window_chunks)
            for accepted_sequence in tuple(session.accepted_chunk_digests):
                if accepted_sequence < oldest_retained:
                    session.accepted_chunk_digests.pop(accepted_sequence, None)
            accepted_bytes = session.inflight_chunk_bytes
            session.inflight_chunk_sequence = None
            session.inflight_chunk_digest = None
            session.inflight_chunk_bytes = None
            session.accepted_audio_bytes += accepted_bytes
            session.state = "active"
            return session

    def begin_finalize(
        self,
        principal: VoicePrincipal,
        session_id: str,
    ) -> VoiceFinalizeReservation:
        """Fence finalization against chunk admission and concurrent terminals."""

        with self._lock:
            session = self.require(principal, session_id)
            if session.state not in {"created", "active"}:
                raise VoiceGovernanceError(
                    code="voice_stream.invalid_state",
                    message=f"cannot finalize stream in state {session.state}",
                    status_code=409,
                )
            if session.inflight_chunk_sequence is not None:
                raise VoiceGovernanceError(
                    code="voice_stream.backpressure",
                    message="cannot finalize while a chunk is in flight",
                    status_code=409,
                )
            token = secrets.token_urlsafe(24)
            session.state = "finalizing"
            session.finalize_token = token
            return VoiceFinalizeReservation(session=session, token=token)

    def complete_finalize(
        self,
        principal: VoicePrincipal,
        session_id: str,
        *,
        token: str,
        result_ref: str,
    ) -> HubVoiceStreamSession:
        with self._lock:
            session = self.require(principal, session_id)
            if session.state != "finalizing" or not secrets.compare_digest(
                str(session.finalize_token or ""),
                str(token),
            ):
                raise VoiceGovernanceError(
                    code="voice_stream.finalize_reservation_lost",
                    message="stream finalization reservation is no longer valid",
                    status_code=409,
                )
            session.state = "final"
            session.finalize_token = None
            session.result_ref = result_ref
            self._release_admission_locked(session)
            return session

    def fail_finalize(
        self,
        principal: VoicePrincipal,
        session_id: str,
        *,
        token: str,
    ) -> HubVoiceStreamSession | None:
        """Terminalize an uncertain Runtime finalize instead of reopening it."""

        with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.tenant_id != principal.tenant_id
                or session.owner_subject != principal.subject
                or session.state != "finalizing"
                or not secrets.compare_digest(str(session.finalize_token or ""), str(token))
            ):
                return None
            session.state = "failed"
            session.finalize_token = None
            session.accepted_chunk_digests.clear()
            self._release_admission_locked(session)
            return session

    def abort_chunk(
        self,
        principal: VoicePrincipal,
        session_id: str,
        *,
        chunk_sequence: int,
        chunk_digest: str,
    ) -> None:
        """Release only the caller's still-current reservation."""

        with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.tenant_id != principal.tenant_id
                or session.owner_subject != principal.subject
            ):
                return
            if (
                session.inflight_chunk_sequence == chunk_sequence
                and session.inflight_chunk_digest == chunk_digest
            ):
                session.inflight_chunk_sequence = None
                session.inflight_chunk_digest = None
                session.inflight_chunk_bytes = None

    def accept_chunk(
        self,
        principal: VoicePrincipal,
        session_id: str,
        *,
        chunk_sequence: int,
        chunk_digest: str | None = None,
    ) -> HubVoiceStreamSession:
        """Backward-compatible atomic helper for in-process callers/tests."""

        digest = chunk_digest or ("0" * 64)
        reservation = self.begin_chunk(
            principal,
            session_id,
            chunk_sequence=chunk_sequence,
            chunk_digest=digest,
        )
        if reservation.replayed:
            return reservation.session
        return self.complete_chunk(
            principal,
            session_id,
            chunk_sequence=chunk_sequence,
            chunk_digest=digest,
        )

    def mark_final(self, principal: VoicePrincipal, session_id: str, *, result_ref: str) -> HubVoiceStreamSession:
        reservation = self.begin_finalize(principal, session_id)
        return self.complete_finalize(
            principal,
            session_id,
            token=reservation.token,
            result_ref=result_ref,
        )

    def delete(self, principal: VoicePrincipal, session_id: str) -> HubVoiceStreamSession:
        with self._lock:
            session = self.require(principal, session_id)
            self._sessions.pop(session.session_id, None)
            session.state = "closed"
            session.accepted_chunk_digests.clear()
            session.inflight_chunk_sequence = None
            session.inflight_chunk_digest = None
            session.inflight_chunk_bytes = None
            session.finalize_token = None
            self._release_admission_locked(session)
            return session

    def revoke_profile(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        before_remove: ProfileRemovalHook | None = None,
    ) -> tuple[HubVoiceStreamSession, ...]:
        """Remove every active session carrying a now-revoked snapshot.

        A supplied hook must durably stage external cleanup before these
        ephemeral capability mappings are destroyed.  Hook failures leave all
        mappings intact so callers fail closed.
        """

        normalized_profile = validate_identifier(profile_id, field="profile_id")
        with self._lock:
            revoked = tuple(
                session
                for session in self._sessions.values()
                if session.tenant_id == principal.tenant_id
                and session.owner_subject == principal.subject
                and session.profile_id == normalized_profile
                and session.state not in {"final", "failed", "closed"}
            )
            if revoked and before_remove is not None:
                before_remove(revoked)
            for session in revoked:
                self._sessions.pop(session.session_id, None)
                session.state = "closed"
                session.accepted_chunk_digests.clear()
                session.inflight_chunk_sequence = None
                session.inflight_chunk_digest = None
                session.inflight_chunk_bytes = None
                session.finalize_token = None
                self._release_admission_locked(session)
            return revoked

    def delete_profile(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        before_remove: ProfileRemovalHook | None = None,
    ) -> tuple[HubVoiceStreamSession, ...]:
        """Remove all ephemeral Hub mappings for a deleted profile.

        Unlike consent revocation this also removes terminal mappings.  Runtime
        capability IDs remain only on the returned short-lived objects.  The
        pre-removal hook is responsible for durable remote-cleanup staging.
        """

        normalized_profile = validate_identifier(profile_id, field="profile_id")
        with self._lock:
            deleted = tuple(
                session
                for session in self._sessions.values()
                if session.tenant_id == principal.tenant_id
                and session.owner_subject == principal.subject
                and session.profile_id == normalized_profile
            )
            if deleted and before_remove is not None:
                before_remove(deleted)
            for session in deleted:
                self._sessions.pop(session.session_id, None)
                session.state = "closed"
                session.accepted_chunk_digests.clear()
                session.inflight_chunk_sequence = None
                session.inflight_chunk_digest = None
                session.inflight_chunk_bytes = None
                session.finalize_token = None
                self._release_admission_locked(session)
            return deleted

    def delete_profile_before(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        deleted_at: float,
        before_remove: ProfileRemovalHook | None = None,
    ) -> tuple[HubVoiceStreamSession, ...]:
        """Remove only mappings that predate a durable deletion cutoff."""

        normalized_profile = validate_identifier(profile_id, field="profile_id")
        with self._lock:
            deleted = tuple(
                session
                for session in self._sessions.values()
                if session.tenant_id == principal.tenant_id
                and session.owner_subject == principal.subject
                and session.profile_id == normalized_profile
                and session.created_at <= deleted_at
            )
            if deleted and before_remove is not None:
                before_remove(deleted)
            for session in deleted:
                self._sessions.pop(session.session_id, None)
                session.state = "closed"
                session.accepted_chunk_digests.clear()
                session.inflight_chunk_sequence = None
                session.inflight_chunk_digest = None
                session.inflight_chunk_bytes = None
                session.finalize_token = None
                self._release_admission_locked(session)
            return deleted

    def _purge_locked(self) -> None:
        now = time.time()
        expired = tuple(session for session in self._sessions.values() if session.deadline_at <= now)
        if expired:
            self._expire_locked(expired)
        for session_id, session in tuple(self._sessions.items()):
            if session.state == "closed":
                self._sessions.pop(session_id, None)
                self._release_admission_locked(session)

    def _expire_locked(self, expired: tuple[HubVoiceStreamSession, ...]) -> None:
        """Stage external cleanup before dropping local capabilities."""

        self._expiration_hook(expired)
        for session in expired:
            self._sessions.pop(session.session_id, None)
            session.state = "closed" if session.state == "final" else "failed"
            session.accepted_chunk_digests.clear()
            session.inflight_chunk_sequence = None
            session.inflight_chunk_digest = None
            session.inflight_chunk_bytes = None
            session.finalize_token = None
            session.runtime_session_id = ""
            session.task_id = None
            self._release_admission_locked(session)

    def _release_admission_locked(self, session: HubVoiceStreamSession) -> None:
        lease_id = session.admission_lease_id
        session.admission_lease_id = None
        if lease_id:
            self._admission_release(lease_id)


def _release_voice_admission(lease_id: str | None) -> None:
    from agent.services.voice_admission_service import get_voice_admission_service

    get_voice_admission_service().release(lease_id)


def _expire_voice_streams(sessions: tuple[HubVoiceStreamSession, ...]) -> None:
    """Durably delete abandoned Runtime work and terminalize its Hub task."""

    from agent.services.voice_delegation_task_service import get_voice_delegation_task_service
    from agent.services.voice_runtime_cleanup_service import (
        VoiceRuntimeCleanupTarget,
        get_voice_runtime_cleanup_service,
    )

    cleanup = get_voice_runtime_cleanup_service()
    scopes: dict[tuple[str, str, str], VoicePrincipal] = {}
    for session in sessions:
        principal = VoicePrincipal(tenant_id=session.tenant_id, subject=session.owner_subject)
        cleanup.stage(
            principal,
            profile_id=session.profile_id,
            operation="stream_expire",
            targets=(
                VoiceRuntimeCleanupTarget(
                    source_session_id=session.session_id,
                    runtime_session_id=session.runtime_session_id,
                ),
            ),
        )
        scopes[(principal.tenant_id, principal.subject, session.profile_id)] = principal
    for (_tenant_id, _owner_subject, profile_id), principal in scopes.items():
        cleanup.retry_profile(principal, profile_id)
    delegation = get_voice_delegation_task_service()
    for session in sessions:
        if session.task_id and session.state not in {"final", "closed"}:
            delegation.cancel(session.task_id, reason_code="voice_stream_deadline_exceeded")


voice_stream_session_service = VoiceStreamSessionService(
    admission_release=_release_voice_admission,
    expiration_hook=_expire_voice_streams,
)


def get_voice_stream_session_service() -> VoiceStreamSessionService:
    return voice_stream_session_service
