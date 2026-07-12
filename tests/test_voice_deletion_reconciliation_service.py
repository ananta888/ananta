from __future__ import annotations

import hashlib
import time

from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import (
    ArchivedTaskDB,
    TaskDB,
    VoiceConsentDB,
    VoiceDeletionTombstoneDB,
    VoiceGovernanceIdempotencyDB,
    VoicePersonalizationProfileDB,
    VoiceResultArtifactDB,
    VoiceReviewDB,
)
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.services.voice_deletion_ledger import VoiceDeletionLedger
from agent.services.voice_deletion_reconciliation_service import VoiceDeletionReconciliationService
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_runtime_cleanup_service import VoiceRuntimeCleanupService


class _UnusedCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


def _artifact(
    principal: VoicePrincipal,
    profile_id: str,
    artifact_id: str,
    *,
    created_at: float,
) -> VoiceResultArtifactDB:
    return VoiceResultArtifactDB(
        id=artifact_id,
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        profile_id=profile_id,
        request_hash=hashlib.sha256(artifact_id.encode()).hexdigest(),
        payload_ciphertext="encrypted-backup-residue",
        payload_digest="1" * 64,
        expires_at=time.time() + 3_600,
        created_at=created_at,
    )


def test_external_ledger_restore_reconciliation_deletes_old_residue_but_keeps_new_consent_and_data(
    tmp_path,
) -> None:
    principal = VoicePrincipal(tenant_id="restore-tenant", subject="restore-owner")
    profile_id = "restore-profile"
    ledger = VoiceDeletionLedger(tmp_path / "external-ledger.jsonl")
    tombstones = VoiceDeletionTombstoneRepository(ledger=ledger)
    claim = tombstones.claim(
        principal,
        profile_id,
        idempotency_key="restore-delete-key",
    )
    old_created_at = claim.deleted_at - 10
    new_created_at = claim.deleted_at + 10

    # Simulate a full database restore to a snapshot that predates deletion:
    # the DB projection disappears, while the separately persisted ledger stays.
    with Session(engine) as session:
        session.exec(delete(VoiceDeletionTombstoneDB))
        session.add(_artifact(principal, profile_id, "voice-result-restored-old", created_at=old_created_at))
        session.add(_artifact(principal, profile_id, "voice-result-new", created_at=new_created_at))
        session.add(
            VoiceReviewDB(
                id="restored-old-review",
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                profile_id=profile_id,
                session_id="restored-old-session",
                result_ref="voice-result-restored-old",
                created_at=old_created_at,
                updated_at=old_created_at,
            )
        )
        session.add(
            VoiceConsentDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                profile_id=profile_id,
                granted=True,
                created_at=new_created_at,
                updated_at=new_created_at,
            )
        )
        session.add(
            VoicePersonalizationProfileDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                profile_id=profile_id,
                created_at=new_created_at,
                updated_at=new_created_at,
            )
        )
        session.add(
            VoiceGovernanceIdempotencyDB(
                id="restored-old-idempotency",
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                operation=f"voice_personalization.import:{profile_id}",
                idempotency_key="old-key",
                request_hash="2" * 64,
                state="completed",
                lease_expires_at=old_created_at,
                created_at=old_created_at,
                updated_at=old_created_at,
            )
        )
        session.add(
            VoiceGovernanceIdempotencyDB(
                id="new-idempotency",
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                operation=f"voice_personalization.import:{profile_id}",
                idempotency_key="new-key",
                request_hash="3" * 64,
                state="completed",
                lease_expires_at=new_created_at,
                created_at=new_created_at,
                updated_at=new_created_at,
            )
        )
        for record_id, operation, result_metadata in (
            ("restored-review-create", "voice_review.create", {"review_id": "restored-old-review"}),
            (
                "restored-review-decide",
                "voice_review.decide:restored-old-review",
                {},
            ),
            (
                "restored-transcribe",
                "voice.transcribe",
                {"result_ref": "voice-result-restored-old"},
            ),
            (
                "restored-profile-config",
                f"voice_configuration.put:profile:{profile_id}",
                {"scope_id": profile_id},
            ),
            (
                "restored-session-config",
                "voice_configuration.put:session:restored-old-session",
                {"scope_id": "restored-old-session"},
            ),
        ):
            session.add(
                VoiceGovernanceIdempotencyDB(
                    id=record_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    operation=operation,
                    idempotency_key=record_id,
                    request_hash=hashlib.sha256(record_id.encode()).hexdigest(),
                    state="completed",
                    lease_expires_at=old_created_at,
                    result_metadata=result_metadata,
                    created_at=old_created_at,
                    updated_at=old_created_at,
                )
            )
        session.commit()

    cache_gc_calls: list[str] = []
    cleanup = VoiceRuntimeCleanupService(
        codec=_UnusedCodec(),
        restricted_cache_gc=lambda _principal, request_id: cache_gc_calls.append(request_id),
        audit_sink=lambda _event, _details: None,
    )
    audit_events: list[tuple[str, dict]] = []
    reconciliation = VoiceDeletionReconciliationService(
        tombstones=tombstones,
        runtime_cleanup=cleanup,
        audit_sink=lambda event, details: audit_events.append((event, details)),
    )

    result = reconciliation.reconcile_all()
    cleanup.retry_all_pending()

    assert result.reconciled_scope_count == 1
    assert result.deleted_record_count >= 8
    with Session(engine) as session:
        assert session.get(VoiceResultArtifactDB, "voice-result-restored-old") is None
        assert session.get(VoiceResultArtifactDB, "voice-result-new") is not None
        consent = session.exec(select(VoiceConsentDB)).one()
        assert consent.granted is True
        assert session.exec(select(VoicePersonalizationProfileDB)).one() is not None
        assert session.get(VoiceGovernanceIdempotencyDB, "restored-old-idempotency") is None
        assert session.get(VoiceGovernanceIdempotencyDB, "restored-review-create") is None
        assert session.get(VoiceGovernanceIdempotencyDB, "restored-review-decide") is None
        assert session.get(VoiceGovernanceIdempotencyDB, "restored-transcribe") is None
        assert session.get(VoiceGovernanceIdempotencyDB, "restored-profile-config") is None
        assert session.get(VoiceGovernanceIdempotencyDB, "restored-session-config") is None
        assert session.get(VoiceGovernanceIdempotencyDB, "new-idempotency") is not None
        tombstone = session.exec(select(VoiceDeletionTombstoneDB)).one()
        assert tombstone.reconciliation_count == 1
        assert tombstone.last_reconciled_at is not None
        assert not hasattr(tombstone, "tenant_id")
        assert not hasattr(tombstone, "owner_subject")
        assert not hasattr(tombstone, "profile_id")
    ledger_content = ledger.path.read_text(encoding="utf-8")
    assert principal.tenant_id not in ledger_content
    assert principal.subject not in ledger_content
    assert profile_id not in ledger_content
    assert "restore-delete-key" not in ledger_content
    assert len(cache_gc_calls) == 1
    assert audit_events[0][0] == "voice_deletion_reconciled"
    assert set(audit_events[0][1]) == {"scope_digest", "deleted_count", "status"}
    assert profile_id not in str(audit_events)
    assert principal.tenant_id not in str(audit_events)
    assert principal.subject not in str(audit_events)


