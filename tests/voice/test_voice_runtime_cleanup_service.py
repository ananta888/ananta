from __future__ import annotations

import time
from unittest.mock import patch

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TaskDB, VoiceGovernanceIdempotencyDB, VoiceRuntimeCleanupDB
from agent.repositories.voice_runtime_cleanup import VoiceRuntimeCleanupRepository
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_runtime_cleanup_service import (
    VoiceRuntimeCleanupService,
    VoiceRuntimeCleanupTarget,
)
from agent.services.voice_stream_session_service import VoiceStreamSessionService


class _TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return f"encrypted::{value[::-1]}" if value is not None else None

    def decrypt(self, value: str | None) -> str | None:
        if value is None or not value.startswith("encrypted::"):
            return None
        return value.removeprefix("encrypted::")[::-1]


def test_cleanup_survives_service_recreation_and_retries_without_persisting_content() -> None:
    principal = VoicePrincipal(tenant_id="cleanup-tenant", subject="cleanup-owner")
    profile_id = "cleanup-profile"
    target = VoiceRuntimeCleanupTarget(
        source_session_id="hub-session-restart",
        runtime_session_id="runtime-session-secret",
    )
    failed_calls: list[tuple[str, str]] = []

    def unavailable(runtime_session_id: str, request_id: str) -> None:
        with Session(engine) as session:
            durable_before_remote_call = session.exec(select(VoiceRuntimeCleanupDB)).one()
            assert durable_before_remote_call.state == "pending"
            assert durable_before_remote_call.attempt_count == 1
        failed_calls.append((runtime_session_id, request_id))
        raise RuntimeError("private transport detail")

    first_service = VoiceRuntimeCleanupService(
        repository=VoiceRuntimeCleanupRepository(),
        codec=_TestCodec(),
        runtime_stream_delete=unavailable,
        audit_sink=lambda _event, _details: None,
    )
    assert first_service.stage(
        principal,
        profile_id=profile_id,
        operation="profile_delete",
        targets=(target,),
    ) == 1
    first_run = first_service.retry_profile(principal, profile_id)

    assert first_run.public() == {
        "runtime_cleanup_pending": True,
        "runtime_cleanup_failed_count": 1,
    }
    assert failed_calls == [("runtime-session-secret", "privacy-delete-hub-session-restart")]
    with Session(engine) as session:
        stored = session.exec(select(VoiceRuntimeCleanupDB)).one()
        assert stored.state == "failed"
        assert stored.attempt_count == 1
        assert stored.failure_reason_code == "runtime_delete_failed"
        assert stored.runtime_session_ciphertext != "runtime-session-secret"
        assert "runtime-session-secret" not in stored.runtime_session_ciphertext
        assert not hasattr(stored, "audio")
        assert not hasattr(stored, "text")

    recovered_calls: list[tuple[str, str]] = []
    restarted_service = VoiceRuntimeCleanupService(
        repository=VoiceRuntimeCleanupRepository(),
        codec=_TestCodec(),
        runtime_stream_delete=lambda runtime_id, request_id: recovered_calls.append((runtime_id, request_id)),
        audit_sink=lambda _event, _details: None,
    )
    assert restarted_service.retry_all_pending() == 1
    recovered = restarted_service.retry_profile(principal, profile_id)

    assert recovered.public() == {
        "runtime_cleanup_pending": False,
        "runtime_cleanup_failed_count": 0,
    }
    assert recovered_calls == [("runtime-session-secret", "privacy-delete-hub-session-restart")]
    with Session(engine) as session:
        assert session.exec(select(VoiceRuntimeCleanupDB)).all() == []


def test_cleanup_retry_is_fail_closed_to_tenant_owner_and_profile_scope() -> None:
    owner = VoicePrincipal(tenant_id="scope-tenant", subject="scope-owner")
    other_owner = VoicePrincipal(tenant_id="scope-tenant", subject="other-owner")
    calls: list[str] = []
    service = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        runtime_stream_delete=lambda runtime_id, _request_id: calls.append(runtime_id),
        audit_sink=lambda _event, _details: None,
    )
    service.stage(
        owner,
        profile_id="profile-a",
        operation="consent_revoke",
        targets=(
            VoiceRuntimeCleanupTarget(
                source_session_id="hub-session-scope",
                runtime_session_id="runtime-session-scope",
            ),
        ),
    )

    hidden = service.retry_profile(other_owner, "profile-a")
    wrong_profile = service.retry_profile(owner, "profile-b")

    assert hidden.attempted_count == 0
    assert wrong_profile.attempted_count == 0
    assert calls == []
    assert service.retry_profile(owner, "profile-a").public()["runtime_cleanup_pending"] is False
    assert calls == ["runtime-session-scope"]


