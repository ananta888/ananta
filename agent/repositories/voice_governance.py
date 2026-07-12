from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select, update

from agent.database import engine
from agent.db_models import (
    VoiceConsentDB,
    VoiceFeedbackDB,
    VoiceGovernanceIdempotencyDB,
    VoicePersonalizationProfileDB,
    VoiceReviewDB,
)
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal

_IDEMPOTENCY_LEASE_SECONDS = 600


class VoiceConsentRepository:
    def get(self, principal: VoicePrincipal, profile_id: str) -> VoiceConsentDB | None:
        with Session(engine) as session:
            return session.exec(
                select(VoiceConsentDB).where(
                    VoiceConsentDB.tenant_id == principal.tenant_id,
                    VoiceConsentDB.owner_subject == principal.subject,
                    VoiceConsentDB.profile_id == profile_id,
                )
            ).first()

    def set_state(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        granted: bool,
        categories: list[str],
        retention_days: int,
        idempotency_record_id: str,
        idempotency_lease_token: float,
        result_builder: Callable[[VoiceConsentDB], dict[str, Any]],
    ) -> tuple[VoiceConsentDB, dict[str, Any]]:
        """Commit the consent mutation and fenced claim outcome atomically."""

        now = time.time()
        with Session(engine) as session:
            consent = session.exec(
                select(VoiceConsentDB).where(
                    VoiceConsentDB.tenant_id == principal.tenant_id,
                    VoiceConsentDB.owner_subject == principal.subject,
                    VoiceConsentDB.profile_id == profile_id,
                )
            ).first()
            if consent is None:
                consent = VoiceConsentDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    profile_id=profile_id,
                )
            else:
                consent.version += 1
            consent.granted = granted
            consent.categories = list(categories)
            consent.retention_days = retention_days
            consent.updated_at = now
            if granted:
                consent.granted_at = now
                consent.revoked_at = None
            else:
                consent.revoked_at = now
            session.add(consent)
            session.flush()
            result = dict(result_builder(consent))
            completed = session.exec(
                update(VoiceGovernanceIdempotencyDB)
                .where(
                    VoiceGovernanceIdempotencyDB.id == idempotency_record_id,
                    VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                    VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                    VoiceGovernanceIdempotencyDB.state == "pending",
                    VoiceGovernanceIdempotencyDB.lease_expires_at
                    == idempotency_lease_token,
                )
                .values(
                    state="completed",
                    lease_expires_at=now,
                    result_metadata={"consent": result},
                    updated_at=now,
                )
            )
            if completed.rowcount != 1:
                session.rollback()
                raise VoiceGovernanceError(
                    code="voice_governance.stale_idempotency_claim",
                    message="idempotency claim ownership changed before consent mutation",
                    status_code=409,
                )
            session.commit()
            session.refresh(consent)
            return consent, result


class VoiceReviewRepository:
    def create(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        session_id: str | None,
        result_ref: str,
        candidate_ids: list[str],
    ) -> VoiceReviewDB:
        with Session(engine) as session:
            review = VoiceReviewDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                profile_id=profile_id,
                session_id=session_id,
                result_ref=result_ref,
                candidate_ids=list(candidate_ids),
            )
            session.add(review)
            session.commit()
            session.refresh(review)
            return review

    def get(self, principal: VoicePrincipal, review_id: str) -> VoiceReviewDB | None:
        with Session(engine) as session:
            return session.exec(
                select(VoiceReviewDB).where(
                    VoiceReviewDB.id == review_id,
                    VoiceReviewDB.tenant_id == principal.tenant_id,
                    VoiceReviewDB.owner_subject == principal.subject,
                )
            ).first()

    def decide(
        self,
        principal: VoicePrincipal,
        *,
        review_id: str,
        expected_version: int,
        state: str,
        selected_candidate_id: str | None,
        correction_ciphertext: str | None,
    ) -> VoiceReviewDB:
        with Session(engine) as session:
            review = session.exec(
                select(VoiceReviewDB).where(
                    VoiceReviewDB.id == review_id,
                    VoiceReviewDB.tenant_id == principal.tenant_id,
                    VoiceReviewDB.owner_subject == principal.subject,
                )
            ).first()
            if review is None:
                raise VoiceGovernanceError(
                    code="voice_review.not_found",
                    message="voice review not found",
                    status_code=404,
                )
            if review.version != expected_version:
                raise VoiceGovernanceError(
                    code="voice_review.version_conflict",
                    message="voice review version does not match",
                    status_code=409,
                )
            if review.state != "pending":
                raise VoiceGovernanceError(
                    code="voice_review.already_decided",
                    message="voice review already has a terminal decision",
                    status_code=409,
                )
            review.state = state
            review.selected_candidate_id = selected_candidate_id
            review.correction_ciphertext = correction_ciphertext
            review.version += 1
            review.updated_at = time.time()
            session.add(review)
            session.commit()
            session.refresh(review)
            return review


