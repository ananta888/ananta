"""Productive Hub composition for peer speech-evidence curation.

The browser may request curation and disclose the already acknowledged clear
chunks, but it cannot decide admission or dataset membership.  This service
rebinds every chunk to the signed transfer commitments, encrypts the aggregate
immediately, runs Hub policies, emits a Hub-signed receipt and delegates one
bounded child task.  Dataset publication happens only after an authenticated
Worker result passes the current consent/revocation fence.

The Hub-owned application service below orchestrates the flow.  Its narrow
collaborators live in dedicated modules and are injected with production
defaults: value types and parsers (``speech_peer_curation_values``), port
adapters and read projection (``speech_peer_curation_adapters``), clear-group
verification (``speech_peer_curation_group_decoder``), Worker-artifact staging
and dataset materialisation (``speech_peer_curation_dataset``) and the
transitive revocation fence (``speech_peer_curation_revocation``).  Previously
importable names remain re-exported here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Callable, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechEvidenceConsentDB,
    SpeechPeerCurationArtifactDB,
    SpeechPeerEvidenceCurationDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence import (
    SpeechEvidenceRepository,
    get_speech_evidence_repository,
)
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    get_speech_evidence_lineage_repository,
)
from agent.repositories.speech_evidence_sync import SpeechEvidenceTransferCurationBinding
from agent.services.ml_intern_speech_dataset_build_service import (
    HubSpeechDatasetEvidenceFence,
    MlInternSpeechDatasetBuildService,
)
from agent.services.ml_intern_speech_lineage_service import (
    MlInternSpeechLineageService,
    get_ml_intern_speech_lineage_service,
)
from agent.services.ml_intern_speech_revocation_service import MlInternSpeechRevocationService
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_evidence_admission_policy import SpeechEvidenceAdmissionPolicy
from agent.services.speech_evidence_consent_service import (
    SpeechEvidenceConsentService,
    get_speech_evidence_consent_service,
)
from agent.services.speech_evidence_curation_task_service import SpeechEvidenceCurationTaskService
from agent.services.speech_evidence_encryption_port import (
    SpeechEvidenceEncryptionPort,
    get_speech_evidence_encryption_port,
)
from agent.services.speech_evidence_offer_service import SpeechEvidenceOfferRecord
from agent.services.speech_evidence_poisoning_policy import SpeechEvidencePoisoningPolicy
from agent.services.speech_evidence_receipt_service import SpeechEvidenceReceiptService
from agent.services.speech_evidence_store_service import (
    SpeechEvidenceStoreService,
    get_speech_evidence_store_service,
)
from agent.services.speech_evidence_sync_composition import HubSpeechEvidenceSyncService
from agent.services.speech_peer_curation_adapters import (
    HubLocalSpeechDatasetPublisher,
    HubPeerAdmissionAuthority,
    SpeechPeerCurationAuditPreparer,
    SqlSpeechPeerCurationProjection,
    StagedSpeechCurationResultPort,
)
from agent.services.speech_peer_curation_dataset import (
    SpeechPeerCurationArtifactStager,
    SpeechPeerCurationDatasetMaterializer,
)
from agent.services.speech_peer_curation_group_decoder import SpeechPeerCurationGroupDecoder
from agent.services.speech_peer_curation_revocation import SpeechPeerCurationRevocationFence
from agent.services.speech_peer_curation_values import (
    INPUT_SCHEMA,
    MAX_AGGREGATE_BYTES,
    SpeechPeerCurationError,
    SpeechPeerCurationGroupInput,
    SpeechPeerCurationRecord,
    canonical_sha256,
    curation_artifact,
    joined_digest,
    peer_dataset_id,
    require_identifier,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SpeechCurationWorkerResult,
    canonical_json,
)
from ananta_contracts.speech_evidence_sync import VerifiedSpeechEvidenceMessage
from voice_runtime.evidence_identity import SpeechEvidenceIdentityService


class SpeechPeerEvidenceCurationService:
    """Hub-owned application service spanning quarantine, curation and dataset publication."""

    def __init__(
        self,
        *,
        sync: HubSpeechEvidenceSyncService,
        store: SpeechEvidenceStoreService,
        admission: SpeechEvidenceAdmissionPolicy,
        poisoning: SpeechEvidencePoisoningPolicy,
        receipts: SpeechEvidenceReceiptService,
        curation_tasks: SpeechEvidenceCurationTaskService,
        datasets: MlInternSpeechDatasetBuildService,
        authority: HubPeerAdmissionAuthority,
        identity: SpeechEvidenceIdentityService,
        evidence: SpeechEvidenceRepository | None = None,
        consent: SpeechEvidenceConsentService | None = None,
        encryption: SpeechEvidenceEncryptionPort | None = None,
        lineage: MlInternSpeechLineageService | None = None,
        lineage_repository: SpeechEvidenceLineageRepository | None = None,
        training_revocation: MlInternSpeechRevocationService | None = None,
        audit: SemanticMediaAuditPort | None = None,
        clock_ms: Callable[[], int] | None = None,
        group_decoder: SpeechPeerCurationGroupDecoder | None = None,
        projection: SqlSpeechPeerCurationProjection | None = None,
        audit_events: SpeechPeerCurationAuditPreparer | None = None,
        artifact_stager: SpeechPeerCurationArtifactStager | None = None,
        dataset_materializer: SpeechPeerCurationDatasetMaterializer | None = None,
        revocation_fence: SpeechPeerCurationRevocationFence | None = None,
    ) -> None:
        self._sync = sync
        self._store = store
        self._admission = admission
        self._poisoning = poisoning
        self._receipts = receipts
        self._tasks = curation_tasks
        self._datasets = datasets
        self._authority = authority
        self._identity = identity
        self._evidence = evidence or get_speech_evidence_repository()
        self._consent = consent or get_speech_evidence_consent_service()
        self._encryption = encryption or get_speech_evidence_encryption_port()
        self._lineage_service = lineage or get_ml_intern_speech_lineage_service()
        self._lineage_repository = lineage_repository or get_speech_evidence_lineage_repository()
        self._training_revocation = training_revocation or MlInternSpeechRevocationService()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._group_decoder = group_decoder or SpeechPeerCurationGroupDecoder()
        self._projection = projection or SqlSpeechPeerCurationProjection()
        self._audit_events = audit_events or SpeechPeerCurationAuditPreparer(audit)
        self._artifacts = artifact_stager or SpeechPeerCurationArtifactStager(clock_ms=self._clock_ms)
        self._dataset_materializer = dataset_materializer or SpeechPeerCurationDatasetMaterializer(
            datasets=self._datasets,
            clock_ms=self._clock_ms,
        )
        self._revocation = revocation_fence or SpeechPeerCurationRevocationFence(
            curation_tasks=self._tasks,
            evidence=self._evidence,
            encryption=self._encryption,
            lineage=self._lineage_service,
            lineage_repository=self._lineage_repository,
            training_revocation=self._training_revocation,
            audit=self._audit_events,
            clock_ms=self._clock_ms,
        )

    def request(
        self,
        principal: VoicePrincipal,
        *,
        signed_message: Mapping[str, Any] | bytes,
        groups: Sequence[object],
    ) -> tuple[SpeechPeerCurationRecord, bool]:
        message, offer, bindings = self._sync.authorize_curation_request(principal, signed_message)
        with self._sync.curation_offer_guard(principal, offer):
            return self._request_authorized(
                principal,
                message=message,
                offer=offer,
                bindings=bindings,
                groups=groups,
            )

    def _request_authorized(
        self,
        principal: VoicePrincipal,
        *,
        message: VerifiedSpeechEvidenceMessage,
        offer: SpeechEvidenceOfferRecord,
        bindings: tuple[SpeechEvidenceTransferCurationBinding, ...],
        groups: Sequence[object],
    ) -> tuple[SpeechPeerCurationRecord, bool]:
        supplied = tuple(SpeechPeerCurationGroupInput.from_mapping(value) for value in groups)
        if len(supplied) != len(bindings) or tuple(sorted(value.group_id for value in supplied)) != tuple(
            sorted(value.group_id for value in bindings)
        ):
            raise SpeechPeerCurationError("speech_peer_curation_group_binding_mismatch", status_code=409)
        existing = self._projection.get(principal, offer.offer_id)
        if existing is not None:
            return self._repair_task(principal, existing), False

        consent = self._current_recipient_consent(principal, offer)
        decoded = self._group_decoder.decode(
            supplied,
            bindings,
            offer=offer,
            speaker_id=consent.speaker_id,
        )
        source_binding_digest = canonical_sha256(
            {
                "domain": "ananta.peer-speech-source-binding.v2",
                "offer_id": offer.offer_id,
                "offer_group_preview_digest": offer.group_preview_digest,
                "request_verification_digest": message.verification_digest,
                "groups": [
                    {
                        "group_id": group_id,
                        "content_digest": content_digest,
                        "source_digest": payload["source_digest"],
                        "signed_preview": binding.preview.public_dict(),
                    }
                    for (group_id, _body, payload, content_digest), binding in zip(
                        decoded,
                        sorted(bindings, key=lambda value: value.group_id),
                        strict=True,
                    )
                ],
            }
        )
        aggregate = canonical_json(
            {
                "schema": INPUT_SCHEMA,
                "offer_id": offer.offer_id,
                "source_binding_digest": source_binding_digest,
                "groups": [payload for _group_id, _body, payload, _digest_value in decoded],
            }
        )
        if len(aggregate) > MAX_AGGREGATE_BYTES:
            raise SpeechPeerCurationError("speech_peer_curation_payload_too_large", status_code=413)
        data_sender = bindings[0].sender_id
        contributor_digest = hashlib.sha256(f"peer\0{data_sender}".encode()).hexdigest()
        aggregate_digest = hashlib.sha256(aggregate).hexdigest()
        source_digest = canonical_sha256(
            [
                {"group_id": group_id, "source_digest": payload["source_digest"], "content_digest": digest}
                for group_id, _body, payload, digest in decoded
            ]
        )
        duration_ms = min(3_600_000, max(1, len(decoded) * 1_000))
        identity = self._identity.identify(
            pair_id=offer.pair_id,
            session_id=offer.session_id,
            session_epoch=offer.epoch,
            speaker_scope=consent.speaker_id,
            capture_segment_id=f"peer-offer-{hashlib.sha256(offer.offer_id.encode()).hexdigest()[:32]}",
            start_ms=0,
            end_ms=duration_ms,
            source_digest=source_digest,
            revision=1,
            revision_digest=aggregate_digest,
        )
        evidence_class = (
            "correction"
            if "correction" in offer.data_classes or "text_corrections" in offer.data_classes
            else "transcript"
        )
        evidence, _created = self._store.store(
            principal,
            aggregate,
            claimed_content_digest=aggregate_digest,
            provenance_digest=source_binding_digest,
            identity=identity,
            evidence_class=evidence_class,
            data_class=evidence_class,
            grant="transcript_share",
            consent_id=consent.consent_id,
            consent_version=consent.consent_version,
            revocation_epoch=consent.revocation_epoch,
            consent_digest=consent.consent_digest,
            speaker_id=consent.speaker_id,
            recipient_id=consent.recipient_id,
            direction=consent.direction,
            pair_id=offer.pair_id,
            session_id=offer.session_id,
            session_epoch=offer.epoch,
            purpose=consent.purpose,
            retention_seconds=min(offer.retention_seconds, consent.retention_seconds),
        )
        poison = self._poisoning.evaluate(
            tuple(
                self._group_decoder.risk_signal(
                    group_id=group_id,
                    payload=payload,
                    content_digest=content_digest,
                    contributor_digest=contributor_digest,
                    consent_digest=consent.consent_digest,
                )
                for group_id, _body, payload, content_digest in decoded
            )
        )
        authority_digest = canonical_sha256(
            {
                "content_digest": evidence.content_digest,
                "provenance_digest": evidence.provenance_digest,
                "source_digest": evidence.source_digest,
                "speaker_scope_digest": evidence.speaker_scope_digest,
                "transcript_authority": "hub_fusion_verified",
            }
        )
        decision = self._admission.admit(
            principal,
            evidence.evidence_id,
            peer_id=data_sender,
            speaker_id=consent.speaker_id,
            recipient_id=consent.recipient_id,
            direction=consent.direction,
            data_class=evidence_class,
            purpose=consent.purpose,
            evidence_signature=self._authority.sign(authority_digest),
            provenance_digest=evidence.provenance_digest,
            source_digest=evidence.source_digest,
            speaker_scope_digest=evidence.speaker_scope_digest,
            transcript_authority="hub_fusion_verified",
            quality_metrics={
                "duration_ms": duration_ms,
                "snr_db": 20.0,
                "clipping_ratio": 0.0,
                "silence_ratio": 0.0,
            },
            external_quarantine_reasons=poison.reason_codes if poison.state == "quarantine" else (),
            external_reject_reasons=poison.reason_codes if poison.state == "reject" else (),
        )
        accepted = offer.group_ids if decision.decision == "admitted" else ()
        rejected = offer.group_ids if decision.decision == "rejected" else ()
        quarantined = offer.group_ids if decision.decision == "quarantined" else ()
        policy_digest = canonical_sha256(
            {
                "admission_policy": decision.policy_version,
                "poisoning_decision_digest": poison.decision_digest,
            }
        )
        receipt = self._receipts.issue(
            admission_digest=decision.admission_digest,
            offer_id=offer.offer_id,
            inventory_root_digest=offer.inventory_root_digest,
            resolution_digest=poison.decision_digest,
            accepted_group_ids=tuple(accepted),
            rejected_group_ids=tuple(rejected),
            quarantined_group_ids=tuple(quarantined),
            consent_digest=consent.consent_digest,
            policy_digest=policy_digest,
            pair_id=offer.pair_id,
            direction=offer.direction,
        )
        task_id: str | None = None
        if decision.decision == "admitted":
            task, _task_created = self._tasks.create(principal, admission_digest=decision.admission_digest)
            task_id = task.task_id
        now = self._clock_ms()
        dataset_id = peer_dataset_id(principal, offer.pair_id)
        row = SpeechPeerEvidenceCurationDB(
            id=f"speech-peer-curation-{joined_digest(principal.tenant_id, principal.subject, offer.offer_id)[:32]}",
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            offer_id=offer.offer_id,
            pair_id=offer.pair_id,
            session_id=offer.session_id,
            session_epoch=offer.epoch,
            evidence_id=evidence.evidence_id,
            admission_digest=decision.admission_digest,
            source_binding_digest=source_binding_digest,
            contributor_digest=contributor_digest,
            data_class=evidence_class,
            direction=offer.direction,
            receipt_payload=receipt.public_dict(),
            curation_task_id=task_id,
            consent_id=consent.consent_id,
            consent_version=consent.consent_version,
            revocation_epoch=consent.revocation_epoch,
            dataset_id=dataset_id,
            state=decision.decision,
            created_at_ms=now,
            updated_at_ms=now,
        )
        audit_event = self._audit_events.prepare(
            principal,
            session_id=offer.session_id,
            epoch=offer.epoch,
            transition=f"curation_{decision.decision}",
            reason_code=f"hub_{decision.decision}",
            idempotency_key=f"speech-curation:create:{source_binding_digest}",
            authority_ref=row.id,
        )
        try:
            with Session(engine) as session:
                session.add(row)
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
        except IntegrityError:
            concurrent = self._projection.get(principal, offer.offer_id)
            if concurrent is None:
                raise
            return self._repair_task(principal, concurrent), False
        persisted = self._projection.get(principal, offer.offer_id)
        if persisted is None:
            raise SpeechPeerCurationError("speech_peer_curation_projection_missing", status_code=503)
        return persisted, True

    def get(self, principal: VoicePrincipal, offer_id: str) -> SpeechPeerCurationRecord:
        value = self._projection.get(principal, require_identifier(offer_id, "speech_peer_curation_offer_invalid"))
        if value is None:
            raise SpeechPeerCurationError("speech_peer_curation_not_found", status_code=404)
        return value

    def receipt_public_key(self) -> dict[str, str]:
        return self._receipts.public_key_dict()

    def fence_offer(
        self,
        principal: VoicePrincipal,
        *,
        offer_id: str,
        reason_code: str,
        authority: str = "hub",
    ) -> int:
        """Transitively fence every local descendant before offer invalidation.

        ``principal`` is the already authenticated offer participant.  The
        curation projection itself belongs to the transfer recipient, so the
        Hub deliberately resolves that owner after the sync service has
        authorized access.  No peer can invoke this application boundary
        directly.
        """

        if authority != "hub":
            raise SpeechPeerCurationError("speech_peer_curation_hub_authority_required", status_code=403)
        offer_id = require_identifier(offer_id, "speech_peer_curation_offer_invalid")
        if not reason_code.startswith("speech_") or len(reason_code) > 128:
            raise SpeechPeerCurationError("speech_peer_curation_fence_reason_invalid", status_code=422)
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(
                    SpeechPeerEvidenceCurationDB.tenant_id == principal.tenant_id,
                    SpeechPeerEvidenceCurationDB.offer_id == offer_id,
                )
            ).all()
            curation_ids = tuple(row.id for row in rows)
        for curation_id in curation_ids:
            self._revocation.fence(curation_id=curation_id, reason_code=reason_code)
        return len(curation_ids)

    def claim_input(
        self,
        *,
        task_id: str,
        executor_id: str,
        executor_url: str,
    ) -> dict[str, object]:
        row, principal = self._projection.for_task(task_id)
        task = self._tasks.claim_execution(
            principal,
            task_id,
            executor_id=executor_id,
            executor_url=executor_url,
        )
        evidence = self._evidence.get(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=row.evidence_id,
        )
        if evidence is None or evidence.state != "admitted" or evidence.admission_digest != task.admission_digest:
            raise SpeechPeerCurationError("speech_peer_curation_evidence_fenced", status_code=410)
        envelope = self._evidence.encrypted(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=row.evidence_id,
        )
        clear = self._encryption.decrypt(envelope, security_mode="trusted_compute")
        if not clear or len(clear) > int(task.limits["max_output_bytes"]):
            raise SpeechPeerCurationError("speech_peer_curation_input_budget_exceeded", status_code=413)
        return {
            "task": task.to_dict(),
            "input_media_type": "application/vnd.ananta.peer-speech-curation-input+json",
            "input_b64": base64.b64encode(clear).decode("ascii"),
            "input_digest": hashlib.sha256(clear).hexdigest(),
        }

    def admit_result(
        self,
        *,
        executor_id: str,
        result_raw: object,
        artifact_raw: object,
    ) -> SpeechPeerCurationRecord:
        result = SpeechCurationWorkerResult.from_mapping(result_raw)
        row, principal = self._projection.for_task(result.task_id)
        artifact = curation_artifact(artifact_raw, result)
        if hashlib.sha256(canonical_json(artifact)).hexdigest() != result.artifact_digest:
            raise SpeechPeerCurationError("speech_peer_curation_artifact_digest_mismatch", status_code=409)
        self._artifacts.stage(principal, result, artifact)
        self._tasks.authorize_result(
            principal,
            result_raw,
            expected_executor_id=executor_id,
        )
        current = self._projection.get(principal, row.offer_id)
        if current is None:
            raise SpeechPeerCurationError("speech_peer_curation_not_found", status_code=404)
        if current.dataset_manifest_digest is not None:
            return current
        if current.state != "admitted":
            raise SpeechPeerCurationError("speech_peer_curation_offer_fenced", status_code=410)
        manifest, _created = self._dataset_materializer.build(principal, row, result, artifact)
        manifest_digest = str(manifest["manifest_digest"])
        now = self._clock_ms()
        published_audit = self._audit_events.prepare(
            principal,
            session_id=row.session_id,
            epoch=row.session_epoch,
            transition="curation_dataset_published",
            reason_code="speech_curation_dataset_published",
            idempotency_key=f"speech-curation:dataset-published:{row.id}:{manifest_digest}",
            authority_ref=f"{row.id}:{manifest_digest}",
        )
        with Session(engine) as session:
            persisted = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(
                    SpeechPeerEvidenceCurationDB.id == row.id,
                    SpeechPeerEvidenceCurationDB.dataset_manifest_digest.is_(None),
                    SpeechPeerEvidenceCurationDB.consent_version == result.consent_version,
                    SpeechPeerEvidenceCurationDB.revocation_epoch == result.revocation_epoch,
                )
            ).first()
            if persisted is not None:
                persisted.dataset_parent_digest = manifest.get("parent_digest")
                persisted.dataset_manifest_digest = manifest_digest
                persisted.state = "dataset_published"
                persisted.updated_at_ms = now
                session.add(persisted)
                if published_audit is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, published_audit)
            artifact_row = session.exec(
                select(SpeechPeerCurationArtifactDB).where(
                    SpeechPeerCurationArtifactDB.tenant_id == principal.tenant_id,
                    SpeechPeerCurationArtifactDB.task_id == result.task_id,
                    SpeechPeerCurationArtifactDB.artifact_digest == result.artifact_digest,
                )
            ).first()
            if artifact_row is not None:
                artifact_row.state = "published"
                artifact_row.updated_at_ms = now
                session.add(artifact_row)
            session.commit()
        final = self._projection.get(principal, row.offer_id)
        if final is None or final.dataset_manifest_digest != manifest_digest:
            raise SpeechPeerCurationError("speech_peer_curation_dataset_projection_conflict")
        return final

    def _current_recipient_consent(self, principal: VoicePrincipal, offer: SpeechEvidenceOfferRecord):
        expected_digest = (
            offer.sender_consent_digest if principal.subject == offer.sender_id else offer.recipient_consent_digest
        )
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == principal.subject,
                    SpeechEvidenceConsentDB.pair_id == offer.pair_id,
                    SpeechEvidenceConsentDB.session_id == offer.session_id,
                    SpeechEvidenceConsentDB.session_epoch == offer.epoch,
                    SpeechEvidenceConsentDB.consent_digest == expected_digest,
                    SpeechEvidenceConsentDB.state == "active",
                    SpeechEvidenceConsentDB.expires_at_ms > self._clock_ms(),
                )
            ).all()
        if len(rows) != 1:
            raise SpeechPeerCurationError("speech_peer_curation_consent_stale", status_code=403)
        current = self._consent.get(principal, rows[0].id)
        required_class = (
            "correction"
            if "correction" in offer.data_classes or "text_corrections" in offer.data_classes
            else "transcript"
        )
        if (
            current.consent_digest != expected_digest
            or current.consent_version != rows[0].consent_version
            or current.revocation_epoch != rows[0].revocation_epoch
            or current.purpose != offer.purpose
            or current.direction != offer.direction
            or current.grants.get("transcript_share") is not True
            or current.grants.get("dataset_import") is not True
            or current.grants.get("training") is not True
            or required_class not in current.data_classes
            or not current.trainer_locations
        ):
            raise SpeechPeerCurationError("speech_peer_curation_consent_too_narrow", status_code=403)
        return current

    def _repair_task(self, principal: VoicePrincipal, record: SpeechPeerCurationRecord) -> SpeechPeerCurationRecord:
        if record.state != "admitted" or record.curation_task_id is not None:
            return record
        task, _created = self._tasks.create(principal, admission_digest=record.admission_digest)
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(SpeechPeerEvidenceCurationDB.id == record.curation_id)
            ).first()
            if row is not None and row.curation_task_id is None:
                row.curation_task_id = task.task_id
                row.updated_at_ms = self._clock_ms()
                session.add(row)
                session.commit()
        recovered = self._projection.get(principal, record.offer_id)
        return recovered or record


def build_speech_peer_evidence_curation_service(
    sync: HubSpeechEvidenceSyncService,
    *,
    clock_ms: Callable[[], int] | None = None,
    audit: SemanticMediaAuditPort | None = None,
) -> SpeechPeerEvidenceCurationService:
    from agent.config import settings

    clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
    secret = str(settings.secret_key or "")
    if not secret:
        raise SpeechPeerCurationError("speech_peer_curation_secret_missing", status_code=500)
    root = hashlib.sha256(f"ananta-peer-speech-curation-v1\0{secret}".encode()).digest()
    authority = HubPeerAdmissionAuthority(hmac.new(root, b"admission", hashlib.sha256).digest())
    receipt_seed = hmac.new(root, b"receipt", hashlib.sha256).digest()
    receipt_key = Ed25519PrivateKey.from_private_bytes(receipt_seed)
    result_port = StagedSpeechCurationResultPort()
    tasks = SpeechEvidenceCurationTaskService(result_port=result_port, clock_ms=clock)
    return SpeechPeerEvidenceCurationService(
        sync=sync,
        store=get_speech_evidence_store_service(),
        admission=SpeechEvidenceAdmissionPolicy(authority=authority, audit=audit, clock_ms=clock),
        poisoning=SpeechEvidencePoisoningPolicy(),
        receipts=SpeechEvidenceReceiptService(
            receipt_key,
            hub_key_id=f"speech-hub-{hashlib.sha256(receipt_seed).hexdigest()[:24]}",
            clock_ms=clock,
        ),
        curation_tasks=tasks,
        datasets=MlInternSpeechDatasetBuildService(
            publisher=HubLocalSpeechDatasetPublisher(),
            evidence_fence=HubSpeechDatasetEvidenceFence(),
            clock_ms=clock,
            audit=audit,
        ),
        authority=authority,
        identity=SpeechEvidenceIdentityService(hmac.new(root, b"identity", hashlib.sha256).digest()),
        audit=audit,
        clock_ms=clock,
    )


__all__ = [
    "HubLocalSpeechDatasetPublisher",
    "HubPeerAdmissionAuthority",
    "SpeechPeerCurationError",
    "SpeechPeerCurationGroupInput",
    "SpeechPeerCurationRecord",
    "SpeechPeerEvidenceCurationService",
    "StagedSpeechCurationResultPort",
    "build_speech_peer_evidence_curation_service",
]
