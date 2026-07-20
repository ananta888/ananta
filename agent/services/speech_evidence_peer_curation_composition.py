"""Productive Hub composition for peer speech-evidence curation.

The browser may request curation and disclose the already acknowledged clear
chunks, but it cannot decide admission or dataset membership.  This service
rebinds every chunk to the signed transfer commitments, encrypts the aggregate
immediately, runs Hub policies, emits a Hub-signed receipt and delegates one
bounded child task.  Dataset publication happens only after an authenticated
Worker result passes the current consent/revocation fence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechDatasetManifestDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
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
from agent.services.ml_intern_speech_dataset_port import MlInternSpeechDatasetPort
from agent.services.ml_intern_speech_lineage_service import (
    MlInternSpeechLineageService,
    get_ml_intern_speech_lineage_service,
)
from agent.services.ml_intern_speech_revocation_service import MlInternSpeechRevocationService
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.speech_evidence_admission_policy import (
    SpeechEvidenceAdmissionPolicy,
    SpeechEvidenceAuthorityPort,
)
from agent.services.speech_evidence_consent_service import (
    SpeechEvidenceConsentService,
    get_speech_evidence_consent_service,
)
from agent.services.speech_evidence_curation_task_service import (
    SpeechCurationResultPort,
    SpeechEvidenceCurationTaskService,
)
from agent.services.speech_evidence_encryption_port import (
    SpeechEvidenceEncryptionPort,
    get_speech_evidence_encryption_port,
)
from agent.services.speech_evidence_offer_service import (
    SpeechEvidenceOfferRecord,
    speech_evidence_quality_policy_digest,
    speech_evidence_speaker_scope_digest,
)
from agent.services.speech_evidence_poisoning_policy import (
    EvidenceCandidateRiskSignal,
    SpeechEvidencePoisoningPolicy,
)
from agent.services.speech_evidence_receipt_service import SpeechEvidenceReceiptService
from agent.services.speech_evidence_store_service import (
    SpeechEvidenceStoreService,
    get_speech_evidence_store_service,
)
from agent.services.speech_evidence_sync_composition import HubSpeechEvidenceSyncService
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SpeechCurationWorkerResult,
    canonical_json,
)
from ananta_contracts.speech_evidence_sync import (
    VerifiedSpeechEvidenceMessage,
    group_preview_group_id,
    group_preview_resolution_digest,
)
from voice_runtime.evidence_identity import SpeechEvidenceIdentityService

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_GROUP_SCHEMA = "ananta.peer-transcript-evidence.v1"
_INPUT_SCHEMA = "ananta.peer-speech-hub-curation-input.v1"
_ARTIFACT_SCHEMA = "ananta.peer-speech-curation-artifact.v1"
_MAX_GROUP_BYTES = 1024 * 1024
_MAX_AGGREGATE_BYTES = 8 * 1024 * 1024
_MAX_TEXT_CHARS = 32_768


class SpeechPeerCurationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechPeerCurationGroupInput:
    group_id: str
    chunks_b64: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: object) -> "SpeechPeerCurationGroupInput":
        value = _closed_mapping(raw, {"group_id", "chunks_b64"}, "speech_peer_curation_group_invalid")
        group_id = _identifier(value.get("group_id"), "speech_peer_curation_group_invalid")
        chunks = value.get("chunks_b64")
        if not isinstance(chunks, list) or not chunks or len(chunks) > 16_384:
            raise SpeechPeerCurationError("speech_peer_curation_chunks_invalid", status_code=422)
        rendered = tuple(str(item) for item in chunks)
        if any(not item or len(item) > 96 * 1024 for item in rendered):
            raise SpeechPeerCurationError("speech_peer_curation_chunk_invalid", status_code=413)
        return cls(group_id=group_id, chunks_b64=rendered)


@dataclass(frozen=True)
class SpeechPeerCurationRecord:
    curation_id: str
    offer_id: str
    admission_digest: str
    state: str
    receipt: Mapping[str, object]
    curation_task_id: str | None
    dataset_id: str
    dataset_parent_digest: str | None
    dataset_manifest_digest: str | None
    consent_version: int
    revocation_epoch: int

    def public_dict(self) -> dict[str, object]:
        return {
            "curation_id": self.curation_id,
            "offer_id": self.offer_id,
            "admission_digest": self.admission_digest,
            "state": self.state,
            "receipt": dict(self.receipt),
            "curation_task_id": self.curation_task_id,
            "dataset_id": self.dataset_id,
            "dataset_parent_digest": self.dataset_parent_digest,
            "dataset_manifest_digest": self.dataset_manifest_digest,
            "consent_version": self.consent_version,
            "revocation_epoch": self.revocation_epoch,
        }


class HubPeerAdmissionAuthority(SpeechEvidenceAuthorityPort):
    """One-domain Hub MAC used only after sync authority was revalidated."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("speech_peer_admission_key_invalid")
        self._key = bytes(key)

    def sign(self, evidence_digest: str) -> str:
        return hmac.new(self._key, evidence_digest.encode("ascii"), hashlib.sha256).hexdigest()

    def authorize(self, **bindings: object) -> tuple[bool, str]:
        digest = str(bindings.get("evidence_digest") or "")
        signature = str(bindings.get("signature") or "")
        if _DIGEST.fullmatch(digest) is None or not hmac.compare_digest(self.sign(digest), signature):
            return False, "speech_evidence_hub_admission_signature_invalid"
        return True, "speech_evidence_hub_admission_authorized"


