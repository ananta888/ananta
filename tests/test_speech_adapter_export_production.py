from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Flask
from sqlmodel import Session, select

from agent.bootstrap.semantic_media_services import initialize_semantic_media_services
from agent.database import engine
from agent.db_models import (
    SemanticMediaAuditOutboxDB,
    SpeechAdaptationArtifactDB,
    SpeechAdaptationJobDB,
    SpeechLineageOutboxDB,
)
from agent.repositories.speech_adaptation import SqlSpeechAdaptationArtifactRepository
from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository
from agent.services.ml_intern_speech_adapter_export import (
    ConfiguredPairExportKeyPort,
    EncryptedSpeechAdapterExportService,
    SpeechAdapterExportConfigurationError,
    SqlSpeechAdapterExportArtifactPort,
    SqlSpeechAdapterExportConsentPort,
    build_speech_adapter_export_service,
)
from agent.services.ml_intern_speech_adapter_registry import (
    MlInternSpeechAdapterRegistry,
    SpeechAdapterRegistryError,
)
from agent.services.ml_intern_speech_eval_service import SpeechEvaluationDecision
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import speech_scope_digest
from ananta_contracts.speech_evidence_governance import SPEECH_GRANTS, SpeechEvidenceConsent


def _digest(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode()
    return hashlib.sha256(raw).hexdigest()


def _key_file(tmp_path: Path) -> Path:
    path = tmp_path / "speech-adapter-export.key"
    path.write_bytes(b"x" * 32)
    path.chmod(0o600)
    return path


def _approved_registry(tmp_path: Path, *, adapter_id: str, artifact: bytes, now_ms: int):
    pair_id = "pair-export-test"
    direction = "sender_to_receiver"
    speaker_digest = _digest("speaker-export-test")
    training_consent_digest = _digest("training-consent")
    registry = MlInternSpeechAdapterRegistry(tmp_path / "legacy-registry.json", clock_ms=lambda: now_ms)
    record = registry.register_evaluated(
        adapter_id=adapter_id,
        version="v1",
        tenant_id="admin",
        owner_subject="admin",
        pair_id=pair_id,
        direction=direction,
        speaker_digest=speaker_digest,
        scope_digest=speech_scope_digest(
            pair_id=pair_id,
            direction=direction,
            speaker_digest=speaker_digest,
        ),
        base_model_id="openvoice-v2-test",
        base_model_digest=_digest("base-model"),
        backend="mock-contract-test",
        backend_digest=_digest("mock-contract-test"),
        dataset_digest=_digest("dataset"),
        split_digest=_digest("split"),
        evaluation=SpeechEvaluationDecision(
            report_digest=_digest("evaluation"),
            passed=True,
            approval_eligible=True,
            reason_codes=(),
            policy_version="speech-eval-policy.v1",
        ),
        consent_digest=training_consent_digest,
        consent_expires_at_ms=now_ms + 120_000,
        artifact_ref=f"artifact://speech-adapters/export-test/{adapter_id}",
        artifact_sha256=_digest(artifact),
        artifact_size_bytes=len(artifact),
        expires_at_ms=now_ms + 60_000,
    )
    approved = registry.approve(
        adapter_id,
        tenant_id="admin",
        owner_subject="admin",
        pair_id=pair_id,
        direction=direction,
        expected_version=record.registry_version,
        authorized_confirmation=True,
        approved_by="admin",
        reason_code="manual_export_test_approval",
        current_consent_digest=training_consent_digest,
    )
    return registry, approved


def _publish_source(root: Path, record) -> None:
    job_id = f"job-{record.adapter_id}"
    attempt_id = f"attempt-{record.adapter_id}"
    with Session(engine) as session:
        session.add(
            SpeechAdaptationJobDB(
                id=job_id,
                tenant_id=record.tenant_id,
                owner_subject=record.owner_subject,
                task_id=f"task-{record.adapter_id}",
                idempotency_digest=_digest(f"idempotency-{record.adapter_id}"),
                request_digest=_digest(f"request-{record.adapter_id}"),
                status="completed",
                reason_code="completed",
            )
        )
        session.flush()
        session.add(
            SpeechAdaptationArtifactDB(
                id=record.adapter_id,
                tenant_id=record.tenant_id,
                owner_subject=record.owner_subject,
                job_id=job_id,
                attempt_id=attempt_id,
                artifact_ref=record.artifact_ref,
                sha256=record.artifact_sha256,
                size_bytes=record.artifact_size_bytes,
                media_type="application/vnd.ananta.speech-adapter",
                storage_ref=(
                    f"hub-artifact://speech-adaptation/{job_id}/{attempt_id}/{record.artifact_sha256}"
                ),
                state="committed",
            )
        )
        session.commit()
    path = root / job_id / attempt_id / record.artifact_sha256
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_bytes(b"receiver-local adapter fixture")
    path.chmod(0o600)


def _export_consent_payload(record, *, now_ms: int, session_epoch: int = 7) -> dict[str, object]:
    grants = {name: False for name in SPEECH_GRANTS}
    grants["export"] = True
    raw = {
        "schema": "ananta.speech-evidence-consent.v1",
        "consent_id": f"consent-{record.adapter_id}",
        "tenant_id": record.tenant_id,
        "owner_subject": record.owner_subject,
        "speaker_id": record.speaker_digest,
        "recipient_id": record.owner_subject,
        "direction": record.direction,
        "pair_id": record.pair_id,
        "session_id": record.adapter_id,
        "session_epoch": session_epoch,
        "purpose": "speech_adapter_export",
        "data_classes": ["speaker_embedding"],
        "retention_seconds": 600,
        "trainer_locations": [],
        "grants": grants,
        "consent_version": 1,
        "revocation_epoch": 0,
        "issued_at_ms": now_ms - 1_000,
        "expires_at_ms": now_ms + 60_000,
        "state": "active",
        "required_signers": [record.owner_subject, record.speaker_digest],
        "signatures": {
            record.owner_subject: _digest("owner-signature"),
            record.speaker_digest: _digest("speaker-signature"),
        },
    }
    return raw


def _export_consent(record, *, now_ms: int, session_epoch: int = 7) -> SpeechEvidenceConsent:
    """Grant through the production Hub consent authority, never DB injection."""

    return SpeechEvidenceConsentService(clock_ms=lambda: now_ms).grant(
        VoicePrincipal(record.tenant_id, record.owner_subject),
        _export_consent_payload(record, now_ms=now_ms, session_epoch=session_epoch),
    )


def test_bootstrap_binds_export_only_with_secure_operator_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent.bootstrap.semantic_media_services.resolve_semantic_media_feature_flags",
        lambda _source: {
            "peer_evidence_sync": False,
            "speech_adaptation_training": False,
            "speech_adapter_routing": True,
        },
    )
    monkeypatch.delenv("ANANTA_SPEECH_ADAPTER_EXPORT_KEY_FILE", raising=False)
    app = Flask("export-bootstrap-missing")
    app.secret_key = "bootstrap-test-secret" * 3
    initialize_semantic_media_services(app)
    assert "speech_adapter_export_port" not in app.extensions
    assert app.extensions["speech_adapter_export_status"] == {
        "ready": False,
        "reason_code": "speech_adapter_export_key_missing",
    }

    key_file = _key_file(tmp_path)
    monkeypatch.setenv("ANANTA_SPEECH_ADAPTER_EXPORT_KEY_FILE", str(key_file))
    monkeypatch.setenv("ANANTA_SPEECH_TRAINING_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    ready = Flask("export-bootstrap-ready")
    ready.secret_key = "bootstrap-test-secret" * 3
    initialize_semantic_media_services(ready)
    assert isinstance(ready.extensions["speech_adapter_export_port"], EncryptedSpeechAdapterExportService)
    assert ready.extensions["speech_adapter_export_status"] == {"ready": True, "reason_code": None}

    with pytest.raises(SpeechAdapterExportConfigurationError) as missing_audit:
        build_speech_adapter_export_service(
            {
                "ANANTA_SPEECH_ADAPTER_EXPORT_KEY_FILE": str(key_file),
                "ANANTA_SPEECH_TRAINING_ARTIFACT_ROOT": str(tmp_path / "without-audit"),
            }
        )
    assert missing_audit.value.reason_code == "speech_adapter_export_audit_unavailable"


def test_export_route_uses_persistent_consent_and_artifact_sot_without_leaking_paths_or_keys(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    now_ms = time.time_ns() // 1_000_000
    adapter_id = "speech-adapter-export-route"
    plaintext = b"receiver-local adapter fixture"
    registry, record = _approved_registry(tmp_path, adapter_id=adapter_id, artifact=plaintext, now_ms=now_ms)
    root = tmp_path / "speech-artifacts"
    _publish_source(root, record)
    key_port = ConfiguredPairExportKeyPort.from_file(str(_key_file(tmp_path)))
    export_service = EncryptedSpeechAdapterExportService(
        artifacts=SqlSpeechAdapterExportArtifactPort(
            SqlSpeechAdaptationArtifactRepository(root),
            audit=app.extensions["semantic_media_audit_recorder"],
        ),
        keys=key_port,
        consents=SqlSpeechAdapterExportConsentPort(clock_ms=lambda: now_ms),
    )
    app.extensions["ml_intern_speech_adapter_registry"] = registry
    app.extensions["speech_adapter_export_port"] = export_service
    destination_ref = f"artifact://speech-adapter-exports/{record.pair_id}/{adapter_id}"
    body = {
        "pair_id": record.pair_id,
        "direction": record.direction,
        "expected_version": record.registry_version,
        "confirmed": True,
        "export_consent_digest": _digest("not-a-real-export-consent"),
        "export_consent_epoch": 7,
        "destination_ref": destination_ref,
    }

    denied = client.post(
        f"/api/ml-intern-speech-adapters/{adapter_id}/export",
        headers=admin_auth_header,
        json=body,
    )
    assert denied.status_code == 422
    assert denied.get_json()["data"]["error"]["code"] == "speech_adapter_export_consent_missing"

    grant = client.post(
        "/v1/voice/speech-evidence-consents",
        headers={**admin_auth_header, "Idempotency-Key": f"export-consent-{adapter_id}"},
        json=_export_consent_payload(record, now_ms=now_ms),
    )
    assert grant.status_code == 201
    consent = SpeechEvidenceConsent.from_mapping(grant.get_json()["data"]["consent"], now_ms=now_ms)

    stale = client.post(
        f"/api/ml-intern-speech-adapters/{adapter_id}/export",
        headers=admin_auth_header,
        json={
            **body,
            "export_consent_digest": consent.consent_digest,
            "export_consent_epoch": 6,
        },
    )
    assert stale.status_code == 422
    assert stale.get_json()["data"]["error"]["code"] == "speech_adapter_export_consent_missing"

    response = client.post(
        f"/api/ml-intern-speech-adapters/{adapter_id}/export",
        headers=admin_auth_header,
        json={**body, "export_consent_digest": consent.consent_digest},
    )
    assert response.status_code == 200
    public = response.get_json()["data"]
    assert set(public) == {
        "export_id",
        "encrypted_artifact_ref",
        "ciphertext_sha256",
        "size_bytes",
        "encryption_scheme",
    }
    assert public["encrypted_artifact_ref"] == destination_ref
    serialized = json.dumps(public).casefold()
    assert "server_path" not in serialized
    assert "storage_ref" not in serialized
    assert "key" not in serialized
    assert record.artifact_ref not in serialized

    with Session(engine) as session:
        exported = session.exec(
            select(SpeechAdaptationArtifactDB).where(
                SpeechAdaptationArtifactDB.id == public["export_id"],
                SpeechAdaptationArtifactDB.artifact_ref == destination_ref,
                SpeechAdaptationArtifactDB.state == "committed",
            )
        ).one()
        lineage_outbox = session.exec(
            select(SpeechLineageOutboxDB).where(
                SpeechLineageOutboxDB.tenant_id == record.tenant_id,
                SpeechLineageOutboxDB.owner_subject == record.owner_subject,
            )
        ).all()
        audit_outbox = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.transition == "exported",
            )
        ).all()
    assert lineage_outbox and all(row.state == "published" for row in lineage_outbox)
    assert len(audit_outbox) == 1
    assert audit_outbox[0].epoch == 7
    assert audit_outbox[0].contract_ref == app.extensions[
        "semantic_media_audit_recorder"
    ].digest("contract", consent.consent_digest)
    envelope_bytes = (root / "exports" / exported.sha256).read_bytes()
    envelope = json.loads(envelope_bytes)
    associated = base64.b64decode(envelope["associated_data"])
    decrypted = AESGCM(
        key_port.key(
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            pair_id=record.pair_id,
        )
    ).decrypt(
        base64.b64decode(envelope["nonce"]),
        base64.b64decode(envelope["ciphertext"]),
        associated,
    )
    assert decrypted == plaintext
    assert plaintext not in envelope_bytes