def test_reconciliation_deletes_pure_task_and_archive_scopes_by_pseudonymous_digest(tmp_path) -> None:
    principal = VoicePrincipal(tenant_id="task-restore-tenant", subject="task-restore-owner")
    profile_id = "task-restore-profile"
    ledger = VoiceDeletionLedger(tmp_path / "task-ledger.jsonl")
    tombstones = VoiceDeletionTombstoneRepository(ledger=ledger)
    claim = tombstones.claim(principal, profile_id, idempotency_key="task-delete-key")
    old_created_at = claim.deleted_at - 1
    context = {
        "voice_transcription": {
            "deletion_scope_digest": claim.scope_digest,
            "profile_id": profile_id,
            "persistence_owner": "hub",
        }
    }
    with Session(engine) as session:
        session.exec(delete(VoiceDeletionTombstoneDB))
        session.add(
            TaskDB(
                id="restored-voice-task",
                task_kind="voice_transcription",
                created_at=old_created_at,
                updated_at=old_created_at,
                worker_execution_context=context,
            )
        )
        session.add(
            ArchivedTaskDB(
                id="restored-voice-archive",
                task_kind="voice_transcription",
                created_at=old_created_at,
                updated_at=old_created_at,
                archived_at=old_created_at,
                worker_execution_context=context,
            )
        )
        session.add(
            TaskDB(
                id="foreign-voice-task",
                task_kind="voice_transcription",
                created_at=old_created_at,
                updated_at=old_created_at,
                worker_execution_context={
                    "voice_transcription": {"deletion_scope_digest": "f" * 64}
                },
            )
        )
        session.commit()

    reconciliation = VoiceDeletionReconciliationService(
        tombstones=tombstones,
        runtime_cleanup=VoiceRuntimeCleanupService(
            codec=_UnusedCodec(),
            restricted_cache_gc=lambda _principal, _request_id: None,
            audit_sink=lambda _event, _details: None,
        ),
        audit_sink=lambda _event, _details: None,
    )

    result = reconciliation.reconcile_all()

    assert result.deleted_record_count == 2
    with Session(engine) as session:
        assert session.get(TaskDB, "restored-voice-task") is None
        assert session.get(ArchivedTaskDB, "restored-voice-archive") is None
        assert session.get(TaskDB, "foreign-voice-task") is not None


def test_reconciliation_paginates_all_tombstones_past_page_boundary(tmp_path) -> None:
    ledger = VoiceDeletionLedger(
        tmp_path / "pagination-ledger.jsonl",
        max_records_per_segment=2,
        max_total_records=20,
    )
    tombstones = VoiceDeletionTombstoneRepository(ledger=ledger)
    for index in range(7):
        tombstones.claim(
            VoicePrincipal(tenant_id=f"page-tenant-{index}", subject="page-owner"),
            "page-profile",
            idempotency_key=f"page-delete-{index}",
        )
    reconciliation = VoiceDeletionReconciliationService(
        tombstones=tombstones,
        runtime_cleanup=VoiceRuntimeCleanupService(
            codec=_UnusedCodec(),
            restricted_cache_gc=lambda _principal, _request_id: None,
            audit_sink=lambda _event, _details: None,
        ),
        audit_sink=lambda _event, _details: None,
    )

    first = reconciliation.reconcile_all(page_size=2)
    second = reconciliation.reconcile_all(page_size=3)

    assert first.reconciled_scope_count == 7
    assert second.reconciled_scope_count == 7
    with Session(engine) as session:
        rows = session.exec(select(VoiceDeletionTombstoneDB)).all()
    assert len(rows) == 7
    assert all(row.reconciliation_count == 2 for row in rows)
