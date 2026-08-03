"""Asymmetric Hub authority for Organization research dispatches.

The Hub owns the private signing key. Workers receive only the public
verification keyring, so a Worker service credential can authenticate the
transport without granting orchestration authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    Ed25519SigningKeyRing,
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)

_CAPABILITY_FIELD = "hub_dispatch_capability"
_CAPABILITY_SCHEMA = "organization_research_dispatch_capability.v2"
_CAPABILITY_SCOPE = "organization.planning_research.dispatch"
_CAPABILITY_NAMESPACE = "ananta.organization.planning_research.dispatch.v2"
_TOKEN_PREFIX = "ord2"
_CLAIM_FIELDS = frozenset(
    {
        "schema",
        "scope",
        "worker_url",
        "parent_task_id",
        "subtask_id",
        "organization_assignment_id",
        "context_bundle_id",
        "source_context_bundle_digest",
        "destination_binding_digest",
        "worker_job_id",
        "payload_digest",
        "jti",
        "iat",
        "exp",
    }
)


class OrganizationResearchDispatchCapabilityError(ValueError):
    """Stable fail-closed error raised for invalid Hub dispatch authority."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def organization_research_dispatch_payload_digest(
    payload: Mapping[str, Any],
) -> str:
    """Hash the complete transport payload except its detached capability."""

    unsigned = {
        str(key): value
        for key, value in dict(payload).items()
        if str(key) != _CAPABILITY_FIELD
    }
    try:
        encoded = json.dumps(
            unsigned,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OrganizationResearchDispatchCapabilityError(
            "organization_research_dispatch_payload_invalid"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _organization_assignment_id(payload: Mapping[str, Any]) -> str:
    worker_context = payload.get("worker_execution_context")
    if not isinstance(worker_context, Mapping):
        return ""
    binding = worker_context.get("planning_research_assignment")
    if not isinstance(binding, Mapping):
        return ""
    return str(binding.get("assignment_id") or "").strip()


class OrganizationResearchDispatchCapabilityIssuer:
    """Hub-only issuer backed by a private Ed25519 signing keyring."""

    def __init__(
        self,
        signing_key_ring: Ed25519SigningKeyRing,
        *,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(signing_key_ring, Ed25519SigningKeyRing):
            raise OrganizationResearchDispatchCapabilityError(
                "organization_research_dispatch_ed25519_signer_required"
            )
        self._key_ring = signing_key_ring
        self._clock = clock
        self._nonce_factory = nonce_factory or (
            lambda: secrets.token_urlsafe(24)
        )

    def issue(
        self,
        *,
        payload: Mapping[str, Any],
        worker_url: str,
        source_context_bundle_digest: str,
        destination_binding_digest: str,
        worker_job_id: str,
        ttl_seconds: int = 300,
    ) -> str:
        now = int(self._clock())
        claims = {
            "schema": _CAPABILITY_SCHEMA,
            "scope": _CAPABILITY_SCOPE,
            "worker_url": str(worker_url or "").strip(),
            "parent_task_id": str(payload.get("parent_task_id") or "").strip(),
            "subtask_id": str(payload.get("id") or "").strip(),
            "organization_assignment_id": _organization_assignment_id(
                payload
            ),
            "context_bundle_id": str(
                payload.get("context_bundle_id") or ""
            ).strip(),
            "source_context_bundle_digest": str(
                source_context_bundle_digest or ""
            ).strip(),
            "destination_binding_digest": str(
                destination_binding_digest or ""
            ).strip(),
            "worker_job_id": str(worker_job_id or "").strip(),
            "payload_digest": organization_research_dispatch_payload_digest(
                payload
            ),
            "jti": str(self._nonce_factory() or "").strip(),
            "iat": now,
            "exp": now + max(60, min(int(ttl_seconds), 600)),
        }
        required = (
            "worker_url",
            "parent_task_id",
            "subtask_id",
            "organization_assignment_id",
            "context_bundle_id",
            "source_context_bundle_digest",
            "destination_binding_digest",
            "worker_job_id",
            "payload_digest",
        )
        if any(not claims[field] for field in required):
            self._fail("organization_research_dispatch_binding_required")
        if not 24 <= len(claims["jti"]) <= 128:
            self._fail(
                "organization_research_dispatch_capability_nonce_invalid"
            )

        selected_key_id = self._key_ring.active_key_id
        protected_header = self._protected_header(selected_key_id)
        try:
            key_id, signature = self._key_ring.sign(
                namespace=_CAPABILITY_NAMESPACE,
                payload=claims,
                key_id=selected_key_id,
                protected_header=protected_header,
            )
        except RuntimeAuthorizationCryptoError as exc:
            raise OrganizationResearchDispatchCapabilityError(
                exc.reason_code
            ) from exc
        if key_id != selected_key_id:
            self._fail(
                "organization_research_dispatch_capability_signature_invalid"
            )

        payload_segment = self._encode(
            json.dumps(
                claims,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        key_segment = self._encode(key_id.encode("utf-8"))
        signature_segment = self._encode(base64.b64decode(signature))
        return (
            f"{_TOKEN_PREFIX}.{key_segment}.{payload_segment}."
            f"{signature_segment}"
        )

    @staticmethod
    def _protected_header(key_id: str) -> dict[str, str]:
        return {
            "schema": _CAPABILITY_SCHEMA,
            "algorithm": ED25519_ALGORITHM,
            "key_id": key_id,
        }

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _fail(reason_code: str) -> None:
        raise OrganizationResearchDispatchCapabilityError(reason_code)


class OrganizationResearchDispatchCapabilityVerifier:
    """Worker-only verifier backed by public Ed25519 keys."""

    def __init__(
        self,
        verification_key_ring: Ed25519VerificationKeyRing,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(
            verification_key_ring,
            Ed25519VerificationKeyRing,
        ):
            raise OrganizationResearchDispatchCapabilityError(
                "organization_research_dispatch_ed25519_verifier_required"
            )
        self._key_ring = verification_key_ring
        self._clock = clock

    def verify(
        self,
        token: str,
        *,
        payload: Mapping[str, Any],
        worker_url: str,
    ) -> dict[str, Any]:
        parts = str(token or "").split(".")
        if len(parts) != 4 or parts[0] != _TOKEN_PREFIX:
            self._fail("organization_research_dispatch_capability_invalid")
        try:
            key_id = self._decode(parts[1]).decode("utf-8")
            claims = json.loads(self._decode(parts[2]).decode("utf-8"))
            signature = base64.b64encode(
                self._decode(parts[3])
            ).decode("ascii")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OrganizationResearchDispatchCapabilityError(
                "organization_research_dispatch_capability_payload_invalid"
            ) from exc
        if not isinstance(claims, dict) or set(claims) != _CLAIM_FIELDS:
            self._fail(
                "organization_research_dispatch_capability_payload_invalid"
            )
        try:
            self._key_ring.verify(
                namespace=_CAPABILITY_NAMESPACE,
                payload=claims,
                key_id=key_id,
                signature=signature,
                contract_id=str(claims.get("jti") or ""),
                protected_header=(
                    OrganizationResearchDispatchCapabilityIssuer
                    ._protected_header(key_id)
                ),
            )
        except RuntimeAuthorizationCryptoError as exc:
            reason_code = (
                exc.reason_code
                if exc.reason_code
                in {"signing_key_revoked", "signed_contract_revoked"}
                else (
                    "organization_research_dispatch_capability_signature_invalid"
                )
            )
            raise OrganizationResearchDispatchCapabilityError(
                reason_code
            ) from exc

        if claims.get("schema") != _CAPABILITY_SCHEMA:
            self._fail(
                "organization_research_dispatch_capability_schema_invalid"
            )
        if claims.get("scope") != _CAPABILITY_SCOPE:
            self._fail(
                "organization_research_dispatch_capability_scope_invalid"
            )
        issued_at = self._strict_timestamp(claims.get("iat"))
        expires_at = self._strict_timestamp(claims.get("exp"))
        now = int(self._clock())
        if (
            issued_at > now + 30
            or expires_at <= now
            or expires_at <= issued_at
            or expires_at - issued_at > 600
        ):
            self._fail(
                "organization_research_dispatch_capability_expired"
            )
        if str(claims.get("worker_url") or "") != str(
            worker_url or ""
        ).strip():
            self._fail("organization_research_dispatch_worker_mismatch")
        expected_bindings = {
            "parent_task_id": str(
                payload.get("parent_task_id") or ""
            ).strip(),
            "subtask_id": str(payload.get("id") or "").strip(),
            "context_bundle_id": str(
                payload.get("context_bundle_id") or ""
            ).strip(),
            "payload_digest": organization_research_dispatch_payload_digest(
                payload
            ),
            "organization_assignment_id": _organization_assignment_id(
                payload
            ),
        }
        if any(
            not expected
            or str(claims.get(field) or "") != expected
            for field, expected in expected_bindings.items()
        ):
            self._fail("organization_research_dispatch_payload_mismatch")
        if not 24 <= len(str(claims.get("jti") or "")) <= 128:
            self._fail(
                "organization_research_dispatch_capability_nonce_invalid"
            )
        return dict(claims)

    @staticmethod
    def _strict_timestamp(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise OrganizationResearchDispatchCapabilityError(
                "organization_research_dispatch_capability_time_invalid"
            )
        return value

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )

    @staticmethod
    def _fail(reason_code: str) -> None:
        raise OrganizationResearchDispatchCapabilityError(reason_code)


def get_organization_research_dispatch_capability_issuer(
) -> OrganizationResearchDispatchCapabilityIssuer:
    """Compose the issuer from the existing Hub-only workflow signer."""

    from agent.services.workflow_hub_task_gateway_runtime import (
        WorkflowHubTaskConfigurationError,
        get_workflow_authorization_key_ring,
    )

    try:
        key_ring = get_workflow_authorization_key_ring()
    except WorkflowHubTaskConfigurationError as exc:
        raise OrganizationResearchDispatchCapabilityError(
            "organization_research_dispatch_signing_keyring_unavailable"
        ) from exc
    if not isinstance(key_ring, Ed25519SigningKeyRing):
        raise OrganizationResearchDispatchCapabilityError(
            "organization_research_dispatch_ed25519_signer_required"
        )
    return OrganizationResearchDispatchCapabilityIssuer(key_ring)


__all__ = [
    "OrganizationResearchDispatchCapabilityError",
    "OrganizationResearchDispatchCapabilityIssuer",
    "OrganizationResearchDispatchCapabilityVerifier",
    "get_organization_research_dispatch_capability_issuer",
    "organization_research_dispatch_payload_digest",
]