def test_export_consent_is_adapter_specific_current_and_separate(tmp_path: Path) -> None:
    now_ms = time.time_ns() // 1_000_000
    plaintext = b"receiver-local adapter fixture"
    _registry, record = _approved_registry(
        tmp_path,
        adapter_id="speech-adapter-consent-scope",
        artifact=plaintext,
        now_ms=now_ms,
    )
    consent = _export_consent(record, now_ms=now_ms)
    port = SqlSpeechAdapterExportConsentPort(clock_ms=lambda: now_ms)
    values = {
        "tenant_id": record.tenant_id,
        "owner_subject": record.owner_subject,
        "pair_id": record.pair_id,
        "direction": record.direction,
        "adapter_id": record.adapter_id,
        "speaker_digest": record.speaker_digest,
        "export_consent_digest": consent.consent_digest,
        "export_consent_epoch": 7,
    }
    binding = port.verify(**values)
    assert binding is not None
    assert binding.session_epoch == 7
    assert port.verify(**{**values, "export_consent_epoch": 6}) is None
    assert port.verify(**{**values, "adapter_id": "speech-adapter-other"}) is None
    assert port.verify(**{**values, "pair_id": "pair-other"}) is None
    assert port.verify(**{**values, "export_consent_digest": record.consent_digest}) is None


