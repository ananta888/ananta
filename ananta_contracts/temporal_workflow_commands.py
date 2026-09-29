"""Signed workflow command contract, its semantic digest/signature checks, and command authority/result contracts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from ananta_contracts.temporal_workflow_primitives import (
    _COMMAND_MAX_SAFE_INTEGER,
    _COMMAND_SEMANTIC_PAYLOAD_SCHEMA,
    _COMMAND_SIGNATURE_ALGORITHMS,
    _DIGEST_RE,
    _IDENTIFIER_RE,
    COMMAND_AUTHORITY_RESULT_SCHEMA,
    COMMAND_RESULT_SCHEMA,
    COMMAND_SCHEMA,
    LEGACY_COMMAND_SCHEMA,
    TemporalContractError,
    _bounded_float,
    _bounded_integer,
    _bounded_strings,
    _contains_sensitive_keys,
    _identifier,
    _mapping,
    _workflow_command_numeric_fields,
)
from ananta_contracts.temporal_workflow_references import WorkflowCommandType


@dataclass(frozen=True)
class WorkflowCommand:
    command_id: str
    command_type: WorkflowCommandType
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    checkpoint_id: str
    expected_revision: int
    plan_hash: str
    policy_version: str
    actor_id: str
    actor_roles: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)
    issued_at: float = 0.0
    expires_at: float = 0.0
    nonce: str = ""
    signature_algorithm: str = ""
    key_id: str = ""
    payload_digest: str = ""
    signature: str = ""
    schema: str = COMMAND_SCHEMA

    def __post_init__(self) -> None:
        if self.schema not in {LEGACY_COMMAND_SCHEMA, COMMAND_SCHEMA}:
            raise TemporalContractError("unsupported_command_schema", "workflow command schema is unsupported")
        try:
            raw_command_type = object.__getattribute__(self, "command_type")
            normalized_type = (
                raw_command_type
                if isinstance(raw_command_type, WorkflowCommandType)
                else WorkflowCommandType(str(raw_command_type))
            )
        except ValueError as exc:
            raise TemporalContractError("invalid_command_type", "workflow command type is unsupported") from exc
        object.__setattr__(self, "command_type", normalized_type)
        for field_name, value in (
            ("command_id", self.command_id),
            ("tenant_id", self.tenant_id),
            ("workflow_id", self.workflow_id),
            ("run_id", self.run_id),
            ("step_id", self.step_id),
            ("checkpoint_id", self.checkpoint_id),
            ("policy_version", self.policy_version),
            ("actor_id", self.actor_id),
            ("nonce", self.nonce),
            ("key_id", self.key_id),
        ):
            _identifier(value, field_name=field_name)
        if not _DIGEST_RE.fullmatch(self.plan_hash):
            raise TemporalContractError("invalid_plan_hash", "workflow command plan_hash must be sha256")
        expected_revision, issued_at, expires_at = _workflow_command_numeric_fields(
            {
                "expected_revision": self.expected_revision,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
            }
        )
        object.__setattr__(self, "expected_revision", expected_revision)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        _bounded_strings(self.actor_roles, field_name="actor_roles", maximum=64)
        _mapping(self.payload, field_name="command_payload", maximum_bytes=65_536)
        if _contains_sensitive_keys(self.payload):
            raise TemporalContractError("embedded_secret_denied", "command payload contains a secret")
        if object.__getattribute__(self, "command_type") in {
            WorkflowCommandType.EDIT,
            WorkflowCommandType.REQUEST_CHANGES,
        }:
            if not (self.payload.get("plan_ref") or self.payload.get("replacement_plan")):
                raise TemporalContractError("plan_edit_required", "plan edit payload is required")
            if not _DIGEST_RE.fullmatch(str(self.payload.get("replacement_plan_hash") or "")):
                raise TemporalContractError(
                    "replacement_plan_hash_required",
                    "replacement plan hash must be sha256",
                )
        if self.schema == LEGACY_COMMAND_SCHEMA:
            if self.signature_algorithm or self.payload_digest:
                raise TemporalContractError(
                    "legacy_command_authority_fields_forbidden",
                    "legacy workflow commands cannot carry v3 authority fields",
                )
            if not (_valid_hmac_signature(self.signature) or _valid_ed25519_signature(self.signature)):
                raise TemporalContractError("invalid_command_signature", "workflow command signature is invalid")
            return
        if self.signature_algorithm not in _COMMAND_SIGNATURE_ALGORITHMS:
            raise TemporalContractError(
                "unsupported_command_signature_algorithm",
                "workflow command signature algorithm is unsupported",
            )
        expected_digest = self.computed_payload_digest()
        if not hmac.compare_digest(str(self.payload_digest), expected_digest):
            raise TemporalContractError(
                "invalid_command_payload_digest",
                "workflow command payload digest is invalid",
            )
        if self.signature_algorithm == "ed25519":
            valid_signature = _valid_ed25519_signature(self.signature)
        else:
            valid_signature = _valid_hmac_signature(self.signature)
        if not valid_signature:
            raise TemporalContractError("invalid_command_signature", "workflow command signature is invalid")

    @classmethod
    def from_mapping(cls, raw: object, *, default_type: str = "") -> "WorkflowCommand":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_command", "workflow command must be an object")
        expected_revision, issued_at, expires_at = cls.parse_numeric_fields(raw)
        try:
            command_type = WorkflowCommandType(str(raw.get("command_type") or default_type or ""))
        except ValueError as exc:
            raise TemporalContractError("invalid_command_type", "workflow command type is unsupported") from exc
        return cls(
            schema=str(raw.get("schema") or ""),
            command_id=str(raw.get("command_id") or ""),
            command_type=command_type,
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            checkpoint_id=str(raw.get("checkpoint_id") or ""),
            expected_revision=expected_revision,
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            actor_id=str(raw.get("actor_id") or ""),
            actor_roles=_bounded_strings(raw.get("actor_roles"), field_name="actor_roles", maximum=64),
            payload=_mapping(raw.get("payload"), field_name="command_payload", maximum_bytes=65_536),
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=str(raw.get("nonce") or ""),
            signature_algorithm=str(raw.get("signature_algorithm") or "").strip().lower(),
            key_id=str(raw.get("key_id") or ""),
            payload_digest=str(raw.get("payload_digest") or ""),
            signature=str(raw.get("signature") or ""),
        )

    @staticmethod
    def parse_numeric_fields(raw: Mapping[str, Any]) -> tuple[int, float, float]:
        """Normalize the three command numerics for neutral and Hub adapters."""

        return _workflow_command_numeric_fields(raw)

    @staticmethod
    def parse_issued_at(value: object) -> float:
        """Normalize a command issuance timestamp before Hub arithmetic."""

        return _bounded_float(
            value,
            field_name="issued_at",
            minimum=0.0,
            maximum=float(_COMMAND_MAX_SAFE_INTEGER),
            reason_code="invalid_command_issued_at",
        )

    @classmethod
    def unsigned_v3_mapping(
        cls,
        *,
        command_id: str,
        command_type: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        checkpoint_id: str,
        expected_revision: int,
        plan_hash: str,
        policy_version: str,
        actor_id: str,
        actor_roles: Sequence[str],
        payload: Mapping[str, Any],
        issued_at: float,
        expires_at: float,
        nonce: str,
        signature_algorithm: str,
        key_id: str,
    ) -> dict[str, Any]:
        """Build the exact v3 bytes-to-sign without creating an unsigned DTO."""

        try:
            normalized_type = WorkflowCommandType(str(command_type)).value
        except ValueError as exc:
            raise TemporalContractError(
                "invalid_command_type",
                "workflow command type is unsupported",
            ) from exc
        normalized_revision, normalized_issued_at, normalized_expires_at = _workflow_command_numeric_fields(
            {
                "expected_revision": expected_revision,
                "issued_at": issued_at,
                "expires_at": expires_at,
            }
        )
        mapping: dict[str, Any] = {
            "schema": COMMAND_SCHEMA,
            "command_id": str(command_id),
            "command_type": normalized_type,
            "tenant_id": str(tenant_id),
            "workflow_id": str(workflow_id),
            "run_id": str(run_id),
            "step_id": str(step_id),
            "checkpoint_id": str(checkpoint_id),
            "expected_revision": normalized_revision,
            "plan_hash": str(plan_hash),
            "policy_version": str(policy_version),
            "actor_id": str(actor_id),
            "actor_roles": [str(value) for value in actor_roles],
            "payload": _mapping(payload, field_name="command_payload", maximum_bytes=65_536),
            "issued_at": normalized_issued_at,
            "expires_at": normalized_expires_at,
            "nonce": str(nonce),
            "signature_algorithm": str(signature_algorithm).strip().lower(),
            "key_id": str(key_id),
        }
        mapping["payload_digest"] = _workflow_command_payload_digest(mapping)
        return mapping

    def semantic_payload(self) -> dict[str, Any]:
        return _workflow_command_semantic_payload(self.to_dict())

    def computed_payload_digest(self) -> str:
        return _workflow_command_payload_digest(self.to_dict())

    @staticmethod
    def semantic_payload_for_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
        """Expose the version-neutral semantic body to Hub-side adapters."""

        return _workflow_command_semantic_payload(raw)

    @staticmethod
    def payload_digest_for_mapping(raw: Mapping[str, Any]) -> str:
        """Compute semantic identity without imposing Temporal ID rules."""

        return _workflow_command_payload_digest(raw)

    def signing_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("signature", None)
        return payload

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": self.schema,
            "command_id": self.command_id,
            "command_type": object.__getattribute__(self, "command_type").value,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "checkpoint_id": self.checkpoint_id,
            "expected_revision": self.expected_revision,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "actor_id": self.actor_id,
            "actor_roles": list(self.actor_roles),
            "payload": dict(self.payload),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "key_id": self.key_id,
            "signature": self.signature,
        }
        if self.schema == COMMAND_SCHEMA:
            result["signature_algorithm"] = self.signature_algorithm
            result["payload_digest"] = self.payload_digest
        if redacted:
            result["nonce"] = "[REDACTED]"
            result["signature"] = "[REDACTED]"
        return result


def _workflow_command_semantic_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable command body shared across signature renewals."""

    return {
        "schema": _COMMAND_SEMANTIC_PAYLOAD_SCHEMA,
        "command_id": str(raw.get("command_id") or ""),
        "command_type": str(raw.get("command_type") or ""),
        "tenant_id": str(raw.get("tenant_id") or ""),
        "workflow_id": str(raw.get("workflow_id") or ""),
        "run_id": str(raw.get("run_id") or ""),
        "step_id": str(raw.get("step_id") or ""),
        "checkpoint_id": str(raw.get("checkpoint_id") or ""),
        "expected_revision": _bounded_integer(
            raw.get("expected_revision"),
            field_name="expected_revision",
            minimum=0,
            maximum=_COMMAND_MAX_SAFE_INTEGER,
            reason_code="invalid_command_revision",
        ),
        "plan_hash": str(raw.get("plan_hash") or ""),
        "policy_version": str(raw.get("policy_version") or ""),
        "actor_id": str(raw.get("actor_id") or ""),
        "actor_roles": list(raw.get("actor_roles") or ()),
        "payload": dict(raw.get("payload") or {}),
    }


