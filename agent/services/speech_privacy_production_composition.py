"""Production composition for Hub-owned speech privacy lifecycle fencing.

The lifecycle coordinator is deliberately persistence-agnostic.  This module
binds it to the authoritative SQL control planes and to the transitive speech
evidence revocation service.  Every supported phase has an explicit handler;
construction fails closed if that registry ever drifts from the domain
contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.ml_intern_training import MlInternSpeechAdapterDB, MlInternTrainingJobDB
from agent.db_models.speech_evidence import (
    SpeechCurationTaskDB,
    SpeechDatasetManifestDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
    SpeechEvidenceKeyDB,
    SpeechEvidenceRevocationDB,
    SpeechPrivacyLifecycleDB,
)
from agent.db_models.speech_evidence_sync import SpeechEvidenceOfferDB, SpeechEvidenceTransferDB
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationJobDB,
)
from agent.repositories.speech_evidence_sync import (
    SqlSpeechEvidenceOfferRepository,
    SqlSpeechEvidencePeerKeyRegistry,
    SqlSpeechEvidenceTransferRepository,
)
from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
from agent.services.speech_evidence_encryption_port import (
    SpeechEvidenceEncryptionPort,
    get_speech_evidence_encryption_port,
)
from agent.services.speech_evidence_revocation_service import (
    SpeechEvidenceRevocationResult,
    SpeechEvidenceRevocationService,
    get_speech_evidence_revocation_service,
)
from agent.services.speech_privacy_lifecycle_service import (
    SAFE_STATE_BY_PHASE,
    SPEECH_DATA_PHASES,
    SpeechPrivacyLifecycleError,
    SpeechPrivacyLifecycleService,
    SpeechPrivacyTombstone,
)
from agent.services.voice_governance_domain import VoicePrincipal

PRODUCTION_SPEECH_PRIVACY_PHASES = frozenset(SAFE_STATE_BY_PHASE)
_ACTIVE_CURATION_STATES = ("pending_queue", "queued", "running", "publishing")
_ACTIVE_RECONCILIATION_STATES = ("queued", "running", "cancel_requested", "paused")


@dataclass(frozen=True, slots=True)
class SpeechPrivacyEvidenceBinding:
    evidence_id: str
    evidence_digest: str
    scope_digest: str
    consent_id: str
    consent_version: int
    consent_state: str
    current_revocation_epoch: int
    contributor_id: str
    session_id: str
    pair_id: str
    key_id: str


class SqlSpeechPrivacyBindingResolver:
    """Resolve opaque lifecycle digests inside one authenticated principal."""

    def __init__(self, principal: VoicePrincipal) -> None:
        self._principal = principal

    def resolve(self, *, scope_digest: str, evidence_digest: str) -> SpeechPrivacyEvidenceBinding:
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceDB, SpeechEvidenceConsentDB)
                .join(
                    SpeechEvidenceConsentDB,
                    SpeechEvidenceConsentDB.id == SpeechEvidenceDB.consent_id,
                )
                .where(
                    SpeechEvidenceDB.tenant_id == self._principal.tenant_id,
                    SpeechEvidenceDB.owner_subject == self._principal.subject,
                    SpeechEvidenceDB.content_digest == evidence_digest,
                    SpeechEvidenceConsentDB.tenant_id == self._principal.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == self._principal.subject,
                )
            ).all()
        if len(rows) != 1:
            raise SpeechPrivacyLifecycleError("speech_privacy_product_binding_missing")
        evidence, consent = rows[0]
        if consent.scope_digest != scope_digest:
            with Session(engine) as session:
                reservation = session.exec(
                    select(SpeechPrivacyLifecycleDB.id).where(
                        SpeechPrivacyLifecycleDB.tenant_id == self._principal.tenant_id,
                        SpeechPrivacyLifecycleDB.owner_subject == self._principal.subject,
                        SpeechPrivacyLifecycleDB.evidence_digest == evidence_digest,
                        SpeechPrivacyLifecycleDB.scope_digest == scope_digest,
                    )
                ).first()
            if reservation is None:
                raise SpeechPrivacyLifecycleError("speech_privacy_product_binding_missing")
        return SpeechPrivacyEvidenceBinding(
            evidence_id=str(evidence.id),
            evidence_digest=str(evidence.content_digest),
            scope_digest=str(consent.scope_digest),
            consent_id=str(consent.id),
            consent_version=int(consent.consent_version),
            consent_state=str(consent.state),
            current_revocation_epoch=int(consent.revocation_epoch),
            contributor_id=str(consent.speaker_id),
            session_id=str(evidence.session_id),
            pair_id=str(evidence.pair_id),
            key_id=str(evidence.key_id),
        )


class SqlSpeechPrivacyTombstoneRepository:
    """Principal-scoped durable completion ledger for crash-safe replay."""

    def __init__(self, principal: VoicePrincipal, *, clock_ms=None) -> None:
        self._principal = principal
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def get(self, evidence_digest: str) -> SpeechPrivacyTombstone | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPrivacyLifecycleDB).where(
                    SpeechPrivacyLifecycleDB.tenant_id == self._principal.tenant_id,
                    SpeechPrivacyLifecycleDB.owner_subject == self._principal.subject,
                    SpeechPrivacyLifecycleDB.evidence_digest == evidence_digest,
                )
            ).first()
        return _tombstone(row) if row is not None and row.local_fenced and row.key_destroyed else None

    def reserve(
        self,
        *,
        scope_digest: str,
        evidence_digest: str,
        phase: str,
        revocation_epoch: int,
    ) -> None:
        """Persist the immutable request binding before destructive work."""

        now = int(self._clock_ms())
        row = SpeechPrivacyLifecycleDB(
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
            scope_digest=scope_digest,
            evidence_digest=evidence_digest,
            phase=phase,
            revocation_epoch=revocation_epoch,
            safe_state=SAFE_STATE_BY_PHASE[phase],
            local_fenced=False,
            key_destroyed=False,
            remote_state="pending",
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
                    select(SpeechPrivacyLifecycleDB).where(
                        SpeechPrivacyLifecycleDB.tenant_id == self._principal.tenant_id,
                        SpeechPrivacyLifecycleDB.owner_subject == self._principal.subject,
                        SpeechPrivacyLifecycleDB.evidence_digest == evidence_digest,
                    )
                ).one()
            _same_lifecycle(_tombstone(existing), _tombstone(row))

    def put_once(self, value: SpeechPrivacyTombstone) -> tuple[SpeechPrivacyTombstone, bool]:
        previous = self.get(value.evidence_digest)
        if previous is not None:
            _same_lifecycle(previous, value)
            return previous, False
        now = int(self._clock_ms())
        with Session(engine) as session:
            reserved = session.exec(
                select(SpeechPrivacyLifecycleDB)
                .where(
                    SpeechPrivacyLifecycleDB.tenant_id == self._principal.tenant_id,
                    SpeechPrivacyLifecycleDB.owner_subject == self._principal.subject,
                    SpeechPrivacyLifecycleDB.evidence_digest == value.evidence_digest,
                )
                .with_for_update()
            ).first()
            if reserved is not None:
                current = _tombstone(reserved)
                _same_lifecycle(current, value)
                if current.local_fenced and current.key_destroyed:
                    return current, False
                if current.local_fenced or current.key_destroyed:
                    raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_partial_state_invalid")
                reserved.local_fenced = value.local_fenced
                reserved.key_destroyed = value.key_destroyed
                reserved.remote_state = value.remote_state
                reserved.remote_request_digest = value.remote_request_digest
                reserved.remote_ack_digest = value.remote_ack_digest
                reserved.updated_at_ms = now
                session.add(reserved)
                session.commit()
                return value, True
        row = SpeechPrivacyLifecycleDB(
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
            **value.public(),
            created_at_ms=now,
            updated_at_ms=now,
        )
        try:
            with Session(engine) as session:
                session.add(row)
                session.commit()
        except IntegrityError:
            previous = self.get(value.evidence_digest)
            if previous is None:
                raise
            _same_lifecycle(previous, value)
            return previous, False
        return value, True

    def replace(self, value: SpeechPrivacyTombstone) -> SpeechPrivacyTombstone:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPrivacyLifecycleDB)
                .where(
                    SpeechPrivacyLifecycleDB.tenant_id == self._principal.tenant_id,
                    SpeechPrivacyLifecycleDB.owner_subject == self._principal.subject,
                    SpeechPrivacyLifecycleDB.evidence_digest == value.evidence_digest,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_missing")
            current = _tombstone(row)
            _same_lifecycle(current, value)
            if current.remote_ack_digest is not None and current.remote_ack_digest != value.remote_ack_digest:
                raise SpeechPrivacyLifecycleError("speech_privacy_remote_ack_conflict")
            row.remote_state = value.remote_state
            row.remote_request_digest = value.remote_request_digest
            row.remote_ack_digest = value.remote_ack_digest
            row.updated_at_ms = int(self._clock_ms())
            session.add(row)
            session.commit()
        return value


class SqlSpeechPrivacyPhaseRepository:
    """Small SQL adapter for phase projections not owned by revocation service."""

    def __init__(self, principal: VoicePrincipal, *, clock_ms=None) -> None:
        self._principal = principal
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._offers = SqlSpeechEvidenceOfferRepository(clock_ms=self._clock_ms)
        self._transfers = SqlSpeechEvidenceTransferRepository(clock_ms=self._clock_ms)
        self._peer_keys = SqlSpeechEvidencePeerKeyRegistry(clock_ms=self._clock_ms)

    def assert_phase_present(self, binding: SpeechPrivacyEvidenceBinding, phase: str) -> None:
        if phase == "curation" and not self._active_curation_count(binding):
            raise SpeechPrivacyLifecycleError("speech_privacy_curation_binding_missing")
        if phase == "transfer" and not self._active_offer_ids(binding):
            raise SpeechPrivacyLifecycleError("speech_privacy_transfer_binding_missing")
        if phase == "dataset" and not self._dataset_digests(binding):
            raise SpeechPrivacyLifecycleError("speech_privacy_dataset_binding_missing")
        if phase == "reconciliation" and not self._reconciliation_job_ids(binding):
            raise SpeechPrivacyLifecycleError("speech_privacy_reconciliation_binding_missing")

    def fence_phase(
        self,
        binding: SpeechPrivacyEvidenceBinding,
        phase: str,
        result: SpeechEvidenceRevocationResult,
    ) -> None:
        if phase == "transfer":
            offer_ids = self._all_offer_ids(binding)
            self._offers.invalidate_scope(
                tenant_id=self._principal.tenant_id,
                session_id=binding.session_id,
                reason_code="speech_privacy_revoked",
            )
            for offer_id in offer_ids:
                self._transfers.invalidate_offer(
                    tenant_id=self._principal.tenant_id,
                    offer_id=offer_id,
                    reason_code="speech_privacy_revoked",
                )
            self._peer_keys.invalidate_scope(
                tenant_id=self._principal.tenant_id,
                session_id=binding.session_id,
            )
        elif phase == "dataset":
            digests = self._impact_digests(result, "manifest")
            with Session(engine) as session:
                session.exec(
                    update(SpeechDatasetManifestDB)
                    .where(
                        SpeechDatasetManifestDB.tenant_id == self._principal.tenant_id,
                        SpeechDatasetManifestDB.owner_subject == self._principal.subject,
                        SpeechDatasetManifestDB.manifest_digest.in_(digests),
                    )
                    .values(status="revoked", revocation_epoch=result.revocation_epoch)
                )
                session.commit()
        elif phase == "reconciliation":
            job_ids = self._reconciliation_job_ids(binding)
            now = int(self._clock_ms())
            with Session(engine) as session:
                session.exec(
                    update(SpeechReconciliationAttemptDB)
                    .where(
                        SpeechReconciliationAttemptDB.job_id.in_(job_ids),
                        SpeechReconciliationAttemptDB.state.in_(["running", "cancel_requested"]),
                    )
                    .values(state="fenced", finished_at_ms=now, updated_at_ms=now)
                )
                session.exec(
                    update(SpeechReconciliationJobDB)
                    .where(SpeechReconciliationJobDB.id.in_(job_ids))
                    .values(
                        state="cancelled",
                        active_attempt_id=None,
                        reason_code="speech_privacy_revoked",
                        finished_at_ms=now,
                        updated_at_ms=now,
                    )
                )
                session.commit()

    def verify_phase(
        self,
        binding: SpeechPrivacyEvidenceBinding,
        phase: str,
        result: SpeechEvidenceRevocationResult,
    ) -> None:
        with Session(engine) as session:
            evidence = session.get(SpeechEvidenceDB, binding.evidence_id)
            consent = session.get(SpeechEvidenceConsentDB, binding.consent_id)
            key = session.get(SpeechEvidenceKeyDB, binding.key_id)
            active_curation = self._active_curation_count(binding, session=session)
            active_offers = self._active_offer_ids(binding, session=session)
            active_transfers = session.exec(
                select(SpeechEvidenceTransferDB.id).where(
                    SpeechEvidenceTransferDB.tenant_id == self._principal.tenant_id,
                    SpeechEvidenceTransferDB.session_id == binding.session_id,
                    SpeechEvidenceTransferDB.state == "active",
                )
            ).all()
            reconciliation = session.exec(
                select(SpeechReconciliationJobDB.state).where(
                    SpeechReconciliationJobDB.id.in_(self._reconciliation_job_ids(binding, session=session))
                )
            ).all()
        if evidence is None or evidence.state != "revoked":
            raise SpeechPrivacyLifecycleError("speech_privacy_evidence_not_revoked")
        if consent is None or consent.state != "revoked" or int(consent.revocation_epoch) != result.revocation_epoch:
            raise SpeechPrivacyLifecycleError("speech_privacy_consent_not_revoked")
        if key is None or key.destroyed_at_ms is None or key.wrapped_dek is not None or key.wrapping_nonce is not None:
            raise SpeechPrivacyLifecycleError("speech_privacy_key_not_destroyed")
        if phase == "curation" and active_curation:
            raise SpeechPrivacyLifecycleError("speech_privacy_curation_not_fenced")
        if phase == "transfer" and (active_offers or active_transfers):
            raise SpeechPrivacyLifecycleError("speech_privacy_transfer_not_fenced")
        if phase == "dataset":
            digests = self._impact_digests(result, "manifest")
            with Session(engine) as session:
                states = session.exec(
                    select(SpeechDatasetManifestDB.status).where(
                        SpeechDatasetManifestDB.tenant_id == self._principal.tenant_id,
                        SpeechDatasetManifestDB.owner_subject == self._principal.subject,
                        SpeechDatasetManifestDB.manifest_digest.in_(digests),
                    )
                ).all()
            if not states or any(state != "revoked" for state in states):
                raise SpeechPrivacyLifecycleError("speech_privacy_dataset_not_fenced")
        if phase == "reconciliation" and (not reconciliation or any(state != "cancelled" for state in reconciliation)):
            raise SpeechPrivacyLifecycleError("speech_privacy_reconciliation_not_fenced")
        required_kind = {
            "training": "job",
            "evaluation": "evaluation",
            "approval": "adapter",
            "inference": "adapter",
        }.get(phase)
        if required_kind is not None and not self._impact_digests(result, required_kind):
            raise SpeechPrivacyLifecycleError(f"speech_privacy_{phase}_binding_missing")
        if phase == "training":
            job_digests = self._impact_digests(result, "job")
            with Session(engine) as session:
                jobs = session.exec(
                    select(MlInternTrainingJobDB.status, MlInternTrainingJobDB.error_code).where(
                        MlInternTrainingJobDB.tenant_id == self._principal.tenant_id,
                        MlInternTrainingJobDB.owner_subject == self._principal.subject,
                        MlInternTrainingJobDB.request_digest.in_(job_digests),
                    )
                ).all()
            if not jobs or any(
                status not in {"cancelled", "cancel_requested"} or reason != "speech_evidence_revoked"
                for status, reason in jobs
            ):
                raise SpeechPrivacyLifecycleError("speech_privacy_training_not_fenced")
        if phase in {"approval", "inference"}:
            adapter_digests = self._impact_digests(result, "adapter")
            with Session(engine) as session:
                states = session.exec(
                    select(MlInternSpeechAdapterDB.status).where(
                        MlInternSpeechAdapterDB.tenant_id == self._principal.tenant_id,
                        MlInternSpeechAdapterDB.owner_subject == self._principal.subject,
                        MlInternSpeechAdapterDB.artifact_sha256.in_(adapter_digests),
                    )
                ).all()
            if not states or any(state != "revoked" for state in states):
                raise SpeechPrivacyLifecycleError(f"speech_privacy_{phase}_not_fenced")

    def _active_curation_count(self, binding: SpeechPrivacyEvidenceBinding, *, session=None) -> int:
        if session is not None:
            return len(
                session.exec(
                    select(SpeechCurationTaskDB.id).where(
                        SpeechCurationTaskDB.tenant_id == self._principal.tenant_id,
                        SpeechCurationTaskDB.owner_subject == self._principal.subject,
                        SpeechCurationTaskDB.consent_id == binding.consent_id,
                        SpeechCurationTaskDB.state.in_(_ACTIVE_CURATION_STATES),
                    )
                ).all()
            )
        with Session(engine) as current:
            return self._active_curation_count(binding, session=current)

    def _active_offer_ids(self, binding: SpeechPrivacyEvidenceBinding, *, session=None) -> tuple[str, ...]:
        if session is not None:
            return tuple(
                str(value)
                for value in session.exec(
                    select(SpeechEvidenceOfferDB.offer_id).where(
                        SpeechEvidenceOfferDB.tenant_id == self._principal.tenant_id,
                        SpeechEvidenceOfferDB.session_id == binding.session_id,
                        SpeechEvidenceOfferDB.pair_id == binding.pair_id,
                        SpeechEvidenceOfferDB.state.in_(["proposed", "accepted"]),
                    )
                ).all()
            )
        with Session(engine) as current:
            return self._active_offer_ids(binding, session=current)

    def _all_offer_ids(self, binding: SpeechPrivacyEvidenceBinding) -> tuple[str, ...]:
        with Session(engine) as session:
            return tuple(
                str(value)
                for value in session.exec(
                    select(SpeechEvidenceOfferDB.offer_id).where(
                        SpeechEvidenceOfferDB.tenant_id == self._principal.tenant_id,
                        SpeechEvidenceOfferDB.session_id == binding.session_id,
                        SpeechEvidenceOfferDB.pair_id == binding.pair_id,
                    )
                ).all()
            )

    def _dataset_digests(self, binding: SpeechPrivacyEvidenceBinding) -> tuple[str, ...]:
        page = get_ml_intern_speech_lineage_service().forward(
            self._principal,
            root_kind="evidence",
            root_digest=binding.evidence_digest,
            limit=200,
        )
        digests = tuple(str(node["digest"]) for node in page.nodes if node["kind"] == "manifest")
        if not digests:
            return ()
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechDatasetManifestDB.manifest_digest).where(
                    SpeechDatasetManifestDB.tenant_id == self._principal.tenant_id,
                    SpeechDatasetManifestDB.owner_subject == self._principal.subject,
                    SpeechDatasetManifestDB.manifest_digest.in_(digests),
                )
            ).all()
        return tuple(str(value) for value in rows)

    def _reconciliation_job_ids(
        self,
        binding: SpeechPrivacyEvidenceBinding,
        *,
        session=None,
    ) -> tuple[str, ...]:
        page = get_ml_intern_speech_lineage_service().forward(
            self._principal,
            root_kind="evidence",
            root_digest=binding.evidence_digest,
            limit=200,
        )
        digests = tuple(str(node["digest"]) for node in page.nodes if node["kind"] == "reconciliation")
        if not digests:
            return ()
        if session is not None:
            return tuple(
                str(value)
                for value in session.exec(
                    select(SpeechReconciliationJobDB.id).where(
                        SpeechReconciliationJobDB.tenant_id == self._principal.tenant_id,
                        SpeechReconciliationJobDB.owner_subject == self._principal.subject,
                        SpeechReconciliationJobDB.consent_id == binding.consent_id,
                        SpeechReconciliationJobDB.request_digest.in_(digests),
                    )
                ).all()
            )
        with Session(engine) as current:
            return self._reconciliation_job_ids(binding, session=current)

    @staticmethod
    def _impact_digests(result: SpeechEvidenceRevocationResult, kind: str) -> tuple[str, ...]:
        return tuple(str(node["digest"]) for node in result.impacted if node["kind"] == kind)


class ProductionSpeechPrivacyFencePort:
    """Explicit eleven-phase adapter over the real Hub revocation path."""

    def __init__(
        self,
        principal: VoicePrincipal,
        *,
        resolver: SqlSpeechPrivacyBindingResolver,
        phases: SqlSpeechPrivacyPhaseRepository,
        revocations: SpeechEvidenceRevocationService,
        tombstones: SqlSpeechPrivacyTombstoneRepository,
    ) -> None:
        self._principal = principal
        self._resolver = resolver
        self._phases = phases
        self._revocations = revocations
        self._tombstones = tombstones
        self._handlers = {
            "capture": self._fence_capture,
            "buffer": self._fence_buffer,
            "transfer": self._fence_transfer,
            "quarantine": self._fence_quarantine,
            "curation": self._fence_curation,
            "dataset": self._fence_dataset,
            "reconciliation": self._fence_reconciliation,
            "training": self._fence_training,
            "evaluation": self._fence_evaluation,
            "approval": self._fence_approval,
            "inference": self._fence_inference,
        }
        if frozenset(self._handlers) != SPEECH_DATA_PHASES:
            raise SpeechPrivacyLifecycleError("speech_privacy_product_phase_wiring_incomplete")

    @property
    def phases(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def fence(
        self,
        *,
        scope_digest: str,
        evidence_digest: str,
        phase: str,
        revocation_epoch: int,
    ) -> bool:
        handler = self._handlers.get(phase)
        if handler is None:
            raise SpeechPrivacyLifecycleError("speech_privacy_product_phase_wiring_incomplete")
        binding = self._resolver.resolve(scope_digest=scope_digest, evidence_digest=evidence_digest)
        expected_epoch = binding.current_revocation_epoch + (binding.consent_state == "active")
        if revocation_epoch != expected_epoch:
            raise SpeechPrivacyLifecycleError("speech_privacy_revocation_epoch_stale")
        if binding.consent_state == "active":
            self._phases.assert_phase_present(binding, phase)
        self._tombstones.reserve(
            scope_digest=scope_digest,
            evidence_digest=evidence_digest,
            phase=phase,
            revocation_epoch=revocation_epoch,
        )
        result = self._revocations.revoke(
            self._principal,
            binding.evidence_id,
            expected_consent_version=binding.consent_version,
            reason_code=f"speech_privacy_{phase}",
            contributor_id=binding.contributor_id,
        )
        if (
            result.evidence_digest != evidence_digest
            or result.revocation_epoch != revocation_epoch
            or not result.key_destroyed
            or result.unresolved
        ):
            raise SpeechPrivacyLifecycleError("speech_privacy_transitive_fence_incomplete")
        return handler(binding, result)

    def _finish(
        self,
        binding: SpeechPrivacyEvidenceBinding,
        phase: str,
        result: SpeechEvidenceRevocationResult,
    ) -> bool:
        self._phases.fence_phase(binding, phase, result)
        self._phases.verify_phase(binding, phase, result)
        return True

    def _fence_capture(self, binding, result) -> bool:
        return self._finish(binding, "capture", result)

    def _fence_buffer(self, binding, result) -> bool:
        return self._finish(binding, "buffer", result)

    def _fence_transfer(self, binding, result) -> bool:
        return self._finish(binding, "transfer", result)

    def _fence_quarantine(self, binding, result) -> bool:
        return self._finish(binding, "quarantine", result)

    def _fence_curation(self, binding, result) -> bool:
        return self._finish(binding, "curation", result)

    def _fence_dataset(self, binding, result) -> bool:
        return self._finish(binding, "dataset", result)

    def _fence_reconciliation(self, binding, result) -> bool:
        return self._finish(binding, "reconciliation", result)

    def _fence_training(self, binding, result) -> bool:
        return self._finish(binding, "training", result)

    def _fence_evaluation(self, binding, result) -> bool:
        return self._finish(binding, "evaluation", result)

    def _fence_approval(self, binding, result) -> bool:
        return self._finish(binding, "approval", result)

    def _fence_inference(self, binding, result) -> bool:
        return self._finish(binding, "inference", result)


class ProductionSpeechPrivacyKeyPort:
    def __init__(
        self,
        principal: VoicePrincipal,
        *,
        resolver: SqlSpeechPrivacyBindingResolver,
        encryption: SpeechEvidenceEncryptionPort,
    ) -> None:
        self._principal = principal
        self._resolver = resolver
        self._encryption = encryption

    def destroy(
        self,
        *,
        scope_digest: str,
        evidence_digest: str,
        revocation_epoch: int,
    ) -> bool:
        binding = self._resolver.resolve(scope_digest=scope_digest, evidence_digest=evidence_digest)
        if binding.current_revocation_epoch != revocation_epoch:
            raise SpeechPrivacyLifecycleError("speech_privacy_key_epoch_stale")
        with Session(engine) as session:
            key = session.get(SpeechEvidenceKeyDB, binding.key_id)
        if key is None:
            raise SpeechPrivacyLifecycleError("speech_privacy_key_binding_missing")
        if key.destroyed_at_ms is None:
            self._encryption.destroy(binding.key_id, tenant_id=self._principal.tenant_id)
        with Session(engine) as session:
            key = session.get(SpeechEvidenceKeyDB, binding.key_id)
            return bool(
                key is not None
                and key.destroyed_at_ms is not None
                and key.wrapped_dek is None
                and key.wrapping_nonce is None
            )


class ProductionSpeechPrivacyRemotePort:
    def __init__(self, principal: VoicePrincipal, *, revocations: SpeechEvidenceRevocationService) -> None:
        self._principal = principal
        self._revocations = revocations

    def stage(self, *, evidence_digest: str, request_digest: str, revocation_epoch: int) -> bool:
        row = self._row(evidence_digest)
        if row is None or int(row.revocation_epoch) != revocation_epoch:
            return False
        if row.remote_state == "requested" and row.remote_request_digest == request_digest:
            return True
        self._revocations.stage_remote_request(
            self._principal,
            evidence_digest=evidence_digest,
            request_digest=request_digest,
            signature_verified=True,
        )
        row = self._row(evidence_digest)
        return bool(row is not None and row.remote_state == "requested" and row.remote_request_digest == request_digest)

    def acknowledge(
        self,
        *,
        evidence_digest: str,
        request_digest: str,
        ack_digest: str,
        signature_verified: bool,
    ) -> bool:
        row = self._row(evidence_digest)
        if row is not None and row.remote_state == "acknowledged":
            return row.remote_request_digest == request_digest and row.remote_ack_digest == ack_digest
        self._revocations.acknowledge_remote(
            self._principal,
            evidence_digest=evidence_digest,
            request_digest=request_digest,
            ack_digest=ack_digest,
            signature_verified=signature_verified,
        )
        row = self._row(evidence_digest)
        return bool(
            row is not None
            and row.remote_state == "acknowledged"
            and row.remote_request_digest == request_digest
            and row.remote_ack_digest == ack_digest
        )

    def _row(self, evidence_digest: str) -> SpeechEvidenceRevocationDB | None:
        with Session(engine) as session:
            return session.exec(
                select(SpeechEvidenceRevocationDB).where(
                    SpeechEvidenceRevocationDB.tenant_id == self._principal.tenant_id,
                    SpeechEvidenceRevocationDB.owner_subject == self._principal.subject,
                    SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                )
            ).first()


def build_speech_privacy_lifecycle_service(principal: VoicePrincipal) -> SpeechPrivacyLifecycleService:
    """Build the only production lifecycle composition for one Hub principal."""

    resolver = SqlSpeechPrivacyBindingResolver(principal)
    revocations = get_speech_evidence_revocation_service()
    tombstones = SqlSpeechPrivacyTombstoneRepository(principal)
    fences = ProductionSpeechPrivacyFencePort(
        principal,
        resolver=resolver,
        phases=SqlSpeechPrivacyPhaseRepository(principal),
        revocations=revocations,
        tombstones=tombstones,
    )
    if fences.phases != SPEECH_DATA_PHASES:
        raise SpeechPrivacyLifecycleError("speech_privacy_product_phase_wiring_incomplete")
    return SpeechPrivacyLifecycleService(
        fences=fences,
        keys=ProductionSpeechPrivacyKeyPort(
            principal,
            resolver=resolver,
            encryption=get_speech_evidence_encryption_port(),
        ),
        remote=ProductionSpeechPrivacyRemotePort(principal, revocations=revocations),
        tombstones=tombstones,
    )


def assert_speech_privacy_production_composition() -> None:
    """Static gate hook: exact domain/registry match, no claimed phantom phase."""

    if PRODUCTION_SPEECH_PRIVACY_PHASES != SPEECH_DATA_PHASES or len(SPEECH_DATA_PHASES) != 11:
        raise SpeechPrivacyLifecycleError("speech_privacy_product_phase_wiring_incomplete")
    service = build_speech_privacy_lifecycle_service(
        VoicePrincipal("speech-privacy-gate", "hub-composition-probe")
    )
    if not isinstance(service, SpeechPrivacyLifecycleService):
        raise SpeechPrivacyLifecycleError("speech_privacy_product_composition_missing")


def _tombstone(row: SpeechPrivacyLifecycleDB) -> SpeechPrivacyTombstone:
    return SpeechPrivacyTombstone(
        scope_digest=str(row.scope_digest),
        evidence_digest=str(row.evidence_digest),
        phase=str(row.phase),
        revocation_epoch=int(row.revocation_epoch),
        safe_state=str(row.safe_state),
        local_fenced=bool(row.local_fenced),
        key_destroyed=bool(row.key_destroyed),
        remote_state=str(row.remote_state),
        remote_request_digest=row.remote_request_digest,
        remote_ack_digest=row.remote_ack_digest,
    )


def _same_lifecycle(previous: SpeechPrivacyTombstone, value: SpeechPrivacyTombstone) -> None:
    if (
        previous.scope_digest != value.scope_digest
        or previous.evidence_digest != value.evidence_digest
        or previous.phase != value.phase
        or previous.revocation_epoch != value.revocation_epoch
        or previous.safe_state != value.safe_state
    ):
        raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_conflict")


__all__ = [
    "PRODUCTION_SPEECH_PRIVACY_PHASES",
    "ProductionSpeechPrivacyFencePort",
    "SqlSpeechPrivacyTombstoneRepository",
    "assert_speech_privacy_production_composition",
    "build_speech_privacy_lifecycle_service",
]