def test_adapter_read_routes_are_closed_and_isolate_owner_and_pair(
    app,
    client,
    admin_auth_header,
    user_auth_header,
    tmp_path: Path,
) -> None:
    now_ms = time.time_ns() // 1_000_000
    registry, record = _approved_registry(
        tmp_path,
        adapter_id="speech-adapter-read-isolation",
        artifact=b"receiver-local adapter fixture",
        now_ms=now_ms,
    )
    app.extensions["ml_intern_speech_adapter_registry"] = registry
    endpoint = "/api/ml-intern-speech-adapters"

    owner_page = client.get(
        f"{endpoint}?pair_id={record.pair_id}&direction={record.direction}",
        headers=admin_auth_header,
    )
    assert owner_page.status_code == 200
    assert owner_page.get_json()["data"]["count"] == 1

    foreign_owner = client.get(
        f"{endpoint}?pair_id={record.pair_id}&direction={record.direction}",
        headers=user_auth_header,
    )
    assert foreign_owner.status_code == 200
    assert foreign_owner.get_json()["data"] == {"items": [], "count": 0}

    foreign_pair = client.get(
        f"{endpoint}/{record.adapter_id}?pair_id=pair-foreign&direction={record.direction}",
        headers=admin_auth_header,
    )
    assert foreign_pair.status_code == 404
    assert foreign_pair.get_json()["data"]["error"]["code"] == "speech_adapter_not_found"

    unknown = client.get(
        f"{endpoint}?pair_id={record.pair_id}&direction={record.direction}&server_path=/srv/private",
        headers=admin_auth_header,
    )
    assert unknown.status_code == 422
    assert unknown.get_json()["data"]["error"]["code"] == "speech_adapter_pair_scope_shape_invalid"