def _workflow_command_payload_digest(raw: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            _workflow_command_semantic_payload(raw),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TemporalContractError(
            "invalid_command_payload_digest",
            "workflow command payload is not canonical JSON",
        ) from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _valid_hmac_signature(value: object) -> bool:
    return bool(re.fullmatch(r"[a-fA-F0-9]{64}", str(value or "")))


def _valid_ed25519_signature(value: object) -> bool:
    try:
        decoded = base64.b64decode(str(value or "").encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return len(decoded) == 64


@dataclass(frozen=True)
class WorkflowCommandAuthorityResult:
    """Deterministic proof returned by the worker's crypto Local Activity."""

    accepted: bool
    command_id: str = ""
    payload_digest: str = ""
    signature_algorithm: str = ""
    key_id: str = ""
    reason_code: str = ""
    schema: str = COMMAND_AUTHORITY_RESULT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != COMMAND_AUTHORITY_RESULT_SCHEMA:
            raise TemporalContractError(
                "command_authority_result_schema_unsupported",
                "command authority result schema is unsupported",
            )
        if self.accepted:
            if (
                not _IDENTIFIER_RE.fullmatch(self.command_id)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.payload_digest)
                or self.signature_algorithm != "ed25519"
                or not _IDENTIFIER_RE.fullmatch(self.key_id)
                or self.reason_code
            ):
                raise TemporalContractError(
                    "command_authority_result_invalid",
                    "accepted command authority result is incomplete",
                )
        elif not re.fullmatch(r"[a-z][a-z0-9_]{0,255}", self.reason_code):
            raise TemporalContractError(
                "command_authority_result_invalid",
                "rejected command authority result requires a stable reason",
            )

    @classmethod
    def from_mapping(cls, raw: object) -> "WorkflowCommandAuthorityResult":
        if not isinstance(raw, Mapping):
            raise TemporalContractError(
                "command_authority_result_invalid",
                "command authority result must be an object",
            )
        return cls(
            schema=str(raw.get("schema") or ""),
            accepted=raw.get("accepted") is True,
            command_id=str(raw.get("command_id") or ""),
            payload_digest=str(raw.get("payload_digest") or ""),
            signature_algorithm=str(raw.get("signature_algorithm") or ""),
            key_id=str(raw.get("key_id") or ""),
            reason_code=str(raw.get("reason_code") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowCommandResult:
    command_id: str
    accepted: bool
    revision: int
    status: str
    reason_code: str = ""
    schema: str = COMMAND_RESULT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