def test_provisional_stream_cleanup_runs_only_during_explicit_restart_recovery() -> None:
    principal = VoicePrincipal(tenant_id="provisional-tenant", subject="provisional-owner")
    calls: list[str] = []
    service = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        runtime_stream_delete=lambda runtime_id, _request_id: calls.append(runtime_id),
        audit_sink=lambda _event, _details: None,
    )
    service.stage(
        principal,
        profile_id="provisional-profile",
        operation="stream_orphan",
        targets=(
            VoiceRuntimeCleanupTarget(
                source_session_id="voice-stream-provisional",
                runtime_session_id="vs_provisional-runtime",
            ),
        ),
        provisional=True,
    )

    assert service.retry_all_pending() == 0
    assert calls == []
    with Session(engine) as session:
        record = session.exec(select(VoiceRuntimeCleanupDB)).one()
        assert record.state == "provisional"

    assert service.retry_all_pending(include_provisional=True) == 1
    assert calls == ["vs_provisional-runtime"]
    with Session(engine) as session:
        assert session.exec(select(VoiceRuntimeCleanupDB)).all() == []


def test_hub_restart_cleanup_invalidates_ephemeral_stream_replay_metadata() -> None:
    from agent.bootstrap.voice_runtime_cleanup import recover_voice_runtime_cleanup

    principal = VoicePrincipal(tenant_id="restart-tenant", subject="restart-owner")
    cleanup = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        runtime_stream_delete=lambda _runtime_id, _request_id: None,
        audit_sink=lambda _event, _details: None,
    )
    cleanup.stage(
        principal,
        profile_id="restart-profile",
        operation="stream_orphan",
        targets=(
            VoiceRuntimeCleanupTarget(
                source_session_id="voice-stream-restart",
                runtime_session_id="vs_restart-runtime",
            ),
        ),
        provisional=True,
    )
    idempotency = VoiceIdempotencyService()
    claim = idempotency.begin(
        principal,
        operation="voice_stream.create",
        idempotency_key="restart-key",
        payload={"profile_id": "restart-profile"},
    )
    idempotency.complete(
        claim,
        {"session_id": "voice-stream-restart", "task_id": ""},
    )

    with (
        patch(
            "agent.bootstrap.voice_runtime_cleanup.get_voice_runtime_cleanup_service",
            return_value=cleanup,
        ),
        patch(
            "agent.bootstrap.voice_runtime_cleanup.get_voice_deletion_reconciliation_service"
        ) as reconciliation,
        patch("agent.bootstrap.voice_runtime_cleanup.settings.role", "hub"),
    ):
        recover_voice_runtime_cleanup()

    reconciliation.return_value.reconcile_all.assert_called_once_with()
    with Session(engine) as session:
        assert session.exec(select(VoiceRuntimeCleanupDB)).all() == []
        assert (
            session.exec(
                select(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.operation == "voice_stream.create"
                )
            ).all()
            == []
        )


def test_stream_profile_removal_fails_closed_when_durable_staging_fails() -> None:
    principal = VoicePrincipal(tenant_id="hook-tenant", subject="hook-owner")
    sessions = VoiceStreamSessionService()
    stream = sessions.create(
        principal,
        runtime_session_id="runtime-hook",
        deadline_seconds=60,
        profile_id="profile-hook",
    )

    def staging_failed(_sessions) -> None:
        raise RuntimeError("database unavailable")

    try:
        sessions.revoke_profile(
            principal,
            "profile-hook",
            before_remove=staging_failed,
        )
    except RuntimeError as error:
        assert str(error) == "database unavailable"
    else:
        raise AssertionError("staging failure must propagate")

    assert sessions.require(principal, stream.session_id) is stream
    assert stream.state == "created"


def test_stream_expiration_keeps_capability_and_lease_when_cleanup_staging_fails() -> None:
    principal = VoicePrincipal(tenant_id="expiry-tenant", subject="expiry-owner")
    releases: list[str | None] = []
    fail_staging = True

    def expiration_hook(_sessions) -> None:
        if fail_staging:
            raise RuntimeError("cleanup outbox unavailable")

    sessions = VoiceStreamSessionService(
        admission_release=releases.append,
        expiration_hook=expiration_hook,
    )
    stream = sessions.create(
        principal,
        runtime_session_id="runtime-expiry-hook",
        deadline_seconds=60,
        profile_id="profile-expiry-hook",
        admission_lease_id="voice-lease-expiry-hook",
    )
    stream.deadline_at = time.time() - 1

    try:
        sessions.require(principal, stream.session_id)
    except RuntimeError as error:
        assert str(error) == "cleanup outbox unavailable"
    else:
        raise AssertionError("expiration cleanup staging failure must propagate")

    assert sessions._sessions[stream.session_id] is stream
    assert stream.runtime_session_id == "runtime-expiry-hook"
    assert releases == []

    fail_staging = False
    try:
        sessions.require(principal, stream.session_id)
    except VoiceGovernanceError as error:
        assert error.code == "voice_stream.deadline_exceeded"
    else:
        raise AssertionError("expired session must not remain accessible")

    assert stream.session_id not in sessions._sessions
    assert stream.runtime_session_id == ""
    assert releases == ["voice-lease-expiry-hook"]


