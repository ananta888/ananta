"""Signed authorization and checkpoint contracts for delegated runtimes."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import canonical_json, contains_sensitive_keys, redact_json
from agent.services.workflow_runtime.errors import ContractValidationError, SignatureValidationError
from ananta_contracts.provider_execution import (
    ProviderBindingAuthorization,
    ProviderExecutionBindingError,
    ProviderProfileAttemptPlanEntry,
)
from ananta_contracts.runtime_authorization_crypto import (
    RuntimeAuthorizationCryptoError,
)

AUTHORIZATION_ENVELOPE_SCHEMA = "ananta.runtime_authorization.v1"
WORKFLOW_STATE_SCHEMA = "ananta.workflow_state.v1"
SIGNED_CHECKPOINT_SCHEMA = "ananta.workflow_checkpoint.v1"


class HmacKeyRing:
    """Small injectable signer with explicit key rotation and revocation.

    Keys are configuration/runtime secrets and must never be serialized into a
    workflow contract. Old keys may remain verification-only during rotation.
    """

    def __init__(self, keys: Mapping[str, str | bytes], *, active_key_id: str):
        normalized = {
            str(key_id): value.encode("utf-8") if isinstance(value, str) else bytes(value)
            for key_id, value in keys.items()
        }
        if active_key_id not in normalized:
            raise ValueError("active_signing_key_missing")
        if any(len(value) < 16 for value in normalized.values()):
            raise ValueError("signing_key_too_short")
        self._keys = normalized
        self._active_key_id = str(active_key_id)
        self._revoked_keys: set[str] = set()
        self._revoked_contracts: set[str] = set()
        self._lock = threading.RLock()

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    @property
    def signature_algorithm(self) -> str:
        return "hmac-sha256"

    def rotate(self, *, key_id: str, key: str | bytes) -> None:
        value = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        if len(value) < 16:
            raise ValueError("signing_key_too_short")
        with self._lock:
            self._keys[str(key_id)] = value
            self._active_key_id = str(key_id)

    def revoke_key(self, key_id: str) -> None:
        with self._lock:
            self._revoked_keys.add(str(key_id))

    def revoke_contract(self, contract_id: str) -> None:
        with self._lock:
            self._revoked_contracts.add(str(contract_id))

    def sign(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str | None = None,
    ) -> tuple[str, str]:
        with self._lock:
            selected_key_id = str(key_id or self._active_key_id)
            if selected_key_id in self._revoked_keys:
                raise SignatureValidationError("active_signing_key_revoked")
            key = self._keys.get(selected_key_id)
            if key is None:
                raise SignatureValidationError("signing_key_unknown")
            signature = _signature(key, namespace=namespace, payload=payload)
            return selected_key_id, signature

    def verify(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str,
        signature: str,
        contract_id: str,
    ) -> None:
        with self._lock:
            if str(contract_id) in self._revoked_contracts:
                raise SignatureValidationError("signed_contract_revoked")
            if str(key_id) in self._revoked_keys:
                raise SignatureValidationError("signing_key_revoked")
            key = self._keys.get(str(key_id))
        if key is None:
            raise SignatureValidationError("signing_key_unknown")
        expected = _signature(key, namespace=namespace, payload=payload)
        if not hmac.compare_digest(expected, str(signature)):
            raise SignatureValidationError("signature_invalid")


class SignatureVerificationKeyRingPort(Protocol):
    @property
    def signature_algorithm(self) -> str: ...

    def verify(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str,
        signature: str,
        contract_id: str,
    ) -> None: ...


class SignatureSigningKeyRingPort(SignatureVerificationKeyRingPort, Protocol):
    @property
    def active_key_id(self) -> str: ...

    def sign(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str | None = None,
    ) -> tuple[str, str]: ...


class ReplayNonceStore(Protocol):
    def consume(self, *, tenant_id: str, nonce: str, expires_at: float) -> bool: ...


class InMemoryReplayNonceStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._values: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def consume(self, *, tenant_id: str, nonce: str, expires_at: float) -> bool:
        now = float(self._clock())
        key = (str(tenant_id), str(nonce))
        with self._lock:
            self._values = {item: expiry for item, expiry in self._values.items() if expiry > now}
            if key in self._values:
                return False
            self._values[key] = float(expires_at)
            return True


@dataclass(frozen=True)
class RuntimeAuthorizationEnvelope:
    envelope_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    allowed_tools: tuple[str, ...]
    allowed_artifacts: tuple[str, ...]
    budgets: dict[str, int | float]
    issued_at: float
    expires_at: float
    nonce: str
    key_id: str
    signature: str
    allowed_provider_bindings: tuple[ProviderBindingAuthorization, ...] = ()
    provider_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...] = ()
    schema: str = AUTHORIZATION_ENVELOPE_SCHEMA

    @classmethod
    def issue(
        cls,
        *,
        key_ring: SignatureSigningKeyRingPort,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
        policy_version: str,
        allowed_tools: tuple[str, ...] | list[str] = (),
        allowed_artifacts: tuple[str, ...] | list[str] = (),
        allowed_provider_bindings: (
            tuple[ProviderBindingAuthorization | Mapping[str, Any], ...]
            | list[ProviderBindingAuthorization | Mapping[str, Any]]
        ) = (),
        provider_attempt_plan: (
            tuple[ProviderProfileAttemptPlanEntry | Mapping[str, Any], ...]
            | list[ProviderProfileAttemptPlanEntry | Mapping[str, Any]]
        ) = (),
        budgets: dict[str, int | float] | None = None,
        ttl_seconds: float = 300.0,
        now: float | None = None,
        envelope_id: str | None = None,
        nonce: str | None = None,
    ) -> "RuntimeAuthorizationEnvelope":
        timestamp = float(now if now is not None else time.time())
        if ttl_seconds <= 0:
            raise ContractValidationError("authorization_ttl_invalid")
        unsigned = cls(
            envelope_id=str(envelope_id or f"rae-{uuid.uuid4().hex}"),
            tenant_id=str(tenant_id).strip(),
            workflow_id=str(workflow_id).strip(),
            run_id=str(run_id).strip(),
            step_id=str(step_id).strip(),
            plan_hash=str(plan_hash).strip(),
            policy_version=str(policy_version).strip(),
            allowed_tools=_clean_tuple(allowed_tools),
            allowed_artifacts=_clean_tuple(allowed_artifacts),
            budgets=_normalize_budgets(budgets or {}),
            issued_at=timestamp,
            expires_at=timestamp + float(ttl_seconds),
            nonce=str(nonce or uuid.uuid4().hex),
            key_id="",
            signature="",
            allowed_provider_bindings=_normalize_provider_bindings(allowed_provider_bindings),
            provider_attempt_plan=_normalize_provider_attempt_plan(provider_attempt_plan),
        )
        unsigned._assert_structure()
        key_id = key_ring.active_key_id
        payload = replace(unsigned, key_id=key_id)._signing_payload()
        actual_key_id, signature = _sign_contract(
            key_ring,
            namespace=AUTHORIZATION_ENVELOPE_SCHEMA,
            payload=payload,
            key_id=key_id,
        )
        return replace(unsigned, key_id=actual_key_id, signature=signature)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "RuntimeAuthorizationEnvelope":
        from agent.services.workflow_runtime.compatibility import (
            upcast_runtime_contract_for_loading,
        )

        raw = upcast_runtime_contract_for_loading(raw, contract_type="authorization")
        return cls(
            envelope_id=str(raw.get("envelope_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            allowed_tools=_clean_tuple(raw.get("allowed_tools") or ()),
            allowed_artifacts=_clean_tuple(raw.get("allowed_artifacts") or ()),
            budgets=_normalize_budgets(dict(raw.get("budgets") or {})),
            issued_at=float(raw.get("issued_at") or 0),
            expires_at=float(raw.get("expires_at") or 0),
            nonce=str(raw.get("nonce") or ""),
            key_id=str(raw.get("key_id") or ""),
            signature=str(raw.get("signature") or ""),
            allowed_provider_bindings=_normalize_provider_bindings(raw.get("allowed_provider_bindings") or ()),
            provider_attempt_plan=_normalize_provider_attempt_plan(raw.get("provider_attempt_plan") or ()),
            schema=str(raw.get("schema") or AUTHORIZATION_ENVELOPE_SCHEMA),
        )

    def verify(
        self,
        *,
        key_ring: SignatureVerificationKeyRingPort,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
        policy_version: str,
        now: float | None = None,
    ) -> None:
        self._assert_structure()
        expected = {
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "step_id": step_id,
            "plan_hash": plan_hash,
            "policy_version": policy_version,
        }
        for name, value in expected.items():
            if str(getattr(self, name)) != str(value):
                raise SignatureValidationError(f"authorization_{name}_mismatch")
        timestamp = float(now if now is not None else time.time())
        if timestamp < self.issued_at:
            raise SignatureValidationError("authorization_not_yet_valid")
        if timestamp >= self.expires_at:
            raise SignatureValidationError("authorization_expired")
        _verify_contract(
            key_ring,
            namespace=AUTHORIZATION_ENVELOPE_SCHEMA,
            payload=self._signing_payload(),
            key_id=self.key_id,
            signature=self.signature,
            contract_id=self.envelope_id,
        )

    def _assert_structure(self) -> None:
        required = (
            self.envelope_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.plan_hash,
            self.policy_version,
            self.nonce,
        )
        if any(not value for value in required):
            raise ContractValidationError("authorization_binding_required")
        if self.schema != AUTHORIZATION_ENVELOPE_SCHEMA:
            raise ContractValidationError("authorization_schema_unsupported")
        if self.expires_at <= self.issued_at:
            raise ContractValidationError("authorization_expiry_invalid")
        if any(value < 0 for value in self.budgets.values()):
            raise ContractValidationError("authorization_budget_invalid")
        if len(self.allowed_provider_bindings) > 8:
            raise ContractValidationError("authorization_provider_binding_limit_exceeded")
        if len(self.provider_attempt_plan) > 8:
            raise ContractValidationError("authorization_provider_attempt_plan_limit_exceeded")
        try:
            for item in self.allowed_provider_bindings:
                item.validate()
            for item in self.provider_attempt_plan:
                item.validate()
        except (AttributeError, ProviderExecutionBindingError) as exc:
            raise ContractValidationError("authorization_provider_binding_invalid") from exc
        if self.provider_attempt_plan:
            allowed = {item.binding_id: item for item in self.allowed_provider_bindings}
            planned = {item.binding_id: item.binding_authorization for item in self.provider_attempt_plan}
            if not allowed or set(planned) != set(allowed) or any(planned[key] != allowed[key] for key in planned):
                raise ContractValidationError("authorization_provider_attempt_plan_binding_mismatch")
            raw_maximum = self.budgets.get("provider_attempts")
            if (
                raw_maximum is None
                or isinstance(raw_maximum, bool)
                or int(raw_maximum) != sum(item.maximum_attempts for item in self.provider_attempt_plan)
            ):
                raise ContractValidationError("authorization_provider_attempt_plan_budget_mismatch")

    def _signing_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("signature", None)
        return payload

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "envelope_id": self.envelope_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "allowed_tools": list(self.allowed_tools),
            "allowed_artifacts": list(self.allowed_artifacts),
            "budgets": dict(self.budgets),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "key_id": self.key_id,
            "signature": self.signature,
        }
        if self.allowed_provider_bindings:
            payload["allowed_provider_bindings"] = [item.to_dict() for item in self.allowed_provider_bindings]
        if self.provider_attempt_plan:
            payload["provider_attempt_plan"] = [item.to_dict() for item in self.provider_attempt_plan]
        if redacted:
            payload["nonce"] = "[REDACTED]"
            payload["signature"] = "[REDACTED]"
        return payload


def _normalize_provider_bindings(
    raw: object,
) -> tuple[ProviderBindingAuthorization, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ContractValidationError("authorization_provider_bindings_invalid")
    values: list[ProviderBindingAuthorization] = []
    seen_ids: set[str] = set()
    try:
        for item in raw:
            value = (
                item
                if isinstance(item, ProviderBindingAuthorization)
                else ProviderBindingAuthorization.from_mapping(item)
            )
            value.validate()
            if value.binding_id in seen_ids:
                raise ContractValidationError("authorization_provider_binding_duplicate")
            seen_ids.add(value.binding_id)
            values.append(value)
    except ProviderExecutionBindingError as exc:
        raise ContractValidationError(exc.reason_code) from exc
    return tuple(
        sorted(
            values,
            key=lambda item: (
                item.binding_id,
                item.provider_id,
                item.model_id,
            ),
        )
    )


def _normalize_provider_attempt_plan(
    raw: object,
) -> tuple[ProviderProfileAttemptPlanEntry, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ContractValidationError("authorization_provider_attempt_plan_invalid")
    values: list[ProviderProfileAttemptPlanEntry] = []
    seen_profiles: set[str] = set()
    seen_bindings: set[str] = set()
    try:
        for item in raw:
            value = (
                item
                if isinstance(item, ProviderProfileAttemptPlanEntry)
                else ProviderProfileAttemptPlanEntry.from_mapping(item)
            )
            value.validate()
            if value.profile_id in seen_profiles or value.binding_id in seen_bindings:
                raise ContractValidationError("authorization_provider_attempt_plan_duplicate")
            seen_profiles.add(value.profile_id)
            seen_bindings.add(value.binding_id)
            values.append(value)
    except ProviderExecutionBindingError as exc:
        raise ContractValidationError(exc.reason_code) from exc
    return tuple(values)


class AuthorizationVerifier:
    """Local scope gate with optional one-shot replay protection and hub revalidation."""

    def __init__(
        self,
        key_ring: SignatureVerificationKeyRingPort,
        nonce_store: ReplayNonceStore | None = None,
    ):
        self._key_ring = key_ring
        self._nonce_store = nonce_store

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
        envelope.verify(
            key_ring=self._key_ring,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            plan_hash=plan_hash,
            policy_version=policy_version,
            now=now,
        )
        if tool and tool not in envelope.allowed_tools:
            raise SignatureValidationError("authorization_tool_denied")
        if artifact and artifact not in envelope.allowed_artifacts:
            raise SignatureValidationError("authorization_artifact_denied")
        for name, value in _normalize_budgets(requested_budget or {}).items():
            if name not in envelope.budgets or value > envelope.budgets[name]:
                raise SignatureValidationError("authorization_budget_exceeded")
        if consume_nonce:
            if self._nonce_store is None:
                raise SignatureValidationError("authorization_replay_store_required")
            if not self._nonce_store.consume(
                tenant_id=envelope.tenant_id,
                nonce=envelope.nonce,
                expires_at=envelope.expires_at,
            ):
                raise SignatureValidationError("authorization_replay_detected")
        if writing:
            if hub_revalidator is None or not bool(hub_revalidator(envelope)):
                raise SignatureValidationError("authorization_hub_revalidation_failed")


@dataclass(frozen=True)
class WorkflowState:
    business_data: dict[str, Any] = field(default_factory=dict)
    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    secret_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    open_gates: tuple[str, ...] = ()
    schema: str = WORKFLOW_STATE_SCHEMA

    def assert_safe(self) -> None:
        if self.schema != WORKFLOW_STATE_SCHEMA:
            raise ContractValidationError("workflow_state_schema_unsupported")
        if contains_sensitive_keys(self.business_data) or contains_sensitive_keys(self.runtime_metadata):
            raise ContractValidationError("workflow_state_embedded_secret_denied")
        if any(not str(value).strip() for value in (*self.secret_refs, *self.artifact_refs, *self.open_gates)):
            raise ContractValidationError("workflow_state_empty_reference")
        if any("://" not in reference for reference in self.secret_refs):
            raise ContractValidationError("workflow_state_secret_reference_invalid")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "WorkflowState":
        from agent.services.workflow_runtime.compatibility import (
            upcast_runtime_contract_for_loading,
        )

        raw = upcast_runtime_contract_for_loading(raw, contract_type="state")
        state = cls(
            business_data=dict(raw.get("business_data") or {}),
            runtime_metadata=dict(raw.get("runtime_metadata") or {}),
            secret_refs=_clean_tuple(raw.get("secret_refs") or ()),
            artifact_refs=_clean_tuple(raw.get("artifact_refs") or ()),
            open_gates=_clean_tuple(raw.get("open_gates") or ()),
            schema=str(raw.get("schema") or WORKFLOW_STATE_SCHEMA),
        )
        state.assert_safe()
        return state

    def to_dict(self) -> dict[str, Any]:
        self.assert_safe()
        return {
            "schema": self.schema,
            "business_data": dict(redact_json(self.business_data)),
            "runtime_metadata": dict(redact_json(self.runtime_metadata)),
            "secret_refs": list(self.secret_refs),
            "artifact_refs": list(self.artifact_refs),
            "open_gates": list(self.open_gates),
        }


@dataclass(frozen=True)
class SignedCheckpoint:
    checkpoint_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    task_id: str
    plan_hash: str
    policy_version: str
    runtime_id: str
    runtime_version: str
    state: WorkflowState
    revision: int
    fencing_token: int
    created_at: float
    key_id: str
    signature: str
    schema: str = SIGNED_CHECKPOINT_SCHEMA

    @classmethod
    def issue(
        cls,
        *,
        key_ring: SignatureSigningKeyRingPort,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        task_id: str,
        plan_hash: str,
        policy_version: str,
        runtime_id: str,
        runtime_version: str,
        state: WorkflowState,
        revision: int,
        fencing_token: int,
        now: float | None = None,
        checkpoint_id: str | None = None,
    ) -> "SignedCheckpoint":
        state.assert_safe()
        unsigned = cls(
            checkpoint_id=str(checkpoint_id or f"wfc-{uuid.uuid4().hex}"),
            tenant_id=str(tenant_id).strip(),
            workflow_id=str(workflow_id).strip(),
            run_id=str(run_id).strip(),
            task_id=str(task_id).strip(),
            plan_hash=str(plan_hash).strip(),
            policy_version=str(policy_version).strip(),
            runtime_id=str(runtime_id).strip(),
            runtime_version=str(runtime_version).strip(),
            state=state,
            revision=int(revision),
            fencing_token=int(fencing_token),
            created_at=float(now if now is not None else time.time()),
            key_id=key_ring.active_key_id,
            signature="",
        )
        unsigned._assert_structure()
        key_id, signature = _sign_contract(
            key_ring,
            namespace=SIGNED_CHECKPOINT_SCHEMA,
            payload=unsigned._signing_payload(),
            key_id=unsigned.key_id,
        )
        return replace(unsigned, key_id=key_id, signature=signature)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SignedCheckpoint":
        from agent.services.workflow_runtime.compatibility import (
            upcast_runtime_contract_for_loading,
        )

        raw = upcast_runtime_contract_for_loading(raw, contract_type="checkpoint")
        return cls(
            checkpoint_id=str(raw.get("checkpoint_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            runtime_id=str(raw.get("runtime_id") or ""),
            runtime_version=str(raw.get("runtime_version") or ""),
            state=WorkflowState.from_mapping(dict(raw.get("state") or {})),
            revision=int(raw.get("revision") or 0),
            fencing_token=int(raw.get("fencing_token") or 0),
            created_at=float(raw.get("created_at") or 0),
            key_id=str(raw.get("key_id") or ""),
            signature=str(raw.get("signature") or ""),
            schema=str(raw.get("schema") or SIGNED_CHECKPOINT_SCHEMA),
        )

    def verify(
        self,
        *,
        key_ring: SignatureVerificationKeyRingPort,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        task_id: str,
        plan_hash: str,
        policy_version: str,
        min_fencing_token: int = 0,
    ) -> None:
        self._assert_structure()
        expected = {
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "task_id": task_id,
            "plan_hash": plan_hash,
            "policy_version": policy_version,
        }
        for name, value in expected.items():
            if str(getattr(self, name)) != str(value):
                raise SignatureValidationError(f"checkpoint_{name}_mismatch")
        if self.fencing_token < int(min_fencing_token):
            raise SignatureValidationError("checkpoint_fencing_token_stale")
        _verify_contract(
            key_ring,
            namespace=SIGNED_CHECKPOINT_SCHEMA,
            payload=self._signing_payload(),
            key_id=self.key_id,
            signature=self.signature,
            contract_id=self.checkpoint_id,
        )

    def _assert_structure(self) -> None:
        if self.schema != SIGNED_CHECKPOINT_SCHEMA:
            raise ContractValidationError("checkpoint_schema_unsupported")
        required = (
            self.checkpoint_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.task_id,
            self.plan_hash,
            self.policy_version,
            self.runtime_id,
            self.runtime_version,
            self.key_id,
        )
        if any(not value for value in required):
            raise ContractValidationError("checkpoint_binding_required")
        if self.revision < 1 or self.fencing_token < 1 or self.created_at <= 0:
            raise ContractValidationError("checkpoint_revision_or_fencing_invalid")
        self.state.assert_safe()

    def _signing_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("signature", None)
        return payload

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "checkpoint_id": self.checkpoint_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "state": self.state.to_dict(),
            "revision": self.revision,
            "fencing_token": self.fencing_token,
            "created_at": self.created_at,
            "key_id": self.key_id,
            "signature": self.signature,
        }
        if redacted:
            payload["signature"] = "[REDACTED]"
        return payload


def _signature(key: bytes, *, namespace: str, payload: dict[str, Any]) -> str:
    message = f"{namespace}\n{canonical_json(payload)}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _sign_contract(
    key_ring: SignatureSigningKeyRingPort,
    *,
    namespace: str,
    payload: dict[str, Any],
    key_id: str,
) -> tuple[str, str]:
    try:
        return key_ring.sign(
            namespace=namespace,
            payload=payload,
            key_id=key_id,
        )
    except RuntimeAuthorizationCryptoError as exc:
        raise SignatureValidationError(exc.reason_code) from exc


def _verify_contract(
    key_ring: SignatureVerificationKeyRingPort,
    *,
    namespace: str,
    payload: dict[str, Any],
    key_id: str,
    signature: str,
    contract_id: str,
) -> None:
    try:
        key_ring.verify(
            namespace=namespace,
            payload=payload,
            key_id=key_id,
            signature=signature,
            contract_id=contract_id,
        )
    except RuntimeAuthorizationCryptoError as exc:
        raise SignatureValidationError(exc.reason_code) from exc


def _clean_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _normalize_budgets(values: dict[str, int | float]) -> dict[str, int | float]:
    normalized: dict[str, int | float] = {}
    for raw_name, raw_value in values.items():
        name = str(raw_name).strip()
        if not name or isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ContractValidationError("authorization_budget_invalid")
        normalized[name] = raw_value
    return dict(sorted(normalized.items()))
