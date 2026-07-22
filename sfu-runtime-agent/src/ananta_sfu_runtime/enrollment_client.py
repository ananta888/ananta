"""One-shot HTTPS enrollment client using a non-exportable key provider."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from .key_providers import NonExportableKeyProvider, key_provider_from_environment

MAX_RESPONSE_BYTES = 262_144


class EnrollmentClientError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class EnrollmentClientConfig:
    hub_url: str
    hub_ca_file: Path
    admin_token_file: Path
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.hub_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise EnrollmentClientError("hub_https_endpoint_required")
        if not self.hub_ca_file.is_file():
            raise EnrollmentClientError("hub_ca_unavailable")
        if not self.admin_token_file.is_file():
            raise EnrollmentClientError("enrollment_admin_token_unavailable")


class SfuRuntimeEnrollmentClient:
    def __init__(
        self,
        config: EnrollmentClientConfig,
        key_provider: NonExportableKeyProvider,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self._config = config
        self._keys = key_provider
        self._session = session or requests.Session()

    def enroll_or_rotate(
        self,
        *,
        node_id: str,
        role: str,
        certificate_file: Path,
        expected_version: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        nonce: str,
        issued_at: float,
    ) -> Mapping[str, object]:
        if role not in {"sfu_control", "sfu_observer"}:
            raise EnrollmentClientError("runtime_role_invalid")
        try:
            certificate_pem = certificate_file.read_text(encoding="ascii")
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise EnrollmentClientError("runtime_certificate_invalid") from exc
        public_key_pem = self._keys.public_key_pem()
        public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
        certificate_public = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        provider_public = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if certificate_public != provider_public:
            raise EnrollmentClientError("runtime_certificate_key_mismatch")
        public_fingerprint = f"sha256:{hashlib.sha256(provider_public).hexdigest()}"
        credential_fingerprint = f"sha256:{certificate.fingerprint(hashes.SHA256()).hex()}"
        operation = "enroll" if expected_version == 0 else "rotate"
        message = _proof_message(
            operation=operation,
            node_id=node_id,
            role=role,
            public_key_fingerprint=public_fingerprint,
            credential_fingerprint=credential_fingerprint,
            nonce=nonce,
            issued_at=issued_at,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        signature = base64.urlsafe_b64encode(self._keys.sign(message)).decode("ascii").rstrip("=")
        payload = {
            "runtime_control_mode": "authenticated_runtime_extension",
            "roles": [role],
            "public_key_pem": public_key_pem,
            "credential_kind": "mtls_client_certificate",
            "credential_fingerprint": credential_fingerprint,
            "certificate_pem": certificate_pem,
            "proof": {
                "algorithm": self._keys.algorithm,
                "signature": signature,
                "nonce": nonce,
                "issued_at": issued_at,
            },
            "expected_version": expected_version,
            "actor": actor,
            "reason": reason,
        }
        if operation == "enroll":
            payload["node_id"] = node_id
            path = "/api/admin/webrtc/sfu-nodes/enroll"
        else:
            path = f"/api/admin/webrtc/sfu-nodes/{node_id}/rotate"
        return self._post(path, payload, idempotency_key)

    def revoke(
        self,
        *,
        node_id: str,
        expected_version: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        emergency: bool = True,
    ) -> Mapping[str, object]:
        return self._post(
            f"/api/admin/webrtc/sfu-nodes/{node_id}/revoke",
            {
                "expected_version": expected_version,
                "emergency": emergency,
                "actor": actor,
                "reason": reason,
            },
            idempotency_key,
        )

    def _post(
        self, path: str, payload: Mapping[str, object], idempotency_key: str
    ) -> Mapping[str, object]:
        token = _read_secret(self._config.admin_token_file)
        try:
            response = self._session.post(
                f"{self._config.hub_url.rstrip('/')}{path}",
                json=dict(payload),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": idempotency_key,
                    "Content-Type": "application/json",
                },
                timeout=self._config.timeout_seconds,
                verify=str(self._config.hub_ca_file),
                allow_redirects=False,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise EnrollmentClientError("hub_enrollment_unavailable") from exc
        if 300 <= response.status_code < 400:
            raise EnrollmentClientError("hub_enrollment_redirect_forbidden")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise EnrollmentClientError("hub_enrollment_response_oversize")
        try:
            body = response.json()
        except ValueError as exc:
            raise EnrollmentClientError("hub_enrollment_response_invalid") from exc
        if not isinstance(body, Mapping):
            raise EnrollmentClientError("hub_enrollment_response_invalid")
        if not 200 <= response.status_code < 300:
            reason = (body.get("data") or {}).get("reason_code") if isinstance(body.get("data"), Mapping) else None
            raise EnrollmentClientError(str(reason or "hub_enrollment_rejected"))
        return body


def _proof_message(
    *,
    operation: str,
    node_id: str,
    role: str,
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
            "idempotency_key_digest": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            "issued_at": issued_at,
            "node_id": node_id,
            "nonce": nonce,
            "operation": operation,
            "protocol": "ananta.sfu-node-proof-of-possession.v1",
            "public_key_fingerprint": public_key_fingerprint,
            "reason": reason,
            "roles": [role],
            "runtime_control_mode": "authenticated_runtime_extension",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _read_secret(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise EnrollmentClientError("enrollment_admin_token_permissions_invalid")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 16:
        raise EnrollmentClientError("enrollment_admin_token_invalid")
    return token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("enroll", "rotate", "revoke"))
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--role", choices=("sfu_control", "sfu_observer"), default="sfu_control")
    parser.add_argument("--certificate-file", type=Path)
    parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--nonce")
    parser.add_argument("--issued-at", type=float)
    args = parser.parse_args()
    client = SfuRuntimeEnrollmentClient(
        EnrollmentClientConfig(
            hub_url=os.environ["ANANTA_HUB_URL"],
            hub_ca_file=Path(os.environ["ANANTA_HUB_CA_FILE"]),
            admin_token_file=Path(os.environ["ANANTA_ENROLLMENT_ADMIN_TOKEN_FILE"]),
        ),
        key_provider_from_environment(),
    )
    if args.operation == "revoke":
        result = client.revoke(
            node_id=args.node_id,
            expected_version=args.expected_version,
            actor=args.actor,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
        )
    else:
        if args.certificate_file is None or args.nonce is None or args.issued_at is None:
            raise EnrollmentClientError("enrollment_proof_arguments_required")
        result = client.enroll_or_rotate(
            node_id=args.node_id,
            role=args.role,
            certificate_file=args.certificate_file,
            expected_version=args.expected_version,
            actor=args.actor,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
            nonce=args.nonce,
            issued_at=args.issued_at,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


__all__ = [
    "EnrollmentClientConfig",
    "EnrollmentClientError",
    "SfuRuntimeEnrollmentClient",
    "main",
]
