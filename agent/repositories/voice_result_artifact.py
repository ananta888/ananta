from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import VoiceResultArtifactDB
from agent.services.voice_governance_domain import VoicePrincipal


class VoiceResultArtifactRepository:
    def create(
        self,
        principal: VoicePrincipal,
        *,
        request_hash: str,
        profile_id: str,
        artifact_kind: str,
        parent_artifact_id: str | None,
        payload_ciphertext: str,
        payload_digest: str,
        candidate_ids: list[str],
        expires_at: float,
    ) -> VoiceResultArtifactDB:
        return self.create_many(
            principal,
            artifacts=[
                {
                    "request_hash": request_hash,
                    "profile_id": profile_id,
                    "artifact_kind": artifact_kind,
                    "parent_artifact_id": parent_artifact_id,
                    "payload_ciphertext": payload_ciphertext,
                    "payload_digest": payload_digest,
                    "candidate_ids": candidate_ids,
                    "expires_at": expires_at,
                }
            ],
        )[0]

    def create_many(
        self,
        principal: VoicePrincipal,
        *,
        artifacts: list[dict[str, Any]],
    ) -> list[VoiceResultArtifactDB]:
        """Persist a logical result bundle in one transaction."""

        if not artifacts:
            raise ValueError("voice result artifact bundle must not be empty")
        with Session(engine) as session:
            records = [
                VoiceResultArtifactDB(
                    id=str(item["id"]) if item.get("id") else None,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    profile_id=str(item["profile_id"]),
                    artifact_kind=str(item["artifact_kind"]),
                    parent_artifact_id=str(item["parent_artifact_id"]) if item.get("parent_artifact_id") else None,
                    request_hash=str(item["request_hash"]),
                    payload_ciphertext=str(item["payload_ciphertext"]),
                    payload_digest=str(item["payload_digest"]),
                    candidate_ids=list(item.get("candidate_ids") or []),
                    expires_at=float(item["expires_at"]),
                )
                if item.get("id")
                else VoiceResultArtifactDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    profile_id=str(item["profile_id"]),
                    artifact_kind=str(item["artifact_kind"]),
                    parent_artifact_id=str(item["parent_artifact_id"]) if item.get("parent_artifact_id") else None,
                    request_hash=str(item["request_hash"]),
                    payload_ciphertext=str(item["payload_ciphertext"]),
                    payload_digest=str(item["payload_digest"]),
                    candidate_ids=list(item.get("candidate_ids") or []),
                    expires_at=float(item["expires_at"]),
                )
                for item in artifacts
            ]
            session.add_all(records)
            session.commit()
            for record in records:
                session.refresh(record)
            return records

    def delete_profile(self, principal: VoicePrincipal, profile_id: str) -> int:
        with Session(engine) as session:
            query = select(VoiceResultArtifactDB.id).where(
                VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                VoiceResultArtifactDB.owner_subject == principal.subject,
                VoiceResultArtifactDB.profile_id == profile_id,
            )
            rows = session.exec(query).all()
            session.exec(
                delete(VoiceResultArtifactDB).where(
                    VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                    VoiceResultArtifactDB.owner_subject == principal.subject,
                    VoiceResultArtifactDB.profile_id == profile_id,
                )
            )
            session.commit()
            return len(rows)

    def delete_envelope(self, principal: VoicePrincipal, artifact_id: str) -> int:
        """Delete one scoped result envelope and its direct encrypted children."""

        with Session(engine) as session:
            envelope = session.exec(
                select(VoiceResultArtifactDB).where(
                    VoiceResultArtifactDB.id == artifact_id,
                    VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                    VoiceResultArtifactDB.owner_subject == principal.subject,
                    VoiceResultArtifactDB.artifact_kind == "result_envelope",
                )
            ).first()
            if envelope is None:
                return 0
            rows = session.exec(
                select(VoiceResultArtifactDB.id).where(
                    VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                    VoiceResultArtifactDB.owner_subject == principal.subject,
                    (
                        (VoiceResultArtifactDB.id == artifact_id)
                        | (VoiceResultArtifactDB.parent_artifact_id == artifact_id)
                    ),
                )
            ).all()
            session.exec(
                delete(VoiceResultArtifactDB).where(
                    VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                    VoiceResultArtifactDB.owner_subject == principal.subject,
                    (
                        (VoiceResultArtifactDB.id == artifact_id)
                        | (VoiceResultArtifactDB.parent_artifact_id == artifact_id)
                    ),
                )
            )
            session.commit()
            return len(rows)

    def delete_envelope_for_request(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        request_ref: str,
    ) -> int:
        """Delete the exact scoped bundle even if its create call lost its return value."""

        with Session(engine) as session:
            envelope_ids = list(
                session.exec(
                    select(VoiceResultArtifactDB.id).where(
                        VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                        VoiceResultArtifactDB.owner_subject == principal.subject,
                        VoiceResultArtifactDB.profile_id == profile_id,
                        VoiceResultArtifactDB.request_hash == request_ref,
                        VoiceResultArtifactDB.artifact_kind == "result_envelope",
                    )
                ).all()
            )
            if not envelope_ids:
                return 0
            rows = list(
                session.exec(
                    select(VoiceResultArtifactDB.id).where(
                        VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                        VoiceResultArtifactDB.owner_subject == principal.subject,
                        (
                            VoiceResultArtifactDB.id.in_(envelope_ids)
                            | VoiceResultArtifactDB.parent_artifact_id.in_(envelope_ids)
                        ),
                    )
                ).all()
            )
            session.exec(
                delete(VoiceResultArtifactDB).where(
                    VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                    VoiceResultArtifactDB.owner_subject == principal.subject,
                    (
                        VoiceResultArtifactDB.id.in_(envelope_ids)
                        | VoiceResultArtifactDB.parent_artifact_id.in_(envelope_ids)
                    ),
                )
            )
            session.commit()
            return len(rows)

    def get(
        self,
        principal: VoicePrincipal,
        artifact_id: str,
        *,
        profile_id: str | None = None,
    ) -> VoiceResultArtifactDB | None:
        with Session(engine) as session:
            statement = select(VoiceResultArtifactDB).where(
                VoiceResultArtifactDB.id == artifact_id,
                VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                VoiceResultArtifactDB.owner_subject == principal.subject,
            )
            if profile_id is not None:
                statement = statement.where(VoiceResultArtifactDB.profile_id == profile_id)
            return session.exec(statement).first()

    def find_live_envelope(
        self,
        principal: VoicePrincipal,
        *,
        request_ref: str,
        profile_id: str,
        now: float | None = None,
    ) -> VoiceResultArtifactDB | None:
        """Find the live result envelope for one opaque request scope."""

        cutoff = float(now if now is not None else time.time())
        with Session(engine) as session:
            return session.exec(
                select(VoiceResultArtifactDB)
                .where(
                    VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                    VoiceResultArtifactDB.owner_subject == principal.subject,
                    VoiceResultArtifactDB.profile_id == profile_id,
                    VoiceResultArtifactDB.request_hash == request_ref,
                    VoiceResultArtifactDB.artifact_kind == "result_envelope",
                    VoiceResultArtifactDB.expires_at > cutoff,
                )
                .order_by(VoiceResultArtifactDB.created_at.desc())
            ).first()

    def purge_expired(self, *, now: float | None = None) -> int:
        cutoff = float(now if now is not None else time.time())
        with Session(engine) as session:
            query = select(VoiceResultArtifactDB.id).where(VoiceResultArtifactDB.expires_at <= cutoff)
            rows = session.exec(query).all()
            session.exec(delete(VoiceResultArtifactDB).where(VoiceResultArtifactDB.expires_at <= cutoff))
            session.commit()
            return len(rows)
