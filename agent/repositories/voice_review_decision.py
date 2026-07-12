from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session, select, update

from agent.database import engine
from agent.db_models import VoiceGovernanceIdempotencyDB, VoiceResultArtifactDB, VoiceReviewDB
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal


class VoiceReviewDecisionRepository:
    """Persist review mutations together with their idempotency outcome.

    The service acquires the scoped idempotency lease before calling this
    repository.  The review side effect and lease completion then commit in one
    database transaction, so a process crash cannot leave an applied mutation
    behind an abandoned claim.
    """

    def create(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        session_id: str | None,
        result_ref: str,
        candidate_ids: list[str],
        idempotency_record_id: str,
        idempotency_lease_token: float,
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
            self._complete_idempotency_claim(
                session,
                principal,
                record_id=idempotency_record_id,
                lease_token=idempotency_lease_token,
                result_metadata={"review_id": review.id},
            )
            session.commit()
            session.refresh(review)
            return review

    def decide(
        self,
        principal: VoicePrincipal,
        *,
        review_id: str,
        expected_version: int,
        state: str,
        selected_candidate_id: str | None,
        correction_ciphertext: str | None,
        artifact: dict[str, Any],
        idempotency_record_id: str,
        idempotency_lease_token: float,
    ) -> tuple[VoiceReviewDB, VoiceResultArtifactDB]:
        with Session(engine) as session:
            review = session.exec(
                select(VoiceReviewDB)
                .where(
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
            if review.state != "pending" or review.decision_artifact_id:
                raise VoiceGovernanceError(
                    code="voice_review.already_decided",
                    message="voice review already has a terminal decision",
                    status_code=409,
                )
            decision_artifact = VoiceResultArtifactDB(
                id=str(artifact["id"]),
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                profile_id=review.profile_id,
                artifact_kind="review_decision",
                parent_artifact_id=review.result_ref,
                request_hash=str(artifact["request_hash"]),
                payload_ciphertext=str(artifact["payload_ciphertext"]),
                payload_digest=str(artifact["payload_digest"]),
                candidate_ids=list(artifact.get("candidate_ids") or []),
                expires_at=float(artifact["expires_at"]),
            )
            transitioned = session.exec(
                update(VoiceReviewDB)
                .where(
                    VoiceReviewDB.id == review_id,
                    VoiceReviewDB.tenant_id == principal.tenant_id,
                    VoiceReviewDB.owner_subject == principal.subject,
                    VoiceReviewDB.version == expected_version,
                    VoiceReviewDB.state == "pending",
                    VoiceReviewDB.decision_artifact_id.is_(None),
                )
                .values(
                    state=state,
                    selected_candidate_id=selected_candidate_id,
                    correction_ciphertext=correction_ciphertext,
                    decision_artifact_id=decision_artifact.id,
                    version=expected_version + 1,
                    updated_at=time.time(),
                )
            )
            if transitioned.rowcount != 1:
                session.rollback()
                current = session.exec(
                    select(VoiceReviewDB).where(
                        VoiceReviewDB.id == review_id,
                        VoiceReviewDB.tenant_id == principal.tenant_id,
                        VoiceReviewDB.owner_subject == principal.subject,
                    )
                ).first()
                if current is None:
                    raise VoiceGovernanceError(
                        code="voice_review.not_found",
                        message="voice review not found",
                        status_code=404,
                    )
                if current.version != expected_version:
                    raise VoiceGovernanceError(
                        code="voice_review.version_conflict",
                        message="voice review version does not match",
                        status_code=409,
                    )
                raise VoiceGovernanceError(
                    code="voice_review.already_decided",
                    message="voice review already has a terminal decision",
                    status_code=409,
                )
            session.add(decision_artifact)
            self._complete_idempotency_claim(
                session,
                principal,
                record_id=idempotency_record_id,
                lease_token=idempotency_lease_token,
                result_metadata={
                    "review_id": review_id,
                    "decision_artifact_ref": decision_artifact.id,
                },
            )
            session.commit()
            review = session.get(VoiceReviewDB, review_id)
            if review is None:
                raise RuntimeError("voice review disappeared after atomic decision")
            session.refresh(review)
            session.refresh(decision_artifact)
            return review, decision_artifact

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
                message="idempotency claim ownership changed before review mutation",
                status_code=409,
            )