def test_restricted_cache_gc_failure_survives_recreation_and_replays() -> None:
    principal = VoicePrincipal(tenant_id="cache-tenant", subject="cache-owner")
    profile_id = "cache-profile"
    failed_requests: list[str] = []

    def unavailable(_principal: VoicePrincipal, request_id: str) -> None:
        failed_requests.append(request_id)
        raise RuntimeError("worker unavailable")

    first = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        restricted_cache_gc=unavailable,
        audit_sink=lambda _event, _details: None,
    )
    assert first.stage_cache_gc(
        principal,
        profile_id=profile_id,
        operation="profile_delete",
    ) == 1
    failed = first.retry_profile(principal, profile_id)

    assert failed.public() == {
        "runtime_cleanup_pending": True,
        "runtime_cleanup_failed_count": 1,
    }
    with Session(engine) as session:
        stored = session.exec(select(VoiceRuntimeCleanupDB)).one()
        assert stored.cleanup_kind == "restricted_cache_gc"
        assert stored.runtime_session_ciphertext is None
        assert stored.target_digest is None
        assert stored.failure_reason_code == "restricted_cache_gc_failed"

    recovered_requests: list[str] = []
    restarted = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        restricted_cache_gc=lambda _principal, request_id: recovered_requests.append(request_id),
        audit_sink=lambda _event, _details: None,
    )
    assert restarted.retry_all_pending() == 1

    assert len(failed_requests) == 1
    assert recovered_requests == failed_requests
    assert profile_id not in recovered_requests[0]
    assert restarted.retry_profile(principal, profile_id).public() == {
        "runtime_cleanup_pending": False,
        "runtime_cleanup_failed_count": 0,
    }


def test_default_cache_gc_task_persists_only_system_identity_and_scope_digest() -> None:
    principal = VoicePrincipal(tenant_id="private-tenant", subject="private-owner")
    profile_id = "private-profile"

    class _Management:
        @staticmethod
        def cache_gc() -> dict:
            return {"removed_entries": 2}

    service = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        audit_sink=lambda _event, _details: None,
    )
    service.stage_cache_gc(
        principal,
        profile_id=profile_id,
        operation="profile_delete",
    )
    with patch(
        "agent.services.restricted_inference_management_service.get_restricted_inference_management_service",
        return_value=_Management(),
    ):
        result = service.retry_profile(principal, profile_id)

    assert result.public()["runtime_cleanup_pending"] is False
    with Session(engine) as session:
        task = session.exec(select(TaskDB).where(TaskDB.task_kind == "restricted_inference_management")).one()
        persisted = str(task.model_dump())
        assert "hub-privacy-cleanup" in persisted
        assert principal.tenant_id not in persisted
        assert principal.subject not in persisted
        assert profile_id not in persisted


def test_retry_all_pending_coalesces_scope_audits_into_one_content_free_batch() -> None:
    events: list[tuple[str, dict]] = []
    service = VoiceRuntimeCleanupService(
        codec=_TestCodec(),
        restricted_cache_gc=lambda _principal, _request_id: None,
        audit_sink=lambda event, details: events.append((event, details)),
    )
    for suffix in ("a", "b"):
        service.stage_cache_gc(
            VoicePrincipal(
                tenant_id=f"batch-tenant-{suffix}",
                subject=f"batch-owner-{suffix}",
            ),
            profile_id=f"batch-profile-{suffix}",
            operation="profile_delete",
        )

    assert service.retry_all_pending() == 2
    assert events == [
        (
            "voice_runtime_cleanup_batch_processed",
            {
                "scope_count": 2,
                "attempted_count": 2,
                "succeeded_count": 2,
                "failed_count": 0,
                "pending_scope_count": 0,
            },
        )
    ]
    assert "batch-tenant" not in str(events)
    assert "batch-owner" not in str(events)
    assert "batch-profile" not in str(events)
