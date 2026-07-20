from __future__ import annotations

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechEvidenceDB,
    SpeechEvidenceKeyDB,
    SpeechEvidenceRevocationDB,
)
from agent.repositories.speech_evidence_lineage import (
    SpeechLineageEdge,
    SpeechLineageNode,
    get_speech_evidence_lineage_repository,
)
from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
from agent.services.speech_evidence_retention_cleanup_service import (
    SpeechEvidenceRetentionCleanupService,
)
from tests.speech_evidence_support import digest, principal, stored_evidence


class FailOnceArtifacts:
    def __init__(self) -> None:
        self.calls = 0

    def cleanup(self, **_kwargs) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary cleanup failure")
        return True


def test_expired_quarantine_cleanup_is_bounded_resumable_and_content_free() -> None:
    prefix = "cleanup-expired"
    _consent_service, _store, _consent, record = stored_evidence(prefix, b"cleanup secret")
    artifacts = FailOnceArtifacts()
    service = SpeechEvidenceRetentionCleanupService(artifacts=artifacts)
    first = service.run_once(limit=1, now_ms=record.expires_at_ms + 1, principal=principal(prefix))
    second = service.run_once(limit=1, now_ms=record.expires_at_ms + 2, principal=principal(prefix))
    with Session(engine) as session:
        evidence = session.get(SpeechEvidenceDB, record.evidence_id)
        tombstone = session.exec(
            __import__("sqlmodel")
            .select(SpeechEvidenceRevocationDB)
            .where(SpeechEvidenceRevocationDB.evidence_digest == record.content_digest)
        ).one()
    assert first.failed == 1
    assert second.completed == 1
    assert evidence is not None and evidence.state == "deleted" and bytes(evidence.ciphertext) == b""
    assert tombstone.reason_code == "speech_evidence_retention_expired"
    assert "cleanup secret" not in str(tombstone)


def test_active_manifest_reference_prevents_expiry_cleanup() -> None:
    prefix = "cleanup-referenced"
    _consent_service, _store, consent, record = stored_evidence(prefix, b"referenced")
    manifest_digest = digest(f"manifest-{prefix}")
    get_ml_intern_speech_lineage_service().publish(
        principal(prefix),
        nodes=(
            SpeechLineageNode("evidence", record.content_digest, consent_id=consent.consent_id),
            SpeechLineageNode("manifest", manifest_digest, consent_id=consent.consent_id),
        ),
        edges=(SpeechLineageEdge("evidence", record.content_digest, "manifest", manifest_digest, "included_in"),),
    )
    summary = SpeechEvidenceRetentionCleanupService().run_once(
        limit=1, now_ms=record.expires_at_ms + 1, principal=principal(prefix)
    )
    with Session(engine) as session:
        evidence = session.get(SpeechEvidenceDB, record.evidence_id)
    assert summary.skipped_active_references == 1
    assert evidence is not None and evidence.state == "quarantined"


class _PublishDuringArtifactCleanup:
    def __init__(self, *, prefix: str, record, consent, kind: str) -> None:
        self.prefix = prefix
        self.record = record
        self.consent = consent
        self.kind = kind
        self.target_digest = digest(f"{prefix}-{kind}-race")

    def cleanup(self, **_kwargs) -> bool:
        get_ml_intern_speech_lineage_service().publish(
            principal(self.prefix),
            nodes=(
                SpeechLineageNode(
                    "evidence",
                    self.record.content_digest,
                    consent_id=self.consent.consent_id,
                ),
                SpeechLineageNode(
                    self.kind,
                    self.target_digest,
                    consent_id=self.consent.consent_id,
                ),
            ),
            edges=(
                SpeechLineageEdge(
                    "evidence",
                    self.record.content_digest,
                    self.kind,
                    self.target_digest,
                    "race_published",
                ),
            ),
        )
        return True


@pytest.mark.parametrize(
    ("phase", "kind"),
    [
        ("curation", "reconciliation"),
        ("dataset", "manifest"),
        ("offline_reconciliation", "checkpoint"),
        ("training", "job"),
        ("evaluation", "evaluation"),
        ("approval", "adapter"),
        ("export", "export"),
    ],
)
def test_new_active_descendant_at_destructive_boundary_fences_cleanup(
    phase: str,
    kind: str,
) -> None:
    prefix = f"cleanup-race-{phase}"
    _consent_service, _store, consent, record = stored_evidence(
        prefix, f"retention race {phase}".encode()
    )
    artifacts = _PublishDuringArtifactCleanup(
        prefix=prefix,
        record=record,
        consent=consent,
        kind=kind,
    )
    service = SpeechEvidenceRetentionCleanupService(artifacts=artifacts)

    raced = service.run_once(
        limit=1,
        now_ms=record.expires_at_ms + 1,
        principal=principal(prefix),
    )
    with Session(engine) as session:
        evidence = session.get(SpeechEvidenceDB, record.evidence_id)
        key = session.get(SpeechEvidenceKeyDB, record.key_id)
    assert raced.failed == 1 and raced.completed == 0
    assert evidence is not None and evidence.state == "cleanup_pending"
    assert evidence.ciphertext and evidence.nonce
    assert key is not None and key.destroyed_at_ms is None and key.wrapped_dek

    # Once the raced descendant itself is revoked, the persisted cleanup can
    # safely adopt the newer impact decision and resume idempotently.
    get_speech_evidence_lineage_repository().mark_status(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        nodes=((kind, artifacts.target_digest),),
        status="revoked",
        revocation_epoch=1,
        now_ms=record.expires_at_ms + 2,
    )
    resumed = service.run_once(
        limit=1,
        now_ms=record.expires_at_ms + 2,
        principal=principal(prefix),
    )
    with Session(engine) as session:
        evidence = session.get(SpeechEvidenceDB, record.evidence_id)
    assert resumed.completed == 1
    assert evidence is not None and evidence.state == "deleted" and not evidence.ciphertext


def test_cleanup_is_owner_scoped_during_profile_deletion_reconciliation() -> None:
    first_prefix = "cleanup-profile-owner-a"
    second_prefix = "cleanup-profile-owner-b"
    _first_consent, _first_store, _grant_a, first = stored_evidence(first_prefix, b"owner a")
    _second_consent, _second_store, _grant_b, second = stored_evidence(second_prefix, b"owner b")

    summary = SpeechEvidenceRetentionCleanupService().run_once(
        limit=10,
        now_ms=max(first.expires_at_ms, second.expires_at_ms) + 1,
        principal=principal(first_prefix),
    )
    with Session(engine) as session:
        first_row = session.get(SpeechEvidenceDB, first.evidence_id)
        second_row = session.get(SpeechEvidenceDB, second.evidence_id)
    assert summary.completed == 1
    assert first_row is not None and first_row.state == "deleted"
    assert second_row is not None and second_row.state == "quarantined"