class VoicePersonalizationRepository:
    def purge_all_expired(self, *, now: float | None = None) -> int:
        """Physically remove expired feedback across tenants for Hub housekeeping."""

        cutoff = float(now if now is not None else time.time())
        with Session(engine) as session:
            rows = session.exec(select(VoiceFeedbackDB.id).where(VoiceFeedbackDB.expires_at <= cutoff)).all()
            session.exec(delete(VoiceFeedbackDB).where(VoiceFeedbackDB.expires_at <= cutoff))
            session.commit()
            return len(rows)

    def get_feedback(self, principal: VoicePrincipal, feedback_id: str) -> VoiceFeedbackDB | None:
        with Session(engine) as session:
            self._purge_expired(session, principal)
            session.commit()
            return session.exec(
                select(VoiceFeedbackDB).where(
                    VoiceFeedbackDB.id == feedback_id,
                    VoiceFeedbackDB.tenant_id == principal.tenant_id,
                    VoiceFeedbackDB.owner_subject == principal.subject,
                    VoiceFeedbackDB.active.is_(True),
                    VoiceFeedbackDB.expires_at > time.time(),
                )
            ).first()

    def list_feedback(self, principal: VoicePrincipal, profile_id: str) -> list[VoiceFeedbackDB]:
        with Session(engine) as session:
            self._purge_expired(session, principal, profile_id=profile_id)
            session.commit()
            return list(
                session.exec(
                    select(VoiceFeedbackDB)
                    .where(
                        VoiceFeedbackDB.tenant_id == principal.tenant_id,
                        VoiceFeedbackDB.owner_subject == principal.subject,
                        VoiceFeedbackDB.profile_id == profile_id,
                        VoiceFeedbackDB.active.is_(True),
                        VoiceFeedbackDB.expires_at > time.time(),
                    )
                    .order_by(VoiceFeedbackDB.created_at.asc())
                ).all()
            )

    @staticmethod
    def _purge_expired(
        session: Session,
        principal: VoicePrincipal,
        *,
        profile_id: str | None = None,
    ) -> None:
        predicate = (
            (VoiceFeedbackDB.tenant_id == principal.tenant_id)
            & (VoiceFeedbackDB.owner_subject == principal.subject)
            & (VoiceFeedbackDB.expires_at <= time.time())
        )
        if profile_id is not None:
            predicate &= VoiceFeedbackDB.profile_id == profile_id
        session.exec(delete(VoiceFeedbackDB).where(predicate))

    def create_feedback(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        consent_id: str,
        consent_version: int,
        source_review_id: str,
        kind: str,
        source_ciphertext: str | None,
        target_ciphertext: str | None,
        feedback_metadata: dict[str, Any],
    ) -> tuple[VoiceFeedbackDB, VoicePersonalizationProfileDB]:
        feedback, profile = self.create_feedback_many(
            principal,
            profile_id=profile_id,
            consent_id=consent_id,
            consent_version=consent_version,
            items=[
                {
                    "source_review_id": source_review_id,
                    "kind": kind,
                    "source_ciphertext": source_ciphertext,
                    "target_ciphertext": target_ciphertext,
                    "feedback_metadata": feedback_metadata,
                }
            ],
        )
        return feedback[0], profile

    def create_feedback_many(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        consent_id: str,
        consent_version: int,
        items: list[dict[str, Any]],
    ) -> tuple[list[VoiceFeedbackDB], VoicePersonalizationProfileDB]:
        """Persist a validated feedback batch and profile version atomically."""

        if not items:
            raise ValueError("voice feedback batch must not be empty")
        keys = [
            (str(item["source_review_id"]), str(item["kind"]))
            for item in items
        ]
        if len(set(keys)) != len(keys):
            raise VoiceGovernanceError(
                code="voice_personalization.duplicate_import_item",
                message="feedback batch contains duplicate source items",
                status_code=422,
            )
        now = time.time()
        with Session(engine) as session:
            consent = session.exec(
                select(VoiceConsentDB)
                .where(
                    VoiceConsentDB.id == consent_id,
                    VoiceConsentDB.tenant_id == principal.tenant_id,
                    VoiceConsentDB.owner_subject == principal.subject,
                    VoiceConsentDB.profile_id == profile_id,
                )
                .with_for_update()
            ).first()
            if consent is None or not consent.granted or consent.version != consent_version:
                raise VoiceGovernanceError(
                    code="voice_consent.changed",
                    message="voice personalization consent changed before feedback was stored",
                    status_code=409,
                )
            profile = session.exec(
                select(VoicePersonalizationProfileDB).where(
                    VoicePersonalizationProfileDB.tenant_id == principal.tenant_id,
                    VoicePersonalizationProfileDB.owner_subject == principal.subject,
                    VoicePersonalizationProfileDB.profile_id == profile_id,
                )
            ).first()
            existing_rows = session.exec(
                select(VoiceFeedbackDB).where(
                    VoiceFeedbackDB.tenant_id == principal.tenant_id,
                    VoiceFeedbackDB.owner_subject == principal.subject,
                    VoiceFeedbackDB.profile_id == profile_id,
                )
            ).all()
            existing_by_key = {
                (row.source_review_id, row.kind): row
                for row in existing_rows
                if (row.source_review_id, row.kind) in set(keys)
            }
            missing_count = sum(key not in existing_by_key for key in keys)
            if profile is None:
                profile = VoicePersonalizationProfileDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    profile_id=profile_id,
                    version=max(1, missing_count),
                )
            elif missing_count:
                profile.version += missing_count
                profile.updated_at = now
            feedback: list[VoiceFeedbackDB] = []
            for key, item in zip(keys, items, strict=True):
                record = existing_by_key.get(key)
                if record is None:
                    record = VoiceFeedbackDB(
                        tenant_id=principal.tenant_id,
                        owner_subject=principal.subject,
                        profile_id=profile_id,
                        consent_id=consent_id,
                        consent_version=consent_version,
                        source_review_id=key[0],
                        kind=key[1],
                        source_ciphertext=item.get("source_ciphertext"),
                        target_ciphertext=item.get("target_ciphertext"),
                        feedback_metadata=dict(item.get("feedback_metadata") or {}),
                        expires_at=now + max(1, min(int(consent.retention_days), 3650)) * 86_400,
                    )
                    session.add(record)
                feedback.append(record)
            session.add(profile)
            session.commit()
            session.refresh(profile)
            for record in feedback:
                session.refresh(record)
            return feedback, profile

    def profile_version(self, principal: VoicePrincipal, profile_id: str) -> int:
        with Session(engine) as session:
            profile = session.exec(
                select(VoicePersonalizationProfileDB).where(
                    VoicePersonalizationProfileDB.tenant_id == principal.tenant_id,
                    VoicePersonalizationProfileDB.owner_subject == principal.subject,
                    VoicePersonalizationProfileDB.profile_id == profile_id,
                )
            ).first()
            return int(profile.version) if profile is not None else 0

    def reset(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        idempotency_record_id: str,
        idempotency_lease_token: float,
        result_builder: Callable[[int, int], dict[str, Any]],
    ) -> tuple[int, int, dict[str, Any]]:
        """Delete personalization and complete its fenced claim atomically."""

        now = time.time()
        with Session(engine) as session:
            rows = session.exec(
                select(VoiceFeedbackDB).where(
                    VoiceFeedbackDB.tenant_id == principal.tenant_id,
                    VoiceFeedbackDB.owner_subject == principal.subject,
                    VoiceFeedbackDB.profile_id == profile_id,
                )
            ).all()
            deleted_count = len(rows)
            session.exec(
                delete(VoiceFeedbackDB).where(
                    VoiceFeedbackDB.tenant_id == principal.tenant_id,
                    VoiceFeedbackDB.owner_subject == principal.subject,
                    VoiceFeedbackDB.profile_id == profile_id,
                )
            )
            session.exec(
                delete(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                    VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                    VoiceGovernanceIdempotencyDB.operation == f"voice_personalization.feedback:{profile_id}",
                )
            )
            profile = session.exec(
                select(VoicePersonalizationProfileDB).where(
                    VoicePersonalizationProfileDB.tenant_id == principal.tenant_id,
                    VoicePersonalizationProfileDB.owner_subject == principal.subject,
                    VoicePersonalizationProfileDB.profile_id == profile_id,
                )
            ).first()
            if profile is None:
                profile = VoicePersonalizationProfileDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    profile_id=profile_id,
                )
            else:
                profile.version += 1
                profile.updated_at = now
            session.add(profile)
            session.flush()
            profile_version = int(profile.version)
            result = dict(result_builder(deleted_count, profile_version))
            self._complete_idempotency_claim(
                session,
                principal,
                record_id=idempotency_record_id,
                lease_token=idempotency_lease_token,
                result_metadata=result,
            )
            session.commit()
            session.refresh(profile)
            return deleted_count, profile_version, result

    @staticmethod
    def _complete_idempotency_claim(
        session: Session,
        principal: VoicePrincipal,
        *,
        record_id: str,
        lease_token: float,
        result_metadata: dict[str, Any],
    ) -> None:
        now = time.time()
        completed = session.exec(
            update(VoiceGovernanceIdempotencyDB)
            .where(
                VoiceGovernanceIdempotencyDB.id == record_id,
                VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                VoiceGovernanceIdempotencyDB.state == "pending",
                VoiceGovernanceIdempotencyDB.lease_expires_at == lease_token,
            )
            .values(
                state="completed",
                lease_expires_at=now,
                result_metadata=dict(result_metadata),
                updated_at=now,
            )
        )
        if completed.rowcount != 1:
            session.rollback()
            raise VoiceGovernanceError(
                code="voice_governance.stale_idempotency_claim",
                message="idempotency claim ownership changed before personalization reset",
                status_code=409,
            )


