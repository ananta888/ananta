"""Verify-only authorization adapters for the Native Worker runtime."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping

from agent.services.workflow_runtime.security import (
    AUTHORIZATION_ENVELOPE_SCHEMA,
    AuthorizationVerifier,
    InMemoryReplayNonceStore,
    RuntimeAuthorizationEnvelope,
)
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)


class HubBackedNativeAuthorizationVerifier:
    """Verify every envelope at Hub without loading Hub signing material.

    The Worker performs deterministic binding, expiry, scope, budget and replay
    checks locally. Signature, revocation and current policy authority are
    verified by the Hub callback for both reads and writes.
    """

    def __init__(self) -> None:
        self._nonces: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def authorize(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
        policy_version: str,
        tool: str = "",
        artifact: str = "",
        requested_budget: dict[str, int | float] | None = None,
        consume_nonce: bool = False,
        writing: bool = False,
        hub_revalidator: Callable[[RuntimeAuthorizationEnvelope], bool] | None = None,
        now: float | None = None,
    ) -> None:
        del writing
        timestamp = float(time.time() if now is None else now)
        self._assert_structure(envelope, now=timestamp)
        expected = {
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "step_id": step_id,
            "plan_hash": plan_hash,
            "policy_version": policy_version,
        }
        for name, value in expected.items():
            if str(getattr(envelope, name)) != str(value):
                raise ValueError(f"authorization_{name}_mismatch")
        if tool and tool not in envelope.allowed_tools:
            raise ValueError("authorization_tool_denied")
        if artifact and artifact not in envelope.allowed_artifacts:
            raise ValueError("authorization_artifact_denied")
        for name, value in dict(requested_budget or {}).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or name not in envelope.budgets
                or value > envelope.budgets[name]
            ):
                raise ValueError("authorization_budget_exceeded")
        if hub_revalidator is None or not bool(hub_revalidator(envelope)):
            raise ValueError("authorization_hub_verification_failed")
        if consume_nonce:
            self._consume_nonce(
                tenant_id=envelope.tenant_id,
                nonce=envelope.nonce,
                expires_at=envelope.expires_at,
                now=timestamp,
            )

    @staticmethod
    def _assert_structure(
        envelope: RuntimeAuthorizationEnvelope,
        *,
        now: float,
    ) -> None:
        required = (
            envelope.envelope_id,
            envelope.tenant_id,
            envelope.workflow_id,
            envelope.run_id,
            envelope.step_id,
            envelope.plan_hash,
            envelope.policy_version,
            envelope.nonce,
            envelope.key_id,
            envelope.signature,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("authorization_binding_required")
        if envelope.schema != AUTHORIZATION_ENVELOPE_SCHEMA:
            raise ValueError("authorization_schema_unsupported")
        if envelope.expires_at <= envelope.issued_at:
            raise ValueError("authorization_expiry_invalid")
        if now < envelope.issued_at:
            raise ValueError("authorization_not_yet_valid")
        if now >= envelope.expires_at:
            raise ValueError("authorization_expired")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in envelope.budgets.values()
        ):
            raise ValueError("authorization_budget_invalid")

    def _consume_nonce(
        self,
        *,
        tenant_id: str,
        nonce: str,
        expires_at: float,
        now: float,
    ) -> None:
        key = (str(tenant_id), str(nonce))
        with self._lock:
            self._nonces = {item: expiry for item, expiry in self._nonces.items() if expiry > now}
            if key in self._nonces:
                raise ValueError("authorization_replay_detected")
            self._nonces[key] = float(expires_at)


def load_ed25519_verification_key_ring(
    environment: Mapping[str, str] | None = None,
) -> Ed25519VerificationKeyRing | None:
    """Load a public-only workflow keyring for Worker-side verification."""

    source = os.environ if environment is None else environment
    raw_path = str(source.get("ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE") or "").strip()
    if not raw_path:
        return None
    try:
        raw = read_file_managed_bytes(
            raw_path,
            description="workflow Worker verification keyring file",
            max_bytes=65_536,
        )
    except FileCredentialConfigurationError as exc:
        raise ValueError("workflow_worker_verification_keyring_unsafe") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("workflow_worker_verification_keyring_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("workflow_worker_verification_keyring_invalid")
    try:
        key_ring = Ed25519VerificationKeyRing.from_mapping(decoded)
    except RuntimeAuthorizationCryptoError as exc:
        raise ValueError(exc.reason_code) from exc
    return key_ring


def load_ed25519_native_authorization_verifier(
    environment: Mapping[str, str] | None = None,
) -> AuthorizationVerifier | None:
    """Load the Native verifier without exposing a Worker signing seam."""

    key_ring = load_ed25519_verification_key_ring(environment)
    if key_ring is None:
        return None
    return AuthorizationVerifier(key_ring, InMemoryReplayNonceStore())


__all__ = [
    "HubBackedNativeAuthorizationVerifier",
    "load_ed25519_native_authorization_verifier",
    "load_ed25519_verification_key_ring",
]
