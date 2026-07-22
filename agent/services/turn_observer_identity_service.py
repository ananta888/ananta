"""Persistent least-privilege identity authority for TURN observers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent.db_models.turn_observer_identities import (
    TurnObserverCredentialDB,
    TurnObserverIdentityDB,
    TurnObserverIdentityMutationDB,
)
from agent.repositories.turn_observer_identity_repository import (
    SqlTurnObserverIdentityRepository,
    TurnObserverIdentityRepositoryError,
)


class TurnObserverIdentityError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnObserverTrustPolicy:
    audience: str
    allowed_ca_fingerprints: frozenset[str]
    required_eku: str
    credential_ttl_seconds_max: int
    rotation_overlap_seconds: int
    enrollment_attempts_per_minute: int

    @classmethod
    def from_path(cls, path: Path) -> "TurnObserverTrustPolicy":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if set(raw) != {
                "version",
                "activation",
                "role",
                "audience",
                "allowed_ca_fingerprints",
                "required_eku",
                "credential_ttl_seconds_max",
                "rotation_overlap_seconds",
                "enrollment_attempts_per_minute",
            }:
                raise ValueError
            if raw["version"] != "1.0" or raw["role"] != "turn_observer":
                raise ValueError
            return cls(
                str(raw["audience"]),
                frozenset(raw["allowed_ca_fingerprints"]),
                str(raw["required_eku"]),
                int(raw["credential_ttl_seconds_max"]),
                int(raw["rotation_overlap_seconds"]),
                int(raw["enrollment_attempts_per_minute"]),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise TurnObserverIdentityError("turn_observer_trust_policy_invalid", 503) from exc

    def __post_init__(self) -> None:
        if (
            not self.audience
            or not self.allowed_ca_fingerprints
            or self.required_eku != "clientAuth"
            or min(
                self.credential_ttl_seconds_max,
                self.rotation_overlap_seconds,
                self.enrollment_attempts_per_minute,
            )
            <= 0
            or self.rotation_overlap_seconds >= self.credential_ttl_seconds_max
        ):
            raise TurnObserverIdentityError("turn_observer_trust_policy_invalid", 503)


@dataclass(frozen=True, slots=True)
class TurnObserverEnrollmentCommand:
    pool_id: str
    instance_id: str
    region: str
    expected_version: int
    public_key_b64: str
    certificate_fingerprint: str
    certificate_public_key_fingerprint: str
    ca_fingerprint: str
    certificate_san: str
    certificate_ekus: tuple[str, ...]
    certificate_not_after: float
    proof_nonce: str
    proof_signature_b64: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class TurnObserverTransportIdentity:
    mtls_verified: bool
    role: str
    audience: str
    pool_id: str
    instance_id: str
    identity_version: int
    certificate_fingerprint: str
    ca_fingerprint: str
    certificate_san: str
    certificate_ekus: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TurnObserverAuthorization:
    identity_id: str
    identity_version: int
    pool_id: str
    instance_id: str
    region: str
    public_key_b64: str
    credential_id: str


@dataclass(frozen=True, slots=True)
class TurnObserverIdentityResult:
    identity_id: str
    pool_id: str
    instance_id: str
    region: str
    role: str
    audience: str
    status: str
    version: int
    reason_code: str
    recovery_evidence_required: bool

    def public(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class TurnObserverIdentityService:
    def __init__(
        self,
        repository: SqlTurnObserverIdentityRepository,
        *,
        policy: TurnObserverTrustPolicy,
        receipt_secret: bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(receipt_secret) < 32:
            raise TurnObserverIdentityError("turn_observer_receipt_secret_invalid", 503)
        self._repository = repository
        self._policy = policy
        self._secret = bytes(receipt_secret)
        self._clock = clock

    def proof_payload(
        self,
        operation: str,
        command: TurnObserverEnrollmentCommand,
        *,
        actor: str,
        idempotency_key: str,
    ) -> bytes:
        document = self._command_document(operation, command, actor, idempotency_key)
        return b"ananta.turn-observer-proof.v1\0" + json.dumps(
            document, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def enroll(
        self,
        command: TurnObserverEnrollmentCommand,
        *,
        actor: str,
        idempotency_key: str,
        source_ref: str,
    ) -> TurnObserverIdentityResult:
        self._validate_command(command, actor, idempotency_key)
        if command.expected_version != 0:
            raise TurnObserverIdentityError("turn_observer_expected_version_invalid")
        key_digest, request_digest = self._receipt_digests("enroll", command, actor, idempotency_key)
        cached = self._cached(actor, key_digest, request_digest)
        if cached:
            return cached
        now = self._clock()
        self._consume_rate(actor, source_ref, now)
        public_key, public_fingerprint = self._verify_proof("enroll", command, actor, idempotency_key)
        credential_id = "turn-observer-credential-" + hashlib.sha256(
            f"{command.pool_id}\0{command.instance_id}\0{public_fingerprint}".encode()
        ).hexdigest()[:32]
        identity_id = "turn-observer-" + hashlib.sha256(
            f"{command.pool_id}\0{command.instance_id}".encode()
        ).hexdigest()[:32]
        identity = TurnObserverIdentityDB(
            id=identity_id,
            pool_id=command.pool_id,
            instance_id=command.instance_id,
            role="turn_observer",
            audience=self._policy.audience,
            region=command.region,
            active_credential_id=credential_id,
            enrolled_at=now,
            created_at=now,
            updated_at=now,
        )
        credential = self._credential(identity_id, credential_id, command, public_fingerprint, now)
        result = self._result(identity, "turn_observer_enrolled")
        mutation = self._mutation(identity, "enroll", actor, key_digest, request_digest, result, command.expected_version, now)
        try:
            self._repository.create(identity, credential, mutation)
        except TurnObserverIdentityRepositoryError as exc:
            raise self._repository_error(exc) from exc
        return result

    def rotate(
        self,
        command: TurnObserverEnrollmentCommand,
        *,
        actor: str,
        idempotency_key: str,
        source_ref: str,
    ) -> TurnObserverIdentityResult:
        self._validate_command(command, actor, idempotency_key)
        key_digest, request_digest = self._receipt_digests("rotate", command, actor, idempotency_key)
        cached = self._cached(actor, key_digest, request_digest)
        if cached:
            return cached
        now = self._clock()
        self._consume_rate(actor, source_ref, now)
        _public_key, public_fingerprint = self._verify_proof("rotate", command, actor, idempotency_key)
        identity = self._repository.get(pool_id=command.pool_id, instance_id=command.instance_id)
        if identity is None or identity.status != "active":
            raise TurnObserverIdentityError("turn_observer_identity_inactive", 409)
        if identity.version != command.expected_version:
            raise TurnObserverIdentityError("turn_observer_version_conflict", 409)
        credential_id = "turn-observer-credential-" + hashlib.sha256(
            f"{identity.id}\0{public_fingerprint}\0{command.expected_version}".encode()
        ).hexdigest()[:32]
        credential = self._credential(identity.id, credential_id, command, public_fingerprint, now)
        projected = TurnObserverIdentityResult(
            identity.id,
            identity.pool_id,
            identity.instance_id,
            identity.region,
            identity.role,
            identity.audience,
            "active",
            identity.version + 1,
            "turn_observer_rotated",
            False,
        )
        mutation = self._mutation(identity, "rotate", actor, key_digest, request_digest, projected, command.expected_version, now)
        try:
            updated = self._repository.rotate(
                identity=identity,
                credential=credential,
                expected_version=command.expected_version,
                overlap_until=now + self._policy.rotation_overlap_seconds,
                mutation=mutation,
                now=now,
            )
        except TurnObserverIdentityRepositoryError as exc:
            raise self._repository_error(exc) from exc
        return self._result(updated, "turn_observer_rotated")

    def revoke(
        self,
        *,
        pool_id: str,
        instance_id: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        reason_code: str,
    ) -> TurnObserverIdentityResult:
        if not self._safe(actor) or not self._safe(reason_code) or not self._idempotency(idempotency_key):
            raise TurnObserverIdentityError("turn_observer_revoke_request_invalid")
        identity = self._repository.get(pool_id=pool_id, instance_id=instance_id)
        if identity is None:
            raise TurnObserverIdentityError("turn_observer_identity_not_found", 404)
        document = {
            "operation": "revoke",
            "pool_id": pool_id,
            "instance_id": instance_id,
            "expected_version": expected_version,
            "actor": actor,
            "reason_code": reason_code,
        }
        key_digest = self._digest("idempotency", idempotency_key)
        request_digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        cached = self._cached(actor, key_digest, request_digest)
        if cached:
            return cached
        now = self._clock()
        projected = TurnObserverIdentityResult(
            identity.id,
            identity.pool_id,
            identity.instance_id,
            identity.region,
            identity.role,
            identity.audience,
            "revoked",
            expected_version + 1,
            "turn_observer_revoked",
            True,
        )
        mutation = self._mutation(identity, "revoke", actor, key_digest, request_digest, projected, expected_version, now)
        try:
            updated = self._repository.revoke(
                identity=identity,
                expected_version=expected_version,
                mutation=mutation,
                now=now,
            )
        except TurnObserverIdentityRepositoryError as exc:
            raise self._repository_error(exc) from exc
        return self._result(updated, "turn_observer_revoked")

    def authorize(self, transport: TurnObserverTransportIdentity) -> TurnObserverAuthorization:
        if (
            transport.mtls_verified is not True
            or transport.role != "turn_observer"
            or transport.audience != self._policy.audience
            or transport.ca_fingerprint not in self._policy.allowed_ca_fingerprints
            or transport.certificate_ekus != (self._policy.required_eku,)
            or transport.certificate_san != self._san(transport.pool_id, transport.instance_id)
        ):
            raise TurnObserverIdentityError("turn_observer_transport_identity_denied", 403)
        identity = self._repository.get(pool_id=transport.pool_id, instance_id=transport.instance_id)
        if (
            identity is None
            or identity.status != "active"
            or identity.recovery_evidence_required
            or identity.version != transport.identity_version
            or identity.role != "turn_observer"
            or identity.audience != self._policy.audience
        ):
            raise TurnObserverIdentityError("turn_observer_identity_inactive", 403)
        now = self._clock()
        credential_ids = [identity.active_credential_id]
        if identity.previous_credential_id and identity.rotation_overlap_until and identity.rotation_overlap_until > now:
            credential_ids.append(identity.previous_credential_id)
        for credential_id in credential_ids:
            credential = self._repository.credential(credential_id)
            if (
                credential is not None
                and credential.status in {"active", "overlap"}
                and credential.valid_from <= now < credential.valid_until
                and credential.certificate_fingerprint == transport.certificate_fingerprint
                and credential.ca_fingerprint == transport.ca_fingerprint
                and credential.certificate_san == transport.certificate_san
                and tuple(credential.certificate_ekus) == transport.certificate_ekus
            ):
                return TurnObserverAuthorization(
                    identity.id,
                    identity.version,
                    identity.pool_id,
                    identity.instance_id,
                    identity.region,
                    credential.public_key_b64,
                    credential.id,
                )
        raise TurnObserverIdentityError("turn_observer_credential_denied", 403)

    def _verify_proof(self, operation, command, actor, idempotency_key):
        try:
            raw_key = base64.b64decode(command.public_key_b64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(raw_key)
            signature = base64.b64decode(command.proof_signature_b64, validate=True)
            public_key.verify(signature, self.proof_payload(operation, command, actor=actor, idempotency_key=idempotency_key))
        except (ValueError, InvalidSignature) as exc:
            raise TurnObserverIdentityError("turn_observer_proof_invalid", 403) from exc
        fingerprint = "sha256:" + hashlib.sha256(raw_key).hexdigest()
        if fingerprint != command.certificate_public_key_fingerprint:
            raise TurnObserverIdentityError("turn_observer_certificate_key_mismatch", 403)
        return public_key, fingerprint

    def _validate_command(self, command, actor, idempotency_key):
        for value in (command.pool_id, command.instance_id, command.region, actor, command.reason_code):
            if not self._safe(value):
                raise TurnObserverIdentityError("turn_observer_enrollment_request_invalid")
        if not self._idempotency(idempotency_key) or isinstance(command.expected_version, bool) or command.expected_version < 0:
            raise TurnObserverIdentityError("turn_observer_enrollment_request_invalid")
        if command.ca_fingerprint not in self._policy.allowed_ca_fingerprints:
            raise TurnObserverIdentityError("turn_observer_ca_denied", 403)
        if command.certificate_san != self._san(command.pool_id, command.instance_id):
            raise TurnObserverIdentityError("turn_observer_san_denied", 403)
        if command.certificate_ekus != (self._policy.required_eku,):
            raise TurnObserverIdentityError("turn_observer_eku_denied", 403)
        now = self._clock()
        if not now < command.certificate_not_after <= now + self._policy.credential_ttl_seconds_max:
            raise TurnObserverIdentityError("turn_observer_certificate_lifetime_invalid", 403)
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", command.proof_nonce):
            raise TurnObserverIdentityError("turn_observer_proof_nonce_invalid")
        for value in (command.certificate_fingerprint, command.certificate_public_key_fingerprint):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise TurnObserverIdentityError("turn_observer_fingerprint_invalid")

    def _credential(self, identity_id, credential_id, command, public_fingerprint, now):
        return TurnObserverCredentialDB(
            id=credential_id,
            identity_id=identity_id,
            public_key_b64=command.public_key_b64,
            public_key_fingerprint=public_fingerprint,
            certificate_fingerprint=command.certificate_fingerprint,
            ca_fingerprint=command.ca_fingerprint,
            certificate_san=command.certificate_san,
            certificate_ekus=list(command.certificate_ekus),
            proof_nonce_digest=self._digest("proof-nonce", command.proof_nonce),
            valid_from=now,
            valid_until=command.certificate_not_after,
            created_at=now,
        )

    def _command_document(self, operation, command, actor, idempotency_key):
        return {
            "operation": operation,
            "pool_id": command.pool_id,
            "instance_id": command.instance_id,
            "region": command.region,
            "expected_version": command.expected_version,
            "public_key_b64": command.public_key_b64,
            "certificate_fingerprint": command.certificate_fingerprint,
            "certificate_public_key_fingerprint": command.certificate_public_key_fingerprint,
            "ca_fingerprint": command.ca_fingerprint,
            "certificate_san": command.certificate_san,
            "certificate_ekus": list(command.certificate_ekus),
            "certificate_not_after": command.certificate_not_after,
            "proof_nonce": command.proof_nonce,
            "reason_code": command.reason_code,
            "actor": actor,
            "idempotency_key_digest": self._digest("idempotency", idempotency_key),
        }

    def _receipt_digests(self, operation, command, actor, idempotency_key):
        key = self._digest("idempotency", idempotency_key)
        request = hashlib.sha256(
            self.proof_payload(operation, command, actor=actor, idempotency_key=idempotency_key)
            + command.proof_signature_b64.encode()
        ).hexdigest()
        return key, request

    def _cached(self, actor, key_digest, request_digest):
        receipt = self._repository.receipt(actor=actor, key_digest=key_digest)
        if receipt is None:
            return None
        if receipt.request_digest != request_digest:
            raise TurnObserverIdentityError("turn_observer_idempotency_conflict", 409)
        normalized = {
            "identity_id": receipt.identity_id,
            "pool_id": receipt.pool_id,
            "instance_id": receipt.instance_id,
            "region": receipt.result_region,
            "role": receipt.result_role,
            "audience": receipt.result_audience,
            "status": receipt.result_status,
            "version": receipt.result_version,
            "reason_code": receipt.reason_code,
            "recovery_evidence_required": receipt.result_recovery_evidence_required,
        }
        if any(value is None for value in normalized.values()):
            legacy = receipt.response_json
            if not isinstance(legacy, Mapping) or set(legacy) != set(normalized):
                raise TurnObserverIdentityError("turn_observer_receipt_invalid", 503)
            normalized = dict(legacy)
        result = TurnObserverIdentityResult(**normalized)
        if (
            not all(
                self._safe(value)
                for value in (
                    result.identity_id,
                    result.pool_id,
                    result.instance_id,
                    result.region,
                    result.role,
                    result.audience,
                    result.reason_code,
                )
            )
            or result.role != "turn_observer"
            or result.status not in {"active", "revoked"}
            or isinstance(result.version, bool)
            or not isinstance(result.version, int)
            or result.version <= 0
            or not isinstance(result.recovery_evidence_required, bool)
        ):
            raise TurnObserverIdentityError("turn_observer_receipt_invalid", 503)
        return result

    def _consume_rate(self, actor, source_ref, now):
        try:
            self._repository.consume_rate_limit(
                actor=actor,
                source_digest=self._digest("source", source_ref),
                now=now,
                window_seconds=60,
                attempts_max=self._policy.enrollment_attempts_per_minute,
            )
        except TurnObserverIdentityRepositoryError as exc:
            raise self._repository_error(exc) from exc

    def _mutation(self, identity, operation, actor, key_digest, request_digest, result, expected, now):
        return TurnObserverIdentityMutationDB(
            identity_id=identity.id,
            pool_id=identity.pool_id,
            instance_id=identity.instance_id,
            operation=operation,
            expected_version=expected,
            result_version=result.version,
            result_status=result.status,
            result_region=result.region,
            result_role=result.role,
            result_audience=result.audience,
            result_recovery_evidence_required=result.recovery_evidence_required,
            actor=actor,
            reason_code=result.reason_code,
            idempotency_key_digest=key_digest,
            request_digest=request_digest,
            response_json={},
            audited_at=now,
        )

    @staticmethod
    def _result(identity: TurnObserverIdentityDB, reason: str) -> TurnObserverIdentityResult:
        return TurnObserverIdentityResult(
            identity.id,
            identity.pool_id,
            identity.instance_id,
            identity.region,
            identity.role,
            identity.audience,
            identity.status,
            identity.version,
            reason,
            identity.recovery_evidence_required,
        )

    def _digest(self, domain: str, value: str) -> str:
        return hmac.new(self._secret, f"turn-observer-{domain}-v1\0{value}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _safe(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value))

    @staticmethod
    def _idempotency(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,127}", value))

    @staticmethod
    def _san(pool_id: str, instance_id: str) -> str:
        return f"spiffe://ananta.local/turn-observer/{pool_id}/{instance_id}"

    @staticmethod
    def _repository_error(exc: TurnObserverIdentityRepositoryError) -> TurnObserverIdentityError:
        status = 429 if "rate" in exc.reason_code else 409 if "conflict" in exc.reason_code else 503
        return TurnObserverIdentityError(exc.reason_code, status)


__all__ = [
    "TurnObserverAuthorization",
    "TurnObserverEnrollmentCommand",
    "TurnObserverIdentityError",
    "TurnObserverIdentityResult",
    "TurnObserverIdentityService",
    "TurnObserverTransportIdentity",
    "TurnObserverTrustPolicy",
]