class VoiceIdempotencyRepository:
    def claim(
        self,
        principal: VoicePrincipal,
        *,
        operation: str,
        idempotency_key: str,
        legacy_idempotency_key: str | None = None,
        request_hash: str,
        expires_at: float,
    ) -> tuple[VoiceGovernanceIdempotencyDB, bool]:
        with Session(engine) as session:
            existing = self._find(session, principal, operation, idempotency_key)
            if existing is None and legacy_idempotency_key:
                existing = self._find(
                    session,
                    principal,
                    operation,
                    legacy_idempotency_key,
                )
                if existing is not None:
                    existing.idempotency_key = idempotency_key
                    existing.updated_at = time.time()
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
            if existing is not None and existing.expires_at <= time.time():
                session.exec(
                    delete(VoiceGovernanceIdempotencyDB).where(
                        VoiceGovernanceIdempotencyDB.id == existing.id,
                        VoiceGovernanceIdempotencyDB.expires_at <= time.time(),
                    )
                )
                session.commit()
                existing = self._find(session, principal, operation, idempotency_key)
            if existing is not None:
                lease_expired = existing.state == "pending" and existing.lease_expires_at <= time.time()
                if existing.request_hash == request_hash and lease_expired:
                    now = time.time()
                    previous_lease = existing.lease_expires_at
                    next_lease = now + _IDEMPOTENCY_LEASE_SECONDS
                    reclaimed = session.exec(
                        update(VoiceGovernanceIdempotencyDB)
                        .where(
                            VoiceGovernanceIdempotencyDB.id == existing.id,
                            VoiceGovernanceIdempotencyDB.state == "pending",
                            VoiceGovernanceIdempotencyDB.request_hash == request_hash,
                            VoiceGovernanceIdempotencyDB.lease_expires_at == previous_lease,
                        )
                        .values(
                            lease_expires_at=next_lease,
                            result_metadata={},
                            updated_at=now,
                        )
                    )
                    session.commit()
                    if reclaimed.rowcount == 1:
                        record = session.get(VoiceGovernanceIdempotencyDB, existing.id)
                        if record is None:
                            raise RuntimeError("reclaimed voice idempotency row disappeared")
                        return record, True
                    current = self._find(session, principal, operation, idempotency_key)
                    if current is None:
                        raise RuntimeError("voice idempotency row disappeared during reclaim")
                    return self._validate_existing(current, request_hash), False
                return self._validate_existing(existing, request_hash), False
            record = VoiceGovernanceIdempotencyDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                lease_expires_at=time.time() + _IDEMPOTENCY_LEASE_SECONDS,
                expires_at=float(expires_at),
            )
            session.add(record)
            try:
                session.commit()
                session.refresh(record)
                return record, True
            except IntegrityError:
                session.rollback()
                existing = self._find(session, principal, operation, idempotency_key)
                if existing is None:
                    raise
                return self._validate_existing(existing, request_hash), False

    def purge_expired(self, *, now: float | None = None) -> int:
        cutoff = float(now if now is not None else time.time())
        with Session(engine) as session:
            ids = session.exec(
                select(VoiceGovernanceIdempotencyDB.id).where(
                    VoiceGovernanceIdempotencyDB.expires_at <= cutoff
                )
            ).all()
            session.exec(
                delete(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.expires_at <= cutoff
                )
            )
            session.commit()
            return len(ids)

    def invalidate_completed_operation(self, operation: str) -> tuple[dict[str, Any], ...]:
        """Remove replay metadata whose external capabilities died on restart."""

        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(VoiceGovernanceIdempotencyDB).where(
                        VoiceGovernanceIdempotencyDB.operation == operation,
                        VoiceGovernanceIdempotencyDB.state == "completed",
                    )
                ).all()
            )
            metadata = tuple(dict(row.result_metadata or {}) for row in rows)
            if rows:
                session.exec(
                    delete(VoiceGovernanceIdempotencyDB).where(
                        VoiceGovernanceIdempotencyDB.id.in_([row.id for row in rows])
                    )
                )
                session.commit()
            return metadata

    def complete(
        self,
        record_id: str,
        *,
        lease_token: float,
        result_metadata: dict[str, Any],
    ) -> None:
        with Session(engine) as session:
            now = time.time()
            completed = session.exec(
                update(VoiceGovernanceIdempotencyDB)
                .where(
                    VoiceGovernanceIdempotencyDB.id == record_id,
                    VoiceGovernanceIdempotencyDB.state == "pending",
                    VoiceGovernanceIdempotencyDB.lease_expires_at == lease_token,
                )
                .values(
                    state="completed",
                    lease_expires_at=now,
                    result_metadata=dict(result_metadata),
                    updated_at=now,
                )
            )
            session.commit()
            if completed.rowcount != 1:
                raise VoiceGovernanceError(
                    code="voice_governance.stale_idempotency_claim",
                    message="idempotency claim ownership changed before completion",
                    status_code=409,
                )

    def release(self, record_id: str, *, lease_token: float) -> None:
        with Session(engine) as session:
            session.exec(
                delete(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.id == record_id,
                    VoiceGovernanceIdempotencyDB.state == "pending",
                    VoiceGovernanceIdempotencyDB.lease_expires_at == lease_token,
                )
            )
            session.commit()

    @staticmethod
    def _find(
        session: Session,
        principal: VoicePrincipal,
        operation: str,
        idempotency_key: str,
    ) -> VoiceGovernanceIdempotencyDB | None:
        return session.exec(
            select(VoiceGovernanceIdempotencyDB).where(
                VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                VoiceGovernanceIdempotencyDB.operation == operation,
                VoiceGovernanceIdempotencyDB.idempotency_key == idempotency_key,
            )
        ).first()

    @staticmethod
    def _validate_existing(
        record: VoiceGovernanceIdempotencyDB,
        request_hash: str,
    ) -> VoiceGovernanceIdempotencyDB:
        if record.request_hash != request_hash:
            raise VoiceGovernanceError(
                code="voice_governance.idempotency_conflict",
                message="idempotency key was already used with a different payload",
                status_code=409,
            )
        if record.state != "completed":
            raise VoiceGovernanceError(
                code="voice_governance.operation_in_progress",
                message="an operation with this idempotency key is still in progress",
                status_code=409,
            )
        return record
