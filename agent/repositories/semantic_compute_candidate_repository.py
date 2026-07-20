"""Hub persistence for key-bound semantic-compute capability advertisements."""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import (
    SemanticCapabilityAdvertisementDB,
    SemanticComputeCandidateKeyDB,
)
from agent.repositories.semantic_contract_repository import SemanticPrincipal
from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    canonical_json,
    validate_capability_advertisement,
)


class SemanticComputeCandidateRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    """Conservative Hub reduction; no browser-supplied value can raise it."""

    observed_capacity: int
    user_limit: int
    reserve_capacity: int
    recent_error_rate: float
    reputation: int
    active_assignments: int
    failure_domain: str


class CandidateObservationPort(Protocol):
    def observe(self, principal: SemanticPrincipal, advertisement: Mapping[str, Any]) -> CandidateObservation: ...


class ConservativeCandidateObservation:
    """Safe production default until a trusted telemetry adapter is configured.

    Browser buckets can only make a candidate ineligible. They never produce
    more than one unit of Hub-observed capacity.
    """

    def __init__(self, *, maximum_capacity: int | None = None) -> None:
        raw = os.environ.get("ANANTA_SEMANTIC_COMPUTE_MAX_PEER_CAPACITY", "1")
        try:
            configured = int(raw) if maximum_capacity is None else int(maximum_capacity)
        except (TypeError, ValueError):
            configured = 0
        self._maximum = max(0, min(configured, 1))

    def observe(self, principal: SemanticPrincipal, advertisement: Mapping[str, Any]) -> CandidateObservation:
        profile = dict(advertisement.get("resource_profile") or {})
        unsafe = (
            profile.get("cpu") == "unknown"
            or profile.get("memory") == "unknown"
            or profile.get("battery") == "critical"
            or profile.get("network") == "constrained"
        )
        capacity = 0 if unsafe else self._maximum
        domain = hashlib.sha256(f"{principal.tenant_id}\0{principal.subject}".encode()).hexdigest()
        return CandidateObservation(
            observed_capacity=capacity,
            user_limit=self._maximum,
            reserve_capacity=0,
            recent_error_rate=0.0 if capacity else 1.0,
            reputation=50,
            active_assignments=0,
            failure_domain=domain,
        )