class _FailingExportLineage:
    def publish_registration(self, _record) -> None:
        return None

    def publish_export(self, _record, _receipt, *, export_consent_digest: str) -> None:
        del export_consent_digest
        raise RuntimeError("lineage materializer unavailable")


def test_export_receipt_keeps_atomic_recoverable_lineage_and_audit_when_materialization_fails(
    tmp_path: Path,
) -> None:
    now_ms = time.time_ns() // 1_000_000
    adapter_id = "speech-adapter-export-recovery"
    plaintext = b"receiver-local adapter fixture"
    _initial, _record = _approved_registry(tmp_path, adapter_id=adapter_id, artifact=plaintext, now_ms=now_ms)
    # Re-open the same SQL authority with a deliberately failing materializer.
    registry = MlInternSpeechAdapterRegistry(
        tmp_path / "legacy-recovery.json",
        clock_ms=lambda: now_ms,
        export_lineage=_FailingExportLineage(),
    )
    record = registry.get_for_pair(
        adapter_id,
        tenant_id="admin",
        owner_subject="admin",
        pair_id="pair-export-test",
        direction="sender_to_receiver",
    )
    root = tmp_path / "speech-artifacts-recovery"
    _publish_source(root, record)
    consent = _export_consent(record, now_ms=now_ms)
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(InMemorySemanticMediaAuditRepository(), clock_ms=lambda: now_ms),
        secret=b"speech-export-atomic-audit-secret" * 2,
    )
    export = EncryptedSpeechAdapterExportService(
        artifacts=SqlSpeechAdapterExportArtifactPort(
            SqlSpeechAdaptationArtifactRepository(root),
            audit=audit,
        ),
        keys=ConfiguredPairExportKeyPort(b"r" * 32),
        consents=SqlSpeechAdapterExportConsentPort(clock_ms=lambda: now_ms),
    )

    with pytest.raises(SpeechAdapterRegistryError) as captured:
        registry.export_encrypted(
            adapter_id,
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            pair_id=record.pair_id,
            direction=record.direction,
            expected_version=record.registry_version,
            export_consent_digest=consent.consent_digest,
            export_consent_epoch=7,
            destination_ref=f"artifact://speech-adapter-exports/{record.pair_id}/{adapter_id}",
            export_port=export,
        )
    assert captured.value.reason_code == "speech_adapter_export_lineage_failed"

    with Session(engine) as session:
        stored = session.exec(
            select(SpeechAdaptationArtifactDB).where(
                SpeechAdaptationArtifactDB.artifact_ref
                == f"artifact://speech-adapter-exports/{record.pair_id}/{adapter_id}"
            )
        ).one()
        pending = session.exec(
            select(SpeechLineageOutboxDB).where(
                SpeechLineageOutboxDB.tenant_id == record.tenant_id,
                SpeechLineageOutboxDB.owner_subject == record.owner_subject,
                SpeechLineageOutboxDB.state == "pending",
            )
        ).all()
        audit_rows = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.transition == "exported",
            )
        ).all()
    assert stored.state == "committed"
    assert len(pending) == 1
    assert len(audit_rows) == 1
    assert SpeechEvidenceLineageRepository().recover_pending(
        tenant_id=record.tenant_id,
        owner_subject=record.owner_subject,
    ) == 1
    graph = SpeechEvidenceLineageRepository().traverse(
        tenant_id=record.tenant_id,
        owner_subject=record.owner_subject,
        root_kind="adapter",
        root_digest=record.artifact_sha256,
        direction="forward",
    )
    assert {node["kind"] for node in graph.nodes} == {"adapter", "export", "receipt"}