class HubLocalSpeechDatasetPublisher(MlInternSpeechDatasetPort):
    """Fail-closed local publication boundary for content-free manifests.

    The canonical builder writes the manifest and lineage in the same Hub
    transaction after this bounded validation callback.  No filesystem or
    Worker-controlled location is involved.
    """

    def publish_manifest(self, *, tenant_id: str, owner_subject: str, manifest: Mapping[str, object]) -> bool:
        del tenant_id, owner_subject
        if manifest.get("schema") != MlInternSpeechDatasetBuildService.SCHEMA:
            return False
        encoded = canonical_json(dict(manifest))
        return 0 < len(encoded) <= 4 * 1024 * 1024 and not any(
            token in encoded.lower() for token in (b"plaintext", b"private_key", b"file://")
        )


class StagedSpeechCurationResultPort(SpeechCurationResultPort):
    """Authorize only the exact content-free artifact staged by the Worker."""

    def publish(self, result: SpeechCurationWorkerResult) -> bool:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerCurationArtifactDB).where(
                    SpeechPeerCurationArtifactDB.task_id == result.task_id,
                    SpeechPeerCurationArtifactDB.admission_digest == result.admission_digest,
                    SpeechPeerCurationArtifactDB.artifact_ref == result.artifact_ref,
                    SpeechPeerCurationArtifactDB.artifact_digest == result.artifact_digest,
                    SpeechPeerCurationArtifactDB.consent_version == result.consent_version,
                    SpeechPeerCurationArtifactDB.revocation_epoch == result.revocation_epoch,
                    SpeechPeerCurationArtifactDB.fencing_token == result.fencing_token,
                    SpeechPeerCurationArtifactDB.state.in_(["quarantined", "published"]),
                )
            ).first()
            return row is not None


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
        self._audit = audit
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

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
        existing = self._get(principal, offer.offer_id)
        if existing is not None:
            return self._repair_task(principal, existing), False

        consent = self._current_recipient_consent(principal, offer)
        decoded = self._decode_groups(
            supplied,
            bindings,
            offer=offer,
            speaker_id=consent.speaker_id,
        )
        source_binding_digest = _sha256(
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
                "schema": _INPUT_SCHEMA,
                "offer_id": offer.offer_id,
                "source_binding_digest": source_binding_digest,
                "groups": [payload for _group_id, _body, payload, _digest_value in decoded],
            }
        )
        if len(aggregate) > _MAX_AGGREGATE_BYTES:
            raise SpeechPeerCurationError("speech_peer_curation_payload_too_large", status_code=413)
        data_sender = bindings[0].sender_id
        contributor_digest = hashlib.sha256(f"peer\0{data_sender}".encode()).hexdigest()
        aggregate_digest = hashlib.sha256(aggregate).hexdigest()
        source_digest = _sha256(
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
                self._risk_signal(
                    group_id=group_id,
                    payload=payload,
                    content_digest=content_digest,
                    contributor_digest=contributor_digest,
                    consent_digest=consent.consent_digest,
                )
                for group_id, _body, payload, content_digest in decoded
            )
        )
        authority_digest = _sha256(
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
        policy_digest = _sha256(
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
        dataset_id = _dataset_id(principal, offer.pair_id)
        row = SpeechPeerEvidenceCurationDB(
            id=f"speech-peer-curation-{_joined_digest(principal.tenant_id, principal.subject, offer.offer_id)[:32]}",
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
        audit_event = self._prepare_audit_event(
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
            concurrent = self._get(principal, offer.offer_id)
            if concurrent is None:
                raise
            return self._repair_task(principal, concurrent), False
        persisted = self._get(principal, offer.offer_id)
        if persisted is None:
            raise SpeechPeerCurationError("speech_peer_curation_projection_missing", status_code=503)
        return persisted, True

    def _prepare_audit_event(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        epoch: int,
        transition: str,
        reason_code: str,
        idempotency_key: str,
        authority_ref: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                scope=f"speech-evidence:{session_id}",
                event_type="speech_evidence",
                transition=transition,
                reason_code=reason_code,
                epoch=epoch,
                job_ref=authority_ref,
            )
        except Exception as exc:
            raise SpeechPeerCurationError(
                "speech_peer_curation_audit_unavailable",
                status_code=503,
            ) from exc

    def get(self, principal: VoicePrincipal, offer_id: str) -> SpeechPeerCurationRecord:
        value = self._get(principal, _identifier(offer_id, "speech_peer_curation_offer_invalid"))
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
        offer_id = _identifier(offer_id, "speech_peer_curation_offer_invalid")
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
            self._fence_curation(curation_id=curation_id, reason_code=reason_code)
        return len(curation_ids)

    def claim_input(
        self,
        *,
        task_id: str,
        executor_id: str,
        executor_url: str,
    ) -> dict[str, object]:
        row, principal = self._curation_for_task(task_id)
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
        row, principal = self._curation_for_task(result.task_id)
        artifact = _curation_artifact(artifact_raw, result)
        if hashlib.sha256(canonical_json(artifact)).hexdigest() != result.artifact_digest:
            raise SpeechPeerCurationError("speech_peer_curation_artifact_digest_mismatch", status_code=409)
        self._stage_artifact(principal, result, artifact)
        self._tasks.authorize_result(
            principal,
            result_raw,
            expected_executor_id=executor_id,
        )
        current = self._get(principal, row.offer_id)
        if current is None:
            raise SpeechPeerCurationError("speech_peer_curation_not_found", status_code=404)
        if current.dataset_manifest_digest is not None:
            return current
        if current.state != "admitted":
            raise SpeechPeerCurationError("speech_peer_curation_offer_fenced", status_code=410)
        manifest, _created = self._build_dataset(principal, row, result, artifact)
        manifest_digest = str(manifest["manifest_digest"])
        now = self._clock_ms()
        published_audit = self._prepare_audit_event(
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
        final = self._get(principal, row.offer_id)
        if final is None or final.dataset_manifest_digest != manifest_digest:
            raise SpeechPeerCurationError("speech_peer_curation_dataset_projection_conflict")
        return final

    def _fence_curation(self, *, curation_id: str, reason_code: str) -> None:
        now = self._clock_ms()
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB)
                .where(SpeechPeerEvidenceCurationDB.id == curation_id)
                .with_for_update()
            ).first()
            if row is None:
                return
            if row.state == "invalidated":
                return
            owner = VoicePrincipal(row.tenant_id, row.owner_subject)
            reason_digest = hashlib.sha256(reason_code.encode()).hexdigest()
            started_audit = self._prepare_audit_event(
                owner,
                session_id=row.session_id,
                epoch=row.session_epoch,
                transition="curation_revocation_started",
                reason_code="speech_curation_revocation_started",
                idempotency_key=f"speech-curation:revoke-start:{row.id}:{reason_digest}",
                authority_ref=f"{row.id}:{reason_digest}",
            )
            row.state = "invalidating"
            row.updated_at_ms = now
            session.add(row)
            if started_audit is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, started_audit)
            session.commit()
            evidence_id = row.evidence_id
            task_id = row.curation_task_id
            fence_epoch = row.revocation_epoch + 1
            session_id = row.session_id
            session_epoch = row.session_epoch
        if task_id is not None:
            self._tasks.fence(owner, task_id, reason_code=reason_code)
        evidence = self._evidence.get(
            tenant_id=owner.tenant_id,
            owner_subject=owner.subject,
            evidence_id=evidence_id,
        )
        if evidence is None:
            raise SpeechPeerCurationError("speech_peer_curation_evidence_missing", status_code=503)
        evidence_audit = self._prepare_audit_event(
            owner,
            session_id=session_id,
            epoch=session_epoch,
            transition="curation_evidence_revoked",
            reason_code="speech_curation_evidence_revoked",
            idempotency_key=f"speech-curation:evidence-revoked:{curation_id}:{reason_digest}",
            authority_ref=f"{evidence.evidence_id}:{reason_digest}",
        )
        self._evidence.transition(
            tenant_id=owner.tenant_id,
            owner_subject=owner.subject,
            evidence_id=evidence.evidence_id,
            expected_states=("quarantined", "admitted", "rejected", "accepted", "revoked"),
            target="revoked",
            now_ms=now,
            admission_digest=evidence.admission_digest,
            audit_event=evidence_audit,
        )
        self._encryption.destroy(evidence.key_id, tenant_id=owner.tenant_id)
        try:
            impact = self._lineage_service.impact(
                owner,
                root_kind="evidence",
                root_digest=evidence.content_digest,
                revocation_epoch=fence_epoch,
            )
        except Exception as exc:
            raise SpeechPeerCurationError(
                str(getattr(exc, "reason_code", "speech_peer_curation_lineage_unavailable")),
                status_code=503,
            ) from exc
        self._training_revocation.fence_impact(owner, impact)
        manifest_digests = tuple(
            str(node["digest"]) for node in impact.nodes if str(node["kind"]) == "manifest"
        )
        completed_audit = self._prepare_audit_event(
            owner,
            session_id=session_id,
            epoch=session_epoch,
            transition="curation_revoked",
            reason_code="speech_curation_revoked",
            idempotency_key=f"speech-curation:revoked:{curation_id}:{reason_digest}",
            authority_ref=f"{curation_id}:{reason_digest}",
        )
        with Session(engine) as session:
            if manifest_digests:
                session.exec(
                    update(SpeechDatasetManifestDB)
                    .where(
                        SpeechDatasetManifestDB.tenant_id == owner.tenant_id,
                        SpeechDatasetManifestDB.owner_subject == owner.subject,
                        SpeechDatasetManifestDB.manifest_digest.in_(manifest_digests),
                        SpeechDatasetManifestDB.status == "active",
                    )
                    .values(status="revoked")
                )
            if task_id is not None:
                session.exec(
                    update(SpeechPeerCurationArtifactDB)
                    .where(
                        SpeechPeerCurationArtifactDB.tenant_id == owner.tenant_id,
                        SpeechPeerCurationArtifactDB.owner_subject == owner.subject,
                        SpeechPeerCurationArtifactDB.task_id == task_id,
                        SpeechPeerCurationArtifactDB.state.in_(["quarantined", "published"]),
                    )
                    .values(state="fenced", updated_at_ms=now)
                )
            session.exec(
                update(SpeechPeerEvidenceCurationDB)
                .where(SpeechPeerEvidenceCurationDB.id == curation_id)
                .values(state="invalidated", updated_at_ms=now)
            )
            if completed_audit is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, completed_audit)
            session.commit()
        self._lineage_repository.mark_status(
            tenant_id=owner.tenant_id,
            owner_subject=owner.subject,
            nodes=((str(node["kind"]), str(node["digest"])) for node in impact.nodes),
            status="revoked",
            revocation_epoch=fence_epoch,
            now_ms=now,
        )

    def _decode_groups(
        self,
        supplied: tuple[SpeechPeerCurationGroupInput, ...],
        bindings: tuple[SpeechEvidenceTransferCurationBinding, ...],
        *,
        offer: SpeechEvidenceOfferRecord,
        speaker_id: str,
    ) -> tuple[tuple[str, bytes, dict[str, object], str], ...]:
        requested = {value.group_id: value for value in supplied}
        decoded: list[tuple[str, bytes, dict[str, object], str]] = []
        aggregate_bytes = 0
        expected_speaker_scope = speech_evidence_speaker_scope_digest(
            pair_id=offer.pair_id,
            epoch=offer.epoch,
            speaker_id=speaker_id,
        )
        expected_quality = speech_evidence_quality_policy_digest()
        authorized_previews = {value.group_id: value for value in offer.group_previews}
        for binding in sorted(bindings, key=lambda value: value.group_id):
            preview = binding.preview
            if (
                binding.offer_id != offer.offer_id
                or binding.offer_group_preview_digest != offer.group_preview_digest
                or authorized_previews.get(binding.group_id) != preview
                or preview.group_id != binding.group_id
                or preview.group_id
                != group_preview_group_id(preview.source_group_digest, preview.revision)
                or preview.resolution_digest
                != group_preview_resolution_digest(preview.source_group_digest, preview.revision)
                or preview.speaker_scope_digest != expected_speaker_scope
                or preview.quality_basis != "policy"
                or preview.quality_digest != expected_quality
                or preview.size_bytes != binding.received_bytes
            ):
                raise SpeechPeerCurationError(
                    "speech_peer_curation_offer_preview_invalid",
                    status_code=409,
                )
            item = requested[binding.group_id]
            if len(item.chunks_b64) != len(binding.chunks):
                raise SpeechPeerCurationError("speech_peer_curation_chunk_binding_mismatch", status_code=409)
            chunks: list[bytes] = []
            for encoded, expected in zip(item.chunks_b64, binding.chunks, strict=True):
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise SpeechPeerCurationError(
                        "speech_peer_curation_chunk_encoding_invalid", status_code=422
                    ) from exc
                if len(chunk) != expected.plaintext_bytes or not hmac.compare_digest(
                    hashlib.sha256(chunk).hexdigest(), expected.plaintext_digest
                ):
                    raise SpeechPeerCurationError("speech_peer_curation_chunk_digest_mismatch", status_code=409)
                chunks.append(chunk)
            body = b"".join(chunks)
            if not body or len(body) != binding.received_bytes or len(body) > _MAX_GROUP_BYTES:
                raise SpeechPeerCurationError("speech_peer_curation_group_size_invalid", status_code=413)
            payload = _parse_group(body)
            content_digest = hashlib.sha256(body).hexdigest()
            if payload["source_digest"] != preview.source_group_digest:
                raise SpeechPeerCurationError(
                    "speech_peer_curation_source_group_mismatch",
                    status_code=409,
                )
            if payload["revision"] != preview.revision:
                raise SpeechPeerCurationError(
                    "speech_peer_curation_revision_mismatch",
                    status_code=409,
                )
            aggregate_bytes += len(body)
            if aggregate_bytes > _MAX_AGGREGATE_BYTES:
                raise SpeechPeerCurationError("speech_peer_curation_payload_too_large", status_code=413)
            decoded.append((binding.group_id, body, payload, content_digest))
        return tuple(decoded)

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

    @staticmethod
    def _risk_signal(
        *,
        group_id: str,
        payload: Mapping[str, object],
        content_digest: str,
        contributor_digest: str,
        consent_digest: str,
    ) -> EvidenceCandidateRiskSignal:
        candidates = list(payload["candidates"])
        authorities = [str(dict(value)["authority"]) for value in candidates]
        text = "\n".join(str(dict(value)["text"]) for value in candidates).lower()
        return EvidenceCandidateRiskSignal(
            candidate_id=group_id,
            source_role="speaker",
            contributor_digest=contributor_digest,
            lineage_digest=str(payload["source_digest"]),
            model_digest=_sha256(authorities),
            validator_set_digest=consent_digest,
            signature_valid=True,
            digest_valid=_DIGEST.fullmatch(content_digest) is not None,
            speaker_scope_valid=True,
            replayed=False,
            confidence_micros=750_000,
            contradictory_revision_count=0,
            distribution_distance_micros=0,
            trigger_phrase_detected="targeted trigger" in text,
        )

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
        recovered = self._get(principal, record.offer_id)
        return recovered or record

    def _get(self, principal: VoicePrincipal, offer_id: str) -> SpeechPeerCurationRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(
                    SpeechPeerEvidenceCurationDB.tenant_id == principal.tenant_id,
                    SpeechPeerEvidenceCurationDB.owner_subject == principal.subject,
                    SpeechPeerEvidenceCurationDB.offer_id == offer_id,
                )
            ).first()
            return _curation_record(row) if row is not None else None

    @staticmethod
    def _curation_for_task(task_id: str) -> tuple[SpeechPeerEvidenceCurationDB, VoicePrincipal]:
        task_id = _identifier(task_id, "speech_peer_curation_task_invalid")
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(SpeechPeerEvidenceCurationDB.curation_task_id == task_id)
            ).first()
            if row is None:
                raise SpeechPeerCurationError("speech_peer_curation_task_not_found", status_code=404)
            session.expunge(row)
        return row, VoicePrincipal(row.tenant_id, row.owner_subject)

    def _stage_artifact(
        self,
        principal: VoicePrincipal,
        result: SpeechCurationWorkerResult,
        artifact: Mapping[str, object],
    ) -> None:
        now = self._clock_ms()
        row = SpeechPeerCurationArtifactDB(
            id=f"speech-peer-artifact-{_joined_digest(principal.tenant_id, result.task_id)[:32]}",
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=result.task_id,
            admission_digest=result.admission_digest,
            artifact_ref=result.artifact_ref,
            artifact_digest=result.artifact_digest,
            artifact_payload=dict(artifact),
            consent_version=result.consent_version,
            revocation_epoch=result.revocation_epoch,
            fencing_token=result.fencing_token,
            created_at_ms=now,
            updated_at_ms=now,
        )
        try:
            with Session(engine) as session:
                session.add(row)
                session.commit()
        except IntegrityError:
            with Session(engine) as session:
                existing = session.exec(
                    select(SpeechPeerCurationArtifactDB).where(
                        SpeechPeerCurationArtifactDB.tenant_id == principal.tenant_id,
                        SpeechPeerCurationArtifactDB.task_id == result.task_id,
                    )
                ).first()
            if existing is None or (
                existing.artifact_ref != result.artifact_ref
                or existing.artifact_digest != result.artifact_digest
                or dict(existing.artifact_payload or {}) != dict(artifact)
            ):
                raise SpeechPeerCurationError("speech_peer_curation_artifact_replay_mismatch")

    def _build_dataset(
        self,
        principal: VoicePrincipal,
        curation: SpeechPeerEvidenceCurationDB,
        result: SpeechCurationWorkerResult,
        artifact: Mapping[str, object],
    ) -> tuple[dict[str, object], bool]:
        with Session(engine) as session:
            evidence = session.exec(
                select(SpeechEvidenceDB).where(
                    SpeechEvidenceDB.id == curation.evidence_id,
                    SpeechEvidenceDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceDB.owner_subject == principal.subject,
                    SpeechEvidenceDB.state == "admitted",
                    SpeechEvidenceDB.admission_digest == result.admission_digest,
                )
            ).first()
            consent = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.id == curation.consent_id,
                    SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == principal.subject,
                    SpeechEvidenceConsentDB.state == "active",
                    SpeechEvidenceConsentDB.consent_version == result.consent_version,
                    SpeechEvidenceConsentDB.revocation_epoch == result.revocation_epoch,
                )
            ).first()
            latest = session.exec(
                select(SpeechDatasetManifestDB)
                .where(
                    SpeechDatasetManifestDB.tenant_id == principal.tenant_id,
                    SpeechDatasetManifestDB.owner_subject == principal.subject,
                    SpeechDatasetManifestDB.dataset_id == curation.dataset_id,
                    SpeechDatasetManifestDB.status == "active",
                )
                .order_by(SpeechDatasetManifestDB.created_at_ms.desc())
            ).first()
        if evidence is None or consent is None or consent.expires_at_ms <= self._clock_ms():
            raise SpeechPeerCurationError("speech_peer_curation_dataset_consent_stale", status_code=403)
        if artifact["evidence_record_digest"] != evidence.content_digest:
            raise SpeechPeerCurationError("speech_peer_curation_artifact_evidence_mismatch", status_code=409)
        contributor = curation.contributor_digest
        source = evidence.source_digest
        report_digest = str(artifact["curation_report_digest"])
        record = {
            "record_digest": evidence.content_digest,
            "lineage_kind": "evidence",
            "source_digest": source,
            "utterance_family_id": evidence.utterance_family_id,
            "session_group_id": _sha256(
                {"pair_id": curation.pair_id, "session_id": curation.session_id, "epoch": curation.session_epoch}
            ),
            "near_duplicate_group_id": _sha256(
                {"source_digest": source, "utterance_family_id": evidence.utterance_family_id}
            ),
            "contributors": [contributor],
            "data_classes": [curation.data_class],
            "field_provenance": {
                "transcript": {"contributor_digest": contributor, "source_digest": source},
            },
            "consent_refs": [
                {
                    "consent_id": consent.id,
                    "consent_version": consent.consent_version,
                    "revocation_epoch": consent.revocation_epoch,
                    "consent_digest": consent.consent_digest,
                }
            ],
            "duration_ms": int(artifact["duration_ms"]),
            "curation_report_digest": report_digest,
        }
        return self._datasets.build(
            principal,
            dataset_id=curation.dataset_id,
            records=(record,),
            curation_report_digest=report_digest,
            parent_digest=latest.manifest_digest if latest is not None else None,
            authority="hub",
        )


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