class SemanticComputeCandidateRepository:
    """Stores only authenticated, Ed25519-verified, short-lived claims."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        clock=time.time,
        observation: CandidateObservationPort | None = None,
    ) -> None:
        self._engine = db_engine
        self._clock = clock
        self._observation = observation or ConservativeCandidateObservation()

    def register_key(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        key_id: str,
        public_key_b64: str,
        expires_at_ms: int,
    ) -> SemanticComputeCandidateKeyDB:
        now = self._clock()
        expiry = expires_at_ms / 1000.0
        if not now < expiry <= now + 600:
            raise SemanticComputeCandidateRepositoryError("candidate_key_expiry_invalid")
        raw = self._decode_key(public_key_b64)
        expected_id = f"cap-{hashlib.sha256(raw).hexdigest()[:32]}"
        if key_id != expected_id:
            raise SemanticComputeCandidateRepositoryError("candidate_key_id_mismatch")
        with Session(self._engine) as db:
            existing = db.exec(
                select(SemanticComputeCandidateKeyDB).where(
                    SemanticComputeCandidateKeyDB.tenant_id == principal.tenant_id,
                    SemanticComputeCandidateKeyDB.session_id == session_id,
                    SemanticComputeCandidateKeyDB.epoch == epoch,
                    SemanticComputeCandidateKeyDB.member_subject == principal.subject,
                    SemanticComputeCandidateKeyDB.key_id == key_id,
                )
            ).first()
            if existing is not None:
                if existing.public_key_b64 != public_key_b64:
                    raise SemanticComputeCandidateRepositoryError("candidate_key_substitution")
                existing.expires_at = max(existing.expires_at, expiry)
                existing.status = "active"
                existing.version += 1
                existing.updated_at = now
                db.add(existing)
                db.commit()
                db.refresh(existing)
                return existing
            item = SemanticComputeCandidateKeyDB(
                tenant_id=principal.tenant_id,
                session_id=session_id,
                epoch=epoch,
                member_subject=principal.subject,
                key_id=key_id,
                public_key_b64=public_key_b64,
                expires_at=expiry,
            )
            db.add(item)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise SemanticComputeCandidateRepositoryError("candidate_key_conflict") from exc
            db.refresh(item)
            return item

    def put_advertisement(
        self,
        principal: SemanticPrincipal,
        *,
        raw: Mapping[str, Any],
    ) -> tuple[SemanticCapabilityAdvertisementDB, bool]:
        now = self._clock()
        now_ms = int(now * 1000)
        try:
            normalized = validate_capability_advertisement(raw, now_ms=now_ms)
        except SemanticComputeContractError as exc:
            raise SemanticComputeCandidateRepositoryError(exc.reason_code) from exc
        if normalized["sender_id"] != principal.subject:
            raise SemanticComputeCandidateRepositoryError("candidate_sender_mismatch")
        signature = dict(normalized["signature"])
        if signature.get("algorithm") != "ed25519":
            raise SemanticComputeCandidateRepositoryError("candidate_signature_algorithm_invalid")
        key_id = str(signature.get("key_id") or "")
        with Session(self._engine) as db:
            key = db.exec(
                select(SemanticComputeCandidateKeyDB).where(
                    SemanticComputeCandidateKeyDB.tenant_id == principal.tenant_id,
                    SemanticComputeCandidateKeyDB.session_id == normalized["session_id"],
                    SemanticComputeCandidateKeyDB.epoch == normalized["epoch"],
                    SemanticComputeCandidateKeyDB.member_subject == principal.subject,
                    SemanticComputeCandidateKeyDB.key_id == key_id,
                    SemanticComputeCandidateKeyDB.status == "active",
                    SemanticComputeCandidateKeyDB.expires_at > now,
                )
            ).first()
            if key is None:
                raise SemanticComputeCandidateRepositoryError("candidate_key_not_bound")
            self._verify_signature(key.public_key_b64, signature, normalized)
            unsigned = {name: value for name, value in normalized.items() if name != "signature"}
            payload_digest = hashlib.sha256(canonical_json(unsigned)).hexdigest()
            existing = db.exec(
                select(SemanticCapabilityAdvertisementDB).where(
                    SemanticCapabilityAdvertisementDB.tenant_id == principal.tenant_id,
                    SemanticCapabilityAdvertisementDB.advertisement_id == normalized["advertisement_id"],
                )
            ).first()
            if existing is not None:
                if existing.payload_digest != payload_digest or existing.key_id != key_id:
                    raise SemanticComputeCandidateRepositoryError("advertisement_id_conflict")
                return existing, True
            observation = self._observation.observe(principal, normalized)
            item = SemanticCapabilityAdvertisementDB(
                tenant_id=principal.tenant_id,
                session_id=str(normalized["session_id"]),
                room_id=str(normalized["room_id"]) if normalized.get("room_id") else None,
                epoch=int(normalized["epoch"]),
                sender_subject=principal.subject,
                advertisement_id=str(normalized["advertisement_id"]),
                key_id=key_id,
                payload_digest=payload_digest,
                normalized_payload=dict(normalized),
                observed_capacity=observation.observed_capacity,
                user_limit=observation.user_limit,
                reserve_capacity=observation.reserve_capacity,
                recent_error_rate=observation.recent_error_rate,
                reputation=observation.reputation,
                active_assignments=observation.active_assignments,
                failure_domain=observation.failure_domain,
                expires_at=float(normalized["expires_at_ms"]) / 1000.0,
            )
            db.add(item)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise SemanticComputeCandidateRepositoryError("advertisement_conflict") from exc
            db.refresh(item)
            return item, False

    def current(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        room_id: str | None = None,
        limit: int = 32,
    ) -> list[SemanticCapabilityAdvertisementDB]:
        if not 1 <= limit <= 128:
            raise SemanticComputeCandidateRepositoryError("candidate_limit_invalid")
        now = self._clock()
        with Session(self._engine) as db:
            statement = select(SemanticCapabilityAdvertisementDB).where(
                SemanticCapabilityAdvertisementDB.tenant_id == principal.tenant_id,
                SemanticCapabilityAdvertisementDB.session_id == session_id,
                SemanticCapabilityAdvertisementDB.epoch == epoch,
                SemanticCapabilityAdvertisementDB.status == "active",
                SemanticCapabilityAdvertisementDB.expires_at > now,
            )
            if room_id is not None:
                statement = statement.where(SemanticCapabilityAdvertisementDB.room_id == room_id)
            rows = list(
                db.exec(
                    statement.order_by(
                        SemanticCapabilityAdvertisementDB.created_at.desc(),
                        SemanticCapabilityAdvertisementDB.id.desc(),
                    ).limit(limit)
                )
            )
            latest: list[SemanticCapabilityAdvertisementDB] = []
            seen: set[str] = set()
            for item in rows:
                if item.sender_subject in seen:
                    continue
                latest.append(item)
                seen.add(item.sender_subject)
            return latest

    @staticmethod
    def _decode_key(value: str) -> bytes:
        try:
            raw = base64.b64decode(value, validate=True)
        except (TypeError, ValueError) as exc:
            raise SemanticComputeCandidateRepositoryError("candidate_public_key_invalid") from exc
        if len(raw) != 32:
            raise SemanticComputeCandidateRepositoryError("candidate_public_key_invalid")
        return raw

    @classmethod
    def _verify_signature(
        cls,
        public_key_b64: str,
        signature: Mapping[str, Any],
        normalized: Mapping[str, Any],
    ) -> None:
        try:
            signature_raw = base64.b64decode(str(signature.get("value") or ""), validate=True)
            if len(signature_raw) != 64:
                raise ValueError("signature length")
            key = Ed25519PublicKey.from_public_bytes(cls._decode_key(public_key_b64))
            unsigned = {name: value for name, value in normalized.items() if name != "signature"}
            key.verify(signature_raw, canonical_json(unsigned))
        except (InvalidSignature, TypeError, ValueError) as exc:
            raise SemanticComputeCandidateRepositoryError("candidate_signature_invalid") from exc


_repository: SemanticComputeCandidateRepository | None = None


def get_semantic_compute_candidate_repository() -> SemanticComputeCandidateRepository:
    global _repository
    if _repository is None:
        _repository = SemanticComputeCandidateRepository()
    return _repository


__all__ = [
    "CandidateObservation",
    "CandidateObservationPort",
    "ConservativeCandidateObservation",
    "SemanticComputeCandidateRepository",
    "SemanticComputeCandidateRepositoryError",
    "get_semantic_compute_candidate_repository",
]