def test_export_sql_failure_rolls_back_receipt_outboxes_and_new_cas_object(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now_ms = time.time_ns() // 1_000_000
    adapter_id = "speech-adapter-export-sql-rollback"
    plaintext = b"receiver-local adapter fixture"
    _registry, record = _approved_registry(
        tmp_path,
        adapter_id=adapter_id,
        artifact=plaintext,
        now_ms=now_ms,
    )
    root = tmp_path / "speech-artifacts-sql-rollback"
    _publish_source(root, record)
    consent = _export_consent(record, now_ms=now_ms)
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: now_ms,
        ),
        secret=b"speech-export-sql-rollback-audit-secret" * 2,
    )
    export = EncryptedSpeechAdapterExportService(
        artifacts=SqlSpeechAdapterExportArtifactPort(
            SqlSpeechAdaptationArtifactRepository(root),
            audit=audit,
        ),
        keys=ConfiguredPairExportKeyPort(b"z" * 32),
        consents=SqlSpeechAdapterExportConsentPort(clock_ms=lambda: now_ms),
    )

    def fail_stage(*_args, **_kwargs):
        raise RuntimeError("lineage outbox unavailable")

    monkeypatch.setattr(SpeechEvidenceLineageRepository, "stage", fail_stage)
    with pytest.raises(RuntimeError, match="lineage outbox unavailable"):
        export.encrypt_export(
            record,
            destination_ref=f"artifact://speech-adapter-exports/{record.pair_id}/{adapter_id}",
            export_consent_digest=consent.consent_digest,
            export_consent_epoch=7,
        )

    export_root = root / "exports"
    assert not export_root.exists() or list(export_root.iterdir()) == []
    with Session(engine) as session:
        receipts = session.exec(
            select(SpeechAdaptationArtifactDB).where(
                SpeechAdaptationArtifactDB.tenant_id == record.tenant_id,
                SpeechAdaptationArtifactDB.owner_subject == record.owner_subject,
                SpeechAdaptationArtifactDB.media_type
                == "application/vnd.ananta.speech-adapter-export+json",
            )
        ).all()
        audit_rows = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.transition == "exported"
            )
        ).all()
    assert receipts == []
    assert audit_rows == []


