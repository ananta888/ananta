from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    MlInternSpeechAdapterDB,
    SemanticMediaAuditOutboxDB,
    SpeechLineageOutboxDB,
)
from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository
from agent.services.ml_intern_speech_adapter_export import (
    EncryptedSpeechAdapterExportService,
    SpeechAdapterExportConsentBinding,
)
from agent.services.ml_intern_speech_adapter_registry import (
    MlInternSpeechAdapterRegistry,
    SpeechAdapterExportReceipt,
    SpeechAdapterNotFound,
    SpeechAdapterRegistryError,
    SpeechAdapterVersionConflict,
)
from agent.services.ml_intern_speech_eval_service import MlInternSpeechEvalService
from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import speech_scope_digest
from tests.speech_adaptation_support import digest, speech_job
from worker.speech_training.evaluation import build_mock_evaluation


class _ExportPort:
    def __init__(self, *, consent: bool = True) -> None:
        self.consent = consent

    def verify_export_consent(
        self, record, *, export_consent_digest, export_consent_epoch
    ):
        del record, export_consent_digest, export_consent_epoch
        return self.consent

    def encrypt_export(
        self, record, *, destination_ref, export_consent_digest, export_consent_epoch
    ):
        del record, export_consent_digest, export_consent_epoch
        return SpeechAdapterExportReceipt(
            export_id="speech-export-test",
            encrypted_artifact_ref=destination_ref,
            ciphertext_sha256=digest("ciphertext"),
            size_bytes=128,
        )


class _Blobs:
    def __init__(self, plaintext: bytes) -> None:
        self.plaintext = plaintext
        self.written = b""

    def read(self, record, *, maximum_bytes):
        del record
        assert len(self.plaintext) <= maximum_bytes
        return self.plaintext

    def write(self, record, artifact_ref, payload, *, media_type, export_consent):
        del record
        assert export_consent.session_epoch == 7
        assert artifact_ref.startswith("artifact://speech-adapter-exports/")
        assert media_type == "application/vnd.ananta.speech-adapter-export+json"
        self.written = payload
        return hashlib.sha256(payload).hexdigest(), len(payload)


class _Keys:
    value = b"k" * 32

    def key(self, **scope):
        assert set(scope) == {"tenant_id", "owner_subject", "pair_id"}
        return self.value


class _Consents:
    def verify(self, **scope):
        if (
            scope["export_consent_digest"] != digest("export-consent")
            or scope["export_consent_epoch"] != 7
        ):
            return None
        return SpeechAdapterExportConsentBinding(
            consent_id="export-consent-test",
            consent_digest=scope["export_consent_digest"],
            scope_digest=digest("export-consent-scope"),
            session_epoch=scope["export_consent_epoch"],
            consent_version=2,
            revocation_epoch=1,
            expires_at_ms=1_100_000,
        )


class _FailingRegistrationLineage:
    def publish_registration(self, _record) -> None:
        raise RuntimeError("lineage projection unavailable")

    def publish_export(self, _record, _receipt, *, export_consent_digest: str) -> None:
        del export_consent_digest


def _decision():
    job = speech_job()
    report = build_mock_evaluation(job)
    report["hardware_profile"] = "synthetic-openvoice-v2-contract-test"
    return MlInternSpeechEvalService().decide(report, expected_bindings=report["bindings"])


def _register(registry, adapter_id: str):
    pair_id = "pair-test"
    direction = "sender_to_receiver"
    speaker = digest("speaker")
    return registry.register_evaluated(
        adapter_id=adapter_id,
        version="v1",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id=pair_id,
        direction=direction,
        speaker_digest=speaker,
        scope_digest=speech_scope_digest(pair_id=pair_id, direction=direction, speaker_digest=speaker),
        base_model_id="openvoice-v2-test",
        base_model_digest=digest("model"),
        backend="mock",
        backend_digest=digest("mock-backend-v1"),
        dataset_digest=digest("dataset"),
        split_digest=digest("split"),
        evaluation=_decision(),
        consent_digest=digest("consent"),
        consent_expires_at_ms=1_200_000,
        artifact_ref=f"artifact://speech-adapters/test/{adapter_id}",
        artifact_sha256=digest(adapter_id),
        artifact_size_bytes=128,
        expires_at_ms=1_100_000,
    )


def _approve(registry, adapter_id: str, version: int = 1):
    return registry.approve(
        adapter_id,
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
        expected_version=version,
        authorized_confirmation=True,
        approved_by="admin-test",
        reason_code="manual_quality_approval",
        current_consent_digest=digest("consent"),
    )