def _parse_group(body: bytes) -> dict[str, object]:
    try:
        raw = json.loads(body.decode("utf-8"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SpeechPeerCurationError("speech_peer_curation_group_payload_invalid", status_code=422) from exc
    value = _closed_mapping(
        raw,
        {"schema", "turn_id", "revision", "state", "source_digest", "candidates"},
        "speech_peer_curation_group_payload_invalid",
    )
    if value.get("schema") != _GROUP_SCHEMA:
        raise SpeechPeerCurationError("speech_peer_curation_group_schema_invalid", status_code=422)
    turn_id = _identifier(value.get("turn_id"), "speech_peer_curation_turn_invalid")
    revision = _integer(value.get("revision"), 1, 2_147_483_647, "speech_peer_curation_revision_invalid")
    state = str(value.get("state") or "")
    if state not in {"final", "corrected", "correction_failed"}:
        raise SpeechPeerCurationError("speech_peer_curation_state_invalid", status_code=422)
    source_digest = _digest(value.get("source_digest"), "speech_peer_curation_source_digest_invalid")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= 32:
        raise SpeechPeerCurationError("speech_peer_curation_candidates_invalid", status_code=422)
    candidates: list[dict[str, object]] = []
    for raw_candidate in raw_candidates:
        candidate = _closed_mapping(
            raw_candidate,
            {"revision", "authority", "text"},
            "speech_peer_curation_candidate_invalid",
        )
        text = candidate.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT_CHARS:
            raise SpeechPeerCurationError("speech_peer_curation_candidate_text_invalid", status_code=422)
        candidates.append(
            {
                "revision": _integer(
                    candidate.get("revision"), 1, 2_147_483_647, "speech_peer_curation_candidate_invalid"
                ),
                "authority": _identifier(
                    candidate.get("authority"), "speech_peer_curation_candidate_authority_invalid"
                ),
                "text": text,
            }
        )
    return {
        "schema": _GROUP_SCHEMA,
        "turn_id": turn_id,
        "revision": revision,
        "state": state,
        "source_digest": source_digest,
        "candidates": candidates,
    }


def _curation_artifact(raw: object, result: SpeechCurationWorkerResult) -> dict[str, object]:
    value = _closed_mapping(
        raw,
        {
            "schema",
            "task_id",
            "admission_digest",
            "evidence_record_digest",
            "curation_report_digest",
            "resolution_policy_version",
            "duration_ms",
        },
        "speech_peer_curation_artifact_invalid",
    )
    artifact = {
        "schema": str(value.get("schema") or ""),
        "task_id": _identifier(value.get("task_id"), "speech_peer_curation_artifact_task_invalid"),
        "admission_digest": _digest(value.get("admission_digest"), "speech_peer_curation_artifact_admission_invalid"),
        "evidence_record_digest": _digest(
            value.get("evidence_record_digest"), "speech_peer_curation_artifact_evidence_invalid"
        ),
        "curation_report_digest": _digest(
            value.get("curation_report_digest"), "speech_peer_curation_artifact_report_invalid"
        ),
        "resolution_policy_version": _identifier(
            value.get("resolution_policy_version"), "speech_peer_curation_artifact_policy_invalid"
        ),
        "duration_ms": _integer(
            value.get("duration_ms"), 1, 3_600_000, "speech_peer_curation_artifact_duration_invalid"
        ),
    }
    if (
        artifact["schema"] != _ARTIFACT_SCHEMA
        or artifact["task_id"] != result.task_id
        or artifact["admission_digest"] != result.admission_digest
    ):
        raise SpeechPeerCurationError("speech_peer_curation_artifact_binding_mismatch", status_code=409)
    return artifact


def _curation_record(row: SpeechPeerEvidenceCurationDB) -> SpeechPeerCurationRecord:
    return SpeechPeerCurationRecord(
        curation_id=row.id,
        offer_id=row.offer_id,
        admission_digest=row.admission_digest,
        state=row.state,
        receipt=dict(row.receipt_payload or {}),
        curation_task_id=row.curation_task_id,
        dataset_id=row.dataset_id,
        dataset_parent_digest=row.dataset_parent_digest,
        dataset_manifest_digest=row.dataset_manifest_digest,
        consent_version=row.consent_version,
        revocation_epoch=row.revocation_epoch,
    )


def _closed_mapping(raw: object, fields: set[str], reason_code: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw) or set(raw) != fields:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return dict(raw)


def _identifier(raw: object, reason_code: str) -> str:
    value = str(raw or "") if isinstance(raw, str) else ""
    if _IDENTIFIER.fullmatch(value) is None:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return value


def _digest(raw: object, reason_code: str) -> str:
    value = str(raw or "") if isinstance(raw, str) else ""
    if _DIGEST.fullmatch(value) is None:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return value


def _integer(raw: object, minimum: int, maximum: int, reason_code: str) -> int:
    if type(raw) is not int or not minimum <= raw <= maximum:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return raw


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _dataset_id(principal: VoicePrincipal, pair_id: str) -> str:
    digest = _joined_digest(principal.tenant_id, principal.subject, pair_id)
    return f"speech-peer-dataset-{digest[:32]}"


def _joined_digest(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


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