@pytest.mark.parametrize(
    ("race", "reason_code"),
    (
        ("adapter", "speech_export_adapter_fence_changed"),
        ("consent", "speech_export_consent_fence_changed"),
    ),
)
def test_export_final_transaction_revalidates_adapter_and_consent_fences(
    race: str,
    reason_code: str,
    tmp_path: Path,
) -> None:
    now_ms = time.time_ns() // 1_000_000
    adapter_id = f"speech-adapter-export-{race}-race"
    plaintext = b"receiver-local adapter fixture"
    registry, record = _approved_registry(
        tmp_path,
        adapter_id=adapter_id,
        artifact=plaintext,
        now_ms=now_ms,
    )
    root = tmp_path / f"speech-artifacts-{race}-race"
    _publish_source(root, record)
    consent = _export_consent(record, now_ms=now_ms)
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(
            InMemorySemanticMediaAuditRepository(),
            clock_ms=lambda: now_ms,
        ),
        secret=b"speech-export-final-fence-audit-secret" * 2,
    )

    class RacingKeyPort:
        def key(self, **_scope):
            if race == "adapter":
                registry.change_status(
                    record.adapter_id,
                    target="revoked",
                    tenant_id=record.tenant_id,
                    owner_subject=record.owner_subject,
                    pair_id=record.pair_id,
                    direction=record.direction,
                    expected_version=record.registry_version,
                    actor="hub-race-test",
                    reason_code="consent_revoked_during_export",
                )
            else:
                SpeechEvidenceConsentService(clock_ms=lambda: now_ms).revoke(
                    VoicePrincipal(record.tenant_id, record.owner_subject),
                    consent.consent_id,
                    expected_version=consent.consent_version,
                )
            return b"f" * 32

    export = EncryptedSpeechAdapterExportService(
        artifacts=SqlSpeechAdapterExportArtifactPort(
            SqlSpeechAdaptationArtifactRepository(root),
            audit=audit,
        ),
        keys=RacingKeyPort(),
        consents=SqlSpeechAdapterExportConsentPort(clock_ms=lambda: now_ms),
    )

    with pytest.raises(RuntimeError, match=reason_code):
        export.encrypt_export(
            record,
            destination_ref=f"artifact://speech-adapter-exports/{record.pair_id}/{adapter_id}",
            export_consent_digest=consent.consent_digest,
            export_consent_epoch=7,
        )

    export_root = root / "exports"
    assert not export_root.exists() or list(export_root.iterdir()) == []
    with Session(engine) as session:
        receipts = session.exec(
            select(SpeechAdaptationArtifactDB).where(
                SpeechAdaptationArtifactDB.media_type
                == "application/vnd.ananta.speech-adapter-export+json"
            )
        ).all()
        audit_rows = session.exec(
            select(SemanticMediaAuditOutboxDB).where(
                SemanticMediaAuditOutboxDB.transition == "exported"
            )
        ).all()
    assert receipts == []
    assert audit_rows == []