def test_approval_is_explicit_pair_scoped_and_cas_bound(tmp_path) -> None:
    registry = MlInternSpeechAdapterRegistry(tmp_path / "registry.json", clock_ms=lambda: 1_000_000)
    evaluated = _register(registry, "adapter-test")
    assert evaluated.status == "evaluated"
    assert evaluated.registry_version == 1

    with pytest.raises(SpeechAdapterRegistryError) as captured:
        registry.approve(
            "adapter-test",
            tenant_id="tenant-test",
            owner_subject="owner-test",
            pair_id="pair-test",
            direction="sender_to_receiver",
            expected_version=1,
            authorized_confirmation=False,
            approved_by="admin-test",
            reason_code="manual_quality_approval",
            current_consent_digest=digest("consent"),
        )
    assert captured.value.reason_code == "speech_adapter_approval_confirmation_required"

    approved = _approve(registry, "adapter-test")
    assert approved.status == "approved"
    assert approved.registry_version == 2
    assert approved.approved_by_digest == digest("admin-test")
    with pytest.raises(SpeechAdapterNotFound):
        registry.get_for_pair(
            "adapter-test",
            tenant_id="tenant-test",
            owner_subject="owner-test",
            pair_id="foreign-pair",
            direction="sender_to_receiver",
        )


def test_registration_failure_keeps_atomic_registry_audit_and_recoverable_lineage(
    tmp_path,
) -> None:
    now_ms = 1_000_000
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: now_ms,
        ),
        secret=b"speech-adapter-registration-atomic-audit" * 2,
    )
    registry = MlInternSpeechAdapterRegistry(
        tmp_path / "registry.json",
        clock_ms=lambda: now_ms,
        export_lineage=_FailingRegistrationLineage(),
        authority_audit=audit,
    )

    with pytest.raises(SpeechAdapterRegistryError) as captured:
        _register(registry, "adapter-registration-recovery")
    assert captured.value.reason_code == "speech_adapter_registration_lineage_failed"

    with Session(engine) as session:
        adapter = session.get(MlInternSpeechAdapterDB, "adapter-registration-recovery")
        pending = session.exec(
            select(SpeechLineageOutboxDB).where(
                SpeechLineageOutboxDB.tenant_id == "tenant-test",
                SpeechLineageOutboxDB.owner_subject == "owner-test",
                SpeechLineageOutboxDB.state == "pending",
            )
        ).all()
        audit_rows = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.transition == "evaluated"
            )
        ).all()
    assert adapter is not None
    assert len(pending) == 1
    assert len(audit_rows) == 1
    assert SpeechEvidenceLineageRepository().recover_pending(
        tenant_id="tenant-test",
        owner_subject="owner-test",
    ) == 1
    graph = SpeechEvidenceLineageRepository().traverse(
        tenant_id="tenant-test",
        owner_subject="owner-test",
        root_kind="adapter",
        root_digest=digest("adapter-registration-recovery"),
        direction="backward",
    )
    assert {node["kind"] for node in graph.nodes} == {
        "manifest",
        "split",
        "model",
        "evaluation",
        "adapter",
    }

def test_revoke_is_idempotent_and_stale_other_transition_is_rejected(tmp_path) -> None:
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"speech-adapter-audit-test-key" * 2,
    )
    registry = MlInternSpeechAdapterRegistry(
        tmp_path / "registry.json",
        clock_ms=lambda: 1_000_000,
        authority_audit=audit,
    )
    _register(registry, "adapter-test")
    _approve(registry, "adapter-test")
    revoked = registry.change_status(
        "adapter-test",
        target="revoked",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
        expected_version=2,
        actor="admin-test",
        reason_code="consent_revoked",
    )
    replay = registry.change_status(
        "adapter-test",
        target="revoked",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
        expected_version=2,
        actor="admin-test",
        reason_code="consent_revoked",
    )
    assert replay.to_dict() == revoked.to_dict()
    with Session(engine) as session:
        rows = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.tenant_digest == audit.digest("tenant", "tenant-test"),
                SemanticMediaAuditOutboxDB.scope_digest
                == audit.digest("scope", "semantic-media-session:pair-test"),
            )
        ).all()
    assert len(rows) == 3
    assert {row.transition for row in rows} == {"evaluated", "approved", "revoked"}
    with pytest.raises(SpeechAdapterVersionConflict):
        registry.change_status(
            "adapter-test",
            target="expired",
            tenant_id="tenant-test",
            owner_subject="owner-test",
            pair_id="pair-test",
            direction="sender_to_receiver",
            expected_version=2,
            actor="admin-test",
            reason_code="retention_expired",
        )


