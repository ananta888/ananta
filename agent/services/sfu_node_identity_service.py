"""Hub policy for SFU node enrollment, rotation, revocation and runtime trust."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID

from agent.repositories.sfu_runtime_identity_repository import (
    SfuRuntimeCredentialRegistration,
    SfuRuntimeIdentityMutationResult,
    SfuRuntimeIdentityRecord,
    SfuRuntimeIdentityRepositoryError,
    SfuRuntimeIdentityRepositoryPort,
)

LIVEKIT_CONTROL_API = "livekit_control_api"
AUTHENTICATED_RUNTIME_EXTENSION = "authenticated_runtime_extension"
SFU_CONTROL = "sfu_control"
SFU_OBSERVER = "sfu_observer"

_MODES = frozenset({LIVEKIT_CONTROL_API, AUTHENTICATED_RUNTIME_EXTENSION})
_ROLES = frozenset({SFU_CONTROL, SFU_OBSERVER})
_NODE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_PRIVATE_MARKERS = ("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN EC PRIVATE KEY")


class SfuNodeIdentityError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuProofOfPossession:
    algorithm: str
    signature: str
    nonce: str
    issued_at: float


@dataclass(frozen=True, slots=True)
class SfuNodeCredentialCommand:
    node_id: str
    runtime_control_mode: str
    roles: tuple[str, ...]
    public_key_pem: str
    credential_kind: str
    credential_fingerprint: str | None
    certificate_pem: str | None
    proof: SfuProofOfPossession
    expected_version: int
    actor: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SfuNodeRevocationCommand:
    node_id: str
    expected_version: int
    emergency: bool
    actor: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SfuNodeTrustPolicy:
    runtime_control_mode: str
    activation_enabled: bool = False
    activation_evidence_ids: tuple[str, ...] = ()
    enrollment_window_seconds: int = 60
    enrollment_max_attempts: int = 5
    proof_clock_skew_seconds: int = 30
    rotation_overlap_seconds: int = 300
    node_revocation_max_seconds: int = 30
    extension_san_prefix: str = "spiffe://ananta.local/sfu"
    trusted_ca_certificates: tuple[x509.Certificate, ...] = ()
    revoked_certificate_fingerprints: frozenset[str] = frozenset()

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "SfuNodeTrustPolicy":
        config_path = Path(path) if path else Path(__file__).resolve().parents[2] / "config/sfu_node_trust.default.json"
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SfuNodeIdentityError("sfu_node_trust_config_unavailable", status_code=503) from exc
        if not isinstance(payload, Mapping):
            raise SfuNodeIdentityError("sfu_node_trust_config_invalid", status_code=503)
        return cls.from_mapping(payload, base_dir=config_path.parent)

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object], *, base_dir: Path | None = None
    ) -> "SfuNodeTrustPolicy":
        mode = str(payload.get("runtime_control_mode") or "").strip()
        if mode not in _MODES:
            raise SfuNodeIdentityError("sfu_runtime_control_mode_invalid", status_code=503)
        activation = payload.get("activation") if isinstance(payload.get("activation"), Mapping) else {}
        evidence = tuple(str(item) for item in (activation.get("evidence_ids") or ()))
        evidence_verified = bool(activation.get("evidence_verified", False))
        requested_activation = bool(activation.get("enabled", False))
        if requested_activation and (
            not evidence_verified or not evidence or any(not _EVIDENCE_ID.fullmatch(item) for item in evidence)
        ):
            raise SfuNodeIdentityError("sfu_activation_evidence_invalid", status_code=503)
        enrollment = payload.get("enrollment") if isinstance(payload.get("enrollment"), Mapping) else {}
        rotation = payload.get("rotation") if isinstance(payload.get("rotation"), Mapping) else {}
        extension = payload.get("authenticated_runtime_extension") if isinstance(payload.get("authenticated_runtime_extension"), Mapping) else {}
        ca_certificates: list[x509.Certificate] = []
        root = base_dir or Path.cwd()
        for configured_path in extension.get("trusted_ca_pem_files") or ():
            candidate = Path(str(configured_path))
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                ca_certificates.append(x509.load_pem_x509_certificate(candidate.read_bytes()))
            except (OSError, ValueError) as exc:
                raise SfuNodeIdentityError("sfu_trusted_ca_invalid", status_code=503) from exc
        revoked = frozenset(str(item).lower() for item in extension.get("revoked_certificate_fingerprints") or ())
        policy = cls(
            runtime_control_mode=mode,
            activation_enabled=requested_activation and evidence_verified,
            activation_evidence_ids=evidence,
            enrollment_window_seconds=int(enrollment.get("rate_limit_window_seconds", 60)),
            enrollment_max_attempts=int(enrollment.get("rate_limit_max_attempts", 5)),
            proof_clock_skew_seconds=int(enrollment.get("proof_clock_skew_seconds", 30)),
            rotation_overlap_seconds=int(rotation.get("overlap_seconds", 300)),
            node_revocation_max_seconds=int(rotation.get("node_revocation_max_seconds", 30)),
            extension_san_prefix=str(extension.get("san_prefix") or "spiffe://ananta.local/sfu").rstrip("/"),
            trusted_ca_certificates=tuple(ca_certificates),
            revoked_certificate_fingerprints=revoked,
        )
        if min(
            policy.enrollment_window_seconds,
            policy.enrollment_max_attempts,
            policy.proof_clock_skew_seconds,
            policy.node_revocation_max_seconds,
        ) <= 0 or policy.rotation_overlap_seconds < 0:
            raise SfuNodeIdentityError("sfu_node_trust_config_invalid", status_code=503)
        return policy


class SfuNodeIdentityService:
    """Security policy; persistence is injected and remains the sole authority."""

    def __init__(
        self,
        repository: SfuRuntimeIdentityRepositoryPort,
        policy: SfuNodeTrustPolicy,
        *,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self.policy = policy
        self._clock = clock

    def enroll(
        self, command: SfuNodeCredentialCommand, *, source: str
    ) -> SfuRuntimeIdentityMutationResult:
        self._validate_common(command, operation="enroll")
        if command.expected_version != 0:
            raise SfuNodeIdentityError("sfu_identity_create_expected_version_invalid")
        try:
            rate = self._repository.consume_enrollment_attempt(
                actor=command.actor,
                source=source,
                window_seconds=self.policy.enrollment_window_seconds,
                limit=self.policy.enrollment_max_attempts,
            )
        except SfuRuntimeIdentityRepositoryError as exc:
            raise _service_error(exc) from exc
        if not rate.allowed:
            raise SfuNodeIdentityError("sfu_enrollment_rate_limited", status_code=429)
        registration, digest = self._validate_credential(command, operation="enroll")
        try:
            return self._repository.create_identity(
                node_id=command.node_id,
                runtime_control_mode=command.runtime_control_mode,
                roles=command.roles,
                credential=registration,
                expected_version=command.expected_version,
                actor=command.actor,
                reason=command.reason,
                idempotency_key=command.idempotency_key,
                request_digest=digest,
            )
        except SfuRuntimeIdentityRepositoryError as exc:
            raise _service_error(exc) from exc

    def rotate(self, command: SfuNodeCredentialCommand) -> SfuRuntimeIdentityMutationResult:
        self._validate_common(command, operation="rotate")
        if command.expected_version < 1:
            raise SfuNodeIdentityError("sfu_identity_expected_version_invalid")
        current = self.get(command.node_id)
        if current.runtime_control_mode != command.runtime_control_mode or current.roles != command.roles:
            raise SfuNodeIdentityError("sfu_identity_boundary_change_forbidden", status_code=409)
        registration, digest = self._validate_credential(command, operation="rotate")
        try:
            return self._repository.rotate_identity(
                node_id=command.node_id,
                credential=registration,
                expected_version=command.expected_version,
                overlap_seconds=self.policy.rotation_overlap_seconds,
                actor=command.actor,
                reason=command.reason,
                idempotency_key=command.idempotency_key,
                request_digest=digest,
            )
        except SfuRuntimeIdentityRepositoryError as exc:
            raise _service_error(exc) from exc

    def revoke(self, command: SfuNodeRevocationCommand) -> SfuRuntimeIdentityMutationResult:
        _validate_node_id(command.node_id)
        _validate_actor_reason_idempotency(command.actor, command.reason, command.idempotency_key)
        if command.expected_version < 1:
            raise SfuNodeIdentityError("sfu_identity_expected_version_invalid")
        digest = _json_digest(
            {
                "operation": "emergency_revoke" if command.emergency else "revoke",
                "node_id": command.node_id,
                "expected_version": command.expected_version,
                "actor": command.actor,
                "reason": command.reason,
                "idempotency_key_digest": _digest(command.idempotency_key),
            }
        )
        try:
            return self._repository.revoke_identity(
                node_id=command.node_id,
                expected_version=command.expected_version,
                revocation_max_seconds=self.policy.node_revocation_max_seconds,
                emergency=command.emergency,
                actor=command.actor,
                reason=command.reason,
                idempotency_key=command.idempotency_key,
                request_digest=digest,
            )
        except SfuRuntimeIdentityRepositoryError as exc:
            raise _service_error(exc) from exc

    def get(self, node_id: str) -> SfuRuntimeIdentityRecord:
        _validate_node_id(node_id)
        try:
            record = self._repository.get_by_node_id(node_id)
        except SfuRuntimeIdentityRepositoryError as exc:
            raise _service_error(exc) from exc
        if record is None:
            raise SfuNodeIdentityError("sfu_identity_not_found", status_code=404)
        return record

    def authorize_extension_peer(
        self,
        *,
        node_id: str,
        certificate_pem: str,
        required_role: str,
        transport_peer_verified: bool,
    ) -> SfuRuntimeIdentityRecord:
        self._require_active_mode(AUTHENTICATED_RUNTIME_EXTENSION)
        if not transport_peer_verified:
            raise SfuNodeIdentityError("sfu_extension_mtls_transport_unverified", status_code=401)
        role = _validate_required_role(required_role)
        record = self.get(node_id)
        _require_identity_role(record, role)
        public_key = _certificate_public_key(certificate_pem)
        registration = self._extension_registration(
            node_id=node_id,
            role=role,
            public_key=public_key,
            certificate_pem=certificate_pem,
            proof_nonce_digest="runtime-auth-not-persisted",
        )
        self._require_credential(record, registration.credential_fingerprint)
        return record

    def authorize_livekit_control_credential(
        self,
        *,
        node_id: str,
        configured_credential_fingerprint: str,
        required_role: str,
        tls_verified: bool,
    ) -> SfuRuntimeIdentityRecord:
        self._require_active_mode(LIVEKIT_CONTROL_API)
        if not tls_verified:
            raise SfuNodeIdentityError("sfu_livekit_tls_unverified", status_code=401)
        _validate_fingerprint(configured_credential_fingerprint)
        record = self.get(node_id)
        _require_identity_role(record, _validate_required_role(required_role))
        self._require_credential(record, configured_credential_fingerprint)
        return record

    def _require_active_mode(self, expected_mode: str) -> None:
        if self.policy.runtime_control_mode != expected_mode:
            raise SfuNodeIdentityError("sfu_runtime_control_mode_mismatch", status_code=403)
        if not self.policy.activation_enabled:
            raise SfuNodeIdentityError("sfu_runtime_activation_evidence_missing", status_code=503)

    def _require_credential(self, record: SfuRuntimeIdentityRecord, fingerprint: str) -> None:
        if record.status == "revoked":
            raise SfuNodeIdentityError("sfu_identity_revoked", status_code=403)
        now = float(self._clock())
        for credential in record.credentials:
            if credential.credential_fingerprint == fingerprint and credential.usable_at(now):
                return
        raise SfuNodeIdentityError("sfu_runtime_credential_rejected", status_code=403)

    def _validate_common(self, command: SfuNodeCredentialCommand, *, operation: str) -> None:
        _validate_node_id(command.node_id)
        _validate_actor_reason_idempotency(command.actor, command.reason, command.idempotency_key)
        if command.runtime_control_mode != self.policy.runtime_control_mode:
            raise SfuNodeIdentityError("sfu_runtime_control_mode_mismatch", status_code=409)
        _validate_roles(command.roles)
        if any(marker in command.public_key_pem.upper() for marker in _PRIVATE_MARKERS):
            raise SfuNodeIdentityError("sfu_private_key_material_forbidden")
        if command.certificate_pem and any(
            marker in command.certificate_pem.upper() for marker in _PRIVATE_MARKERS
        ):
            raise SfuNodeIdentityError("sfu_private_key_material_forbidden")
        if operation not in {"enroll", "rotate"}:
            raise SfuNodeIdentityError("sfu_identity_operation_invalid")

    def _validate_credential(
        self, command: SfuNodeCredentialCommand, *, operation: str
    ) -> tuple[SfuRuntimeCredentialRegistration, str]:
        public_key = _load_public_key(command.public_key_pem)
        public_fingerprint = _public_key_fingerprint(public_key)
        role = command.roles[0]
        if command.runtime_control_mode == AUTHENTICATED_RUNTIME_EXTENSION:
            if command.credential_kind != "mtls_client_certificate" or not command.certificate_pem:
                raise SfuNodeIdentityError("sfu_extension_certificate_required")
            registration = self._extension_registration(
                node_id=command.node_id,
                role=role,
                public_key=public_key,
                certificate_pem=command.certificate_pem,
                proof_nonce_digest=_digest(command.proof.nonce),
            )
            if command.credential_fingerprint and command.credential_fingerprint != registration.credential_fingerprint:
                raise SfuNodeIdentityError("sfu_certificate_fingerprint_mismatch", status_code=403)
        else:
            if command.credential_kind != "livekit_api_credential" or command.certificate_pem:
                raise SfuNodeIdentityError("sfu_livekit_api_credential_boundary_invalid")
            fingerprint = str(command.credential_fingerprint or "").lower()
            _validate_fingerprint(fingerprint)
            registration = SfuRuntimeCredentialRegistration(
                credential_kind="livekit_api_credential",
                public_key_fingerprint=public_fingerprint,
                credential_fingerprint=fingerprint,
                proof_nonce_digest=_digest(command.proof.nonce),
            )
        if registration.public_key_fingerprint != public_fingerprint:
            raise SfuNodeIdentityError("sfu_certificate_public_key_mismatch", status_code=403)
        _verify_proof(
            public_key,
            command=command,
            operation=operation,
            public_key_fingerprint=public_fingerprint,
            credential_fingerprint=registration.credential_fingerprint,
            now=float(self._clock()),
            allowed_skew=self.policy.proof_clock_skew_seconds,
        )
        digest = _json_digest(
            {
                "operation": operation,
                "node_id": command.node_id,
                "runtime_control_mode": command.runtime_control_mode,
                "roles": list(command.roles),
                "public_key_fingerprint": public_fingerprint,
                "credential_kind": registration.credential_kind,
                "credential_fingerprint": registration.credential_fingerprint,
                "certificate_serial": registration.certificate_serial,
                "proof_nonce_digest": registration.proof_nonce_digest,
                "proof_signature_digest": _digest(command.proof.signature),
                "proof_issued_at": command.proof.issued_at,
                "expected_version": command.expected_version,
                "actor": command.actor,
                "reason": command.reason,
                "idempotency_key_digest": _digest(command.idempotency_key),
            }
        )
        return registration, digest

    def _extension_registration(
        self,
        *,
        node_id: str,
        role: str,
        public_key,
        certificate_pem: str,
        proof_nonce_digest: str,
    ) -> SfuRuntimeCredentialRegistration:
        if not self.policy.trusted_ca_certificates:
            raise SfuNodeIdentityError("sfu_trusted_ca_unavailable", status_code=503)
        certificate = _load_certificate(certificate_pem)
        now = datetime.fromtimestamp(float(self._clock()), tz=timezone.utc)
        not_before = _certificate_time(certificate, "not_valid_before_utc")
        not_after = _certificate_time(certificate, "not_valid_after_utc")
        if now < not_before:
            raise SfuNodeIdentityError("sfu_certificate_not_yet_valid", status_code=403)
        if now > not_after:
            raise SfuNodeIdentityError("sfu_certificate_expired", status_code=403)
        if _public_key_fingerprint(certificate.public_key()) != _public_key_fingerprint(public_key):
            raise SfuNodeIdentityError("sfu_certificate_public_key_mismatch", status_code=403)
        if not any(_certificate_signed_by(certificate, ca) for ca in self.policy.trusted_ca_certificates):
            raise SfuNodeIdentityError("sfu_certificate_foreign_ca", status_code=403)
        expected_san = f"{self.policy.extension_san_prefix}/{role}/{node_id}"
        try:
            sans = tuple(
                certificate.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                .value.get_values_for_type(x509.UniformResourceIdentifier)
            )
        except x509.ExtensionNotFound as exc:
            raise SfuNodeIdentityError("sfu_certificate_san_missing", status_code=403) from exc
        if sans != (expected_san,):
            raise SfuNodeIdentityError("sfu_certificate_san_mismatch", status_code=403)
        try:
            eku = certificate.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
        except x509.ExtensionNotFound as exc:
            raise SfuNodeIdentityError("sfu_certificate_eku_missing", status_code=403) from exc
        if ExtendedKeyUsageOID.CLIENT_AUTH not in eku:
            raise SfuNodeIdentityError("sfu_certificate_eku_mismatch", status_code=403)
        fingerprint = _certificate_fingerprint(certificate)
        if fingerprint in self.policy.revoked_certificate_fingerprints:
            raise SfuNodeIdentityError("sfu_certificate_revoked", status_code=403)
        return SfuRuntimeCredentialRegistration(
            credential_kind="mtls_client_certificate",
            public_key_fingerprint=_public_key_fingerprint(public_key),
            credential_fingerprint=fingerprint,
            proof_nonce_digest=proof_nonce_digest,
            certificate_serial=format(certificate.serial_number, "x"),
            certificate_sans=sans,
            certificate_ekus=tuple(oid.dotted_string for oid in eku),
            certificate_not_before=not_before.timestamp(),
            certificate_not_after=not_after.timestamp(),
        )


def build_sfu_node_pop_message(
    *,
    operation: str,
    node_id: str,
    runtime_control_mode: str,
    roles: tuple[str, ...],
    public_key_fingerprint: str,
    credential_fingerprint: str,
    nonce: str,
    issued_at: float,
    expected_version: int,
    actor: str,
    reason: str,
    idempotency_key: str,
) -> bytes:
    return json.dumps(
        {
            "actor": actor,
            "credential_fingerprint": credential_fingerprint,
            "expected_version": expected_version,
            "idempotency_key_digest": _digest(idempotency_key),
            "issued_at": issued_at,
            "node_id": node_id,
            "nonce": nonce,
            "operation": operation,
            "protocol": "ananta.sfu-node-proof-of-possession.v1",
            "public_key_fingerprint": public_key_fingerprint,
            "reason": reason,
            "roles": list(roles),
            "runtime_control_mode": runtime_control_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def public_key_fingerprint(public_key_pem: str) -> str:
    return _public_key_fingerprint(_load_public_key(public_key_pem))


def certificate_fingerprint(certificate_pem: str) -> str:
    return _certificate_fingerprint(_load_certificate(certificate_pem))


def assert_no_private_key_material(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if "privatekey" in normalized or normalized in {
                "apisecret",
                "clientsecret",
                "credentialsecret",
                "secretkey",
            }:
                raise SfuNodeIdentityError("sfu_private_key_material_forbidden")
            assert_no_private_key_material(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            assert_no_private_key_material(item)
        return
    if isinstance(value, str) and any(marker in value.upper() for marker in _PRIVATE_MARKERS):
        raise SfuNodeIdentityError("sfu_private_key_material_forbidden")


def _verify_proof(
    public_key,
    *,
    command: SfuNodeCredentialCommand,
    operation: str,
    public_key_fingerprint: str,
    credential_fingerprint: str,
    now: float,
    allowed_skew: int,
) -> None:
    proof = command.proof
    if not proof.nonce or len(proof.nonce) < 16 or len(proof.nonce) > 256:
        raise SfuNodeIdentityError("sfu_proof_nonce_invalid", status_code=403)
    if abs(now - proof.issued_at) > allowed_skew:
        raise SfuNodeIdentityError("sfu_proof_clock_skew", status_code=403)
    try:
        signature = base64.urlsafe_b64decode(proof.signature + "=" * (-len(proof.signature) % 4))
    except Exception as exc:
        raise SfuNodeIdentityError("sfu_proof_signature_invalid", status_code=403) from exc
    message = build_sfu_node_pop_message(
        operation=operation,
        node_id=command.node_id,
        runtime_control_mode=command.runtime_control_mode,
        roles=command.roles,
        public_key_fingerprint=public_key_fingerprint,
        credential_fingerprint=credential_fingerprint,
        nonce=proof.nonce,
        issued_at=proof.issued_at,
        expected_version=command.expected_version,
        actor=command.actor,
        reason=command.reason,
        idempotency_key=command.idempotency_key,
    )
    try:
        if isinstance(public_key, ed25519.Ed25519PublicKey):
            if proof.algorithm != "Ed25519":
                raise SfuNodeIdentityError("sfu_proof_algorithm_mismatch", status_code=403)
            public_key.verify(signature, message)
        elif isinstance(public_key, ed448.Ed448PublicKey):
            if proof.algorithm != "Ed448":
                raise SfuNodeIdentityError("sfu_proof_algorithm_mismatch", status_code=403)
            public_key.verify(signature, message)
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            if proof.algorithm != "ECDSA-SHA256":
                raise SfuNodeIdentityError("sfu_proof_algorithm_mismatch", status_code=403)
            public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        elif isinstance(public_key, rsa.RSAPublicKey):
            if proof.algorithm != "RSA-PSS-SHA256":
                raise SfuNodeIdentityError("sfu_proof_algorithm_mismatch", status_code=403)
            public_key.verify(
                signature,
                message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
        else:
            raise SfuNodeIdentityError("sfu_proof_key_type_unsupported", status_code=403)
    except InvalidSignature as exc:
        raise SfuNodeIdentityError("sfu_proof_signature_invalid", status_code=403) from exc


def _certificate_signed_by(certificate: x509.Certificate, ca: x509.Certificate) -> bool:
    if certificate.issuer != ca.subject:
        return False
    try:
        constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints).value
        if not constraints.ca:
            return False
        key = ca.public_key()
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                padding.PKCS1v15(),
                certificate.signature_hash_algorithm,
            )
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                ec.ECDSA(certificate.signature_hash_algorithm),
            )
        elif isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            key.verify(certificate.signature, certificate.tbs_certificate_bytes)
        else:
            return False
    except (InvalidSignature, ValueError, x509.ExtensionNotFound):
        return False
    return True


def _certificate_time(certificate: x509.Certificate, attribute: str) -> datetime:
    value = getattr(certificate, attribute, None)
    if value is None:
        legacy = attribute.removesuffix("_utc")
        value = getattr(certificate, legacy).replace(tzinfo=timezone.utc)
    return value


def _certificate_public_key(certificate_pem: str):
    return _load_certificate(certificate_pem).public_key()


def _load_certificate(certificate_pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SfuNodeIdentityError("sfu_certificate_invalid", status_code=403) from exc


def _load_public_key(public_key_pem: str):
    if not isinstance(public_key_pem, str) or not public_key_pem.strip():
        raise SfuNodeIdentityError("sfu_public_key_required")
    if any(marker in public_key_pem.upper() for marker in _PRIVATE_MARKERS):
        raise SfuNodeIdentityError("sfu_private_key_material_forbidden")
    try:
        return serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise SfuNodeIdentityError("sfu_public_key_invalid") from exc


def _public_key_fingerprint(public_key) -> str:
    encoded = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _certificate_fingerprint(certificate: x509.Certificate) -> str:
    return f"sha256:{certificate.fingerprint(hashes.SHA256()).hex()}"


def _validate_node_id(node_id: str) -> None:
    if not isinstance(node_id, str) or not _NODE_ID.fullmatch(node_id):
        raise SfuNodeIdentityError("sfu_node_id_invalid")


def _validate_roles(roles: tuple[str, ...]) -> None:
    if len(roles) != 1 or roles[0] not in _ROLES:
        raise SfuNodeIdentityError("sfu_identity_role_invalid")


def _validate_required_role(role: str) -> str:
    if role not in _ROLES:
        raise SfuNodeIdentityError("sfu_required_role_invalid")
    return role


def _require_identity_role(record: SfuRuntimeIdentityRecord, required_role: str) -> None:
    if record.roles != (required_role,):
        raise SfuNodeIdentityError("sfu_identity_role_forbidden", status_code=403)


def _validate_actor_reason_idempotency(actor: str, reason: str, key: str) -> None:
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
        raise SfuNodeIdentityError("sfu_identity_actor_required")
    if not isinstance(reason, str) or len(reason.strip()) < 8 or len(reason) > 1000:
        raise SfuNodeIdentityError("sfu_identity_reason_required")
    if not isinstance(key, str) or len(key.strip()) < 16 or len(key) > 256:
        raise SfuNodeIdentityError("sfu_identity_idempotency_key_required")


def _validate_fingerprint(fingerprint: str) -> None:
    if not _FINGERPRINT.fullmatch(fingerprint):
        raise SfuNodeIdentityError("sfu_credential_fingerprint_invalid")


def _json_digest(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _digest(canonical)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _service_error(exc: SfuRuntimeIdentityRepositoryError) -> SfuNodeIdentityError:
    status = 503 if "unavailable" in exc.reason_code else 409
    if exc.reason_code == "sfu_identity_not_found":
        status = 404
    if exc.reason_code == "sfu_identity_revoked":
        status = 403
    return SfuNodeIdentityError(exc.reason_code, status_code=status)


__all__ = [
    "AUTHENTICATED_RUNTIME_EXTENSION",
    "LIVEKIT_CONTROL_API",
    "SFU_CONTROL",
    "SFU_OBSERVER",
    "SfuNodeCredentialCommand",
    "SfuNodeIdentityError",
    "SfuNodeIdentityService",
    "SfuNodeRevocationCommand",
    "SfuNodeTrustPolicy",
    "SfuProofOfPossession",
    "assert_no_private_key_material",
    "build_sfu_node_pop_message",
    "certificate_fingerprint",
    "public_key_fingerprint",
]