def test_lineage_digest_fence_revokes_sql_backed_adapter_idempotently(tmp_path) -> None:
    registry = MlInternSpeechAdapterRegistry(tmp_path / "registry.json", clock_ms=lambda: 1_000_000)
    registered = _register(registry, "adapter-lineage-fence")
    _approve(registry, "adapter-lineage-fence")

    first = registry.fence_by_artifact_digest(
        tenant_id="tenant-test",
        owner_subject="owner-test",
        artifact_sha256=registered.artifact_sha256,
        revocation_epoch=3,
    )
    replay = registry.fence_by_artifact_digest(
        tenant_id="tenant-test",
        owner_subject="owner-test",
        artifact_sha256=registered.artifact_sha256,
        revocation_epoch=3,
    )
    current = registry.get_for_pair(
        "adapter-lineage-fence",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
    )

    assert first == replay == ("adapter-lineage-fence",)
    assert current.status == "revoked"
    assert current.lineage[-1]["revocation_epoch"] == 3
    with pytest.raises(SpeechAdapterRegistryError) as denied:
        registry.fence_by_artifact_digest(
            tenant_id="tenant-test",
            owner_subject="owner-test",
            artifact_sha256=registered.artifact_sha256,
            revocation_epoch=4,
            authority="worker",
        )
    assert denied.value.reason_code == "speech_adapter_hub_fence_required"


def test_export_requires_separate_consent_and_returns_only_encrypted_receipt(tmp_path) -> None:
    registry = MlInternSpeechAdapterRegistry(tmp_path / "registry.json", clock_ms=lambda: 1_000_000)
    _register(registry, "adapter-test")
    approved = _approve(registry, "adapter-test")
    with pytest.raises(SpeechAdapterRegistryError) as captured:
        registry.export_encrypted(
            "adapter-test",
            tenant_id="tenant-test",
            owner_subject="owner-test",
            pair_id="pair-test",
            direction="sender_to_receiver",
            expected_version=approved.registry_version,
            export_consent_digest=digest("export-consent"),
            export_consent_epoch=7,
            destination_ref="artifact://speech-adapter-exports/test/export",
            export_port=_ExportPort(consent=False),
        )
    assert captured.value.reason_code == "speech_adapter_export_consent_missing"

    receipt = registry.export_encrypted(
        "adapter-test",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
        expected_version=approved.registry_version,
        export_consent_digest=digest("export-consent"),
        export_consent_epoch=7,
        destination_ref="artifact://speech-adapter-exports/test/export",
        export_port=_ExportPort(),
    )
    assert receipt.encryption_scheme == "AES-256-GCM"
    assert not hasattr(receipt, "key")
    assert not hasattr(receipt, "server_path")
    lineage = get_ml_intern_speech_lineage_service().forward(
        VoicePrincipal("tenant-test", "owner-test"),
        root_kind="adapter",
        root_digest=digest("adapter-test"),
    )
    assert {node["kind"] for node in lineage.nodes} == {"adapter", "export", "receipt"}


def test_rollback_is_atomic_scoped_and_lineage_recorded(tmp_path) -> None:
    registry = MlInternSpeechAdapterRegistry(tmp_path / "registry.json", clock_ms=lambda: 1_000_000)
    _register(registry, "adapter-old")
    old = _approve(registry, "adapter-old")
    old = registry.change_status(
        "adapter-old",
        target="deprecated",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
        expected_version=old.registry_version,
        actor="admin-test",
        reason_code="superseded",
    )
    _register(registry, "adapter-new")
    new = _approve(registry, "adapter-new")
    restored = registry.rollback(
        from_adapter_id="adapter-new",
        to_adapter_id="adapter-old",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
        from_expected_version=new.registry_version,
        to_expected_version=old.registry_version,
        actor="admin-test",
        reason_code="quality_regression",
    )
    assert restored.status == "approved"
    assert restored.rollback_of_adapter_id == "adapter-new"
    assert restored.lineage[-1]["event"] == "rollback_to"
    current = registry.get_for_pair(
        "adapter-new",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id="pair-test",
        direction="sender_to_receiver",
    )
    assert current.status == "deprecated"


def test_concrete_export_encrypts_adapter_with_bound_aes_gcm_envelope(tmp_path) -> None:
    registry = MlInternSpeechAdapterRegistry(tmp_path / "registry.json", clock_ms=lambda: 1_000_000)
    _register(registry, "adapter-test")
    record = _approve(registry, "adapter-test")
    plaintext = b"private speech adapter bytes"
    record = replace(
        record,
        artifact_sha256=hashlib.sha256(plaintext).hexdigest(),
        artifact_size_bytes=len(plaintext),
    )
    blobs = _Blobs(plaintext)
    service = EncryptedSpeechAdapterExportService(blobs, _Keys(), _Consents())
    consent_digest = digest("export-consent")
    receipt = service.encrypt_export(
        record,
        destination_ref="artifact://speech-adapter-exports/test/export",
        export_consent_digest=consent_digest,
        export_consent_epoch=7,
    )
    assert receipt.ciphertext_sha256 == hashlib.sha256(blobs.written).hexdigest()
    envelope = json.loads(blobs.written)
    nonce = base64.b64decode(envelope["nonce"])
    associated = base64.b64decode(envelope["associated_data"])
    ciphertext = base64.b64decode(envelope["ciphertext"])
    assert AESGCM(_Keys.value).decrypt(nonce, ciphertext, associated) == plaintext
    assert b"private speech adapter bytes" not in blobs.written
