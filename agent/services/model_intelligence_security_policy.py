"""Fail-closed tenant, RBAC, redaction, and retention policy for model intelligence.

The module is deliberately side-effect free. It decides whether work may
proceed and describes retention transitions; persistence and artifact deletion
remain behind their existing Hub-owned ports.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ananta_contracts.model_intelligence import ArtifactRef

AUDIT_EVENT_SCHEMA = "ananta.model-intelligence.audit-event.v1"
THREAT_MATRIX_SCHEMA = "ananta.model-intelligence.threat-matrix.v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_SAFE_AUDIT_FIELDS = frozenset(
    {
        "action",
        "analysis_kind",
        "artifact_ref_digest",
        "correlation_id",
        "duration_ms",
        "idempotency_digest",
        "idempotent",
        "next_state",
        "outcome",
        "previous_state",
        "quota_dimension",
        "reason_code",
        "resource_kind",
        "retention_class",
        "size_bytes",
        "state",
        "transition_id",
    }
)
_SENSITIVE_FIELD_CATEGORIES = {
    "activation": "activation",
    "attention": "activation",
    "content": "raw_content",
    "hidden": "activation",
    "input": "raw_content",
    "local_path": "local_path",
    "logit": "activation",
    "model_bytes": "model_bytes",
    "output": "raw_content",
    "password": "secret",
    "path": "local_path",
    "payload": "raw_content",
    "prompt": "raw_prompt",
    "secret": "secret",
    "token": "secret",
}


class ModelIntelligenceSecurityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ModelIntelligenceRole(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    OPERATOR = "operator"
    TENANT_ADMIN = "tenant_admin"


class ModelIntelligenceAction(str, Enum):
    READ_JOB = "read_job"
    READ_ARTIFACT = "read_artifact"
    READ_REPORT = "read_report"
    SUBMIT_ANALYSIS = "submit_analysis"
    CANCEL_ANALYSIS = "cancel_analysis"
    DELETE_ARTIFACT = "delete_artifact"
    MANAGE_RETENTION = "manage_retention"


class ModelIntelligenceResourceKind(str, Enum):
    JOB = "job"
    ARTIFACT = "artifact"
    REPORT = "report"


_ROLE_ACTIONS: dict[ModelIntelligenceRole, frozenset[ModelIntelligenceAction]] = {
    ModelIntelligenceRole.VIEWER: frozenset(
        {
            ModelIntelligenceAction.READ_JOB,
            ModelIntelligenceAction.READ_ARTIFACT,
            ModelIntelligenceAction.READ_REPORT,
        }
    ),
    ModelIntelligenceRole.ANALYST: frozenset(
        {
            ModelIntelligenceAction.READ_JOB,
            ModelIntelligenceAction.READ_ARTIFACT,
            ModelIntelligenceAction.READ_REPORT,
            ModelIntelligenceAction.SUBMIT_ANALYSIS,
            ModelIntelligenceAction.CANCEL_ANALYSIS,
        }
    ),
    ModelIntelligenceRole.OPERATOR: frozenset(
        {
            ModelIntelligenceAction.READ_JOB,
            ModelIntelligenceAction.READ_ARTIFACT,
            ModelIntelligenceAction.READ_REPORT,
            ModelIntelligenceAction.CANCEL_ANALYSIS,
            ModelIntelligenceAction.DELETE_ARTIFACT,
        }
    ),
    ModelIntelligenceRole.TENANT_ADMIN: frozenset(ModelIntelligenceAction),
}


def _identifier(value: str, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ModelIntelligenceSecurityError(reason_code)
    return normalized


@dataclass(frozen=True, slots=True)
class ModelIntelligencePrincipal:
    subject_id: str
    tenant_id: str
    roles: frozenset[ModelIntelligenceRole]

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_id", _identifier(self.subject_id, "principal_subject_invalid"))
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "principal_tenant_invalid"))
        try:
            roles = frozenset(
                role if isinstance(role, ModelIntelligenceRole) else ModelIntelligenceRole(str(role))
                for role in self.roles
            )
        except ValueError as exc:
            raise ModelIntelligenceSecurityError("principal_role_invalid") from exc
        if not roles:
            raise ModelIntelligenceSecurityError("principal_role_required")
        object.__setattr__(self, "roles", roles)


@dataclass(frozen=True, slots=True)
class ModelIntelligenceResourceScope:
    tenant_id: str
    kind: ModelIntelligenceResourceKind
    resource_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "resource_tenant_invalid"))
        object.__setattr__(self, "resource_id", _identifier(self.resource_id, "resource_id_invalid"))
        try:
            kind = (
                self.kind
                if isinstance(self.kind, ModelIntelligenceResourceKind)
                else ModelIntelligenceResourceKind(str(self.kind))
            )
        except ValueError as exc:
            raise ModelIntelligenceSecurityError("resource_kind_invalid") from exc
        object.__setattr__(self, "kind", kind)


@dataclass(frozen=True, slots=True)
class ModelIntelligenceAccessDecision:
    allowed: bool
    reason_code: str
    action: ModelIntelligenceAction
    resource_kind: ModelIntelligenceResourceKind


class ModelIntelligenceAccessPolicy:
    """Tenant isolation is enforced before role evaluation, including admins."""

    def decide(
        self,
        principal: ModelIntelligencePrincipal,
        resource: ModelIntelligenceResourceScope,
        action: ModelIntelligenceAction | str,
    ) -> ModelIntelligenceAccessDecision:
        try:
            normalized_action = (
                action if isinstance(action, ModelIntelligenceAction) else ModelIntelligenceAction(str(action))
            )
        except ValueError as exc:
            raise ModelIntelligenceSecurityError("rbac_action_invalid") from exc
        if principal.tenant_id != resource.tenant_id:
            return ModelIntelligenceAccessDecision(
                False,
                "tenant_scope_mismatch",
                normalized_action,
                resource.kind,
            )
        permitted = any(normalized_action in _ROLE_ACTIONS[role] for role in principal.roles)
        return ModelIntelligenceAccessDecision(
            permitted,
            "allowed" if permitted else "rbac_action_denied",
            normalized_action,
            resource.kind,
        )

    def require(
        self,
        principal: ModelIntelligencePrincipal,
        resource: ModelIntelligenceResourceScope,
        action: ModelIntelligenceAction | str,
    ) -> None:
        decision = self.decide(principal, resource, action)
        if not decision.allowed:
            raise ModelIntelligenceSecurityError(decision.reason_code)


def _redaction_category(field: str) -> str:
    normalized = field.casefold()
    for fragment, category in _SENSITIVE_FIELD_CATEGORIES.items():
        if fragment in normalized:
            return category
    return "unapproved_field"


def _safe_audit_scalar(value: Any) -> str | int | float | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise ModelIntelligenceSecurityError("audit_value_not_finite")
    if isinstance(value, str) and _SAFE_TOKEN_RE.fullmatch(value):
        return value
    raise ModelIntelligenceSecurityError("audit_value_not_sanitized")


def sanitize_model_intelligence_audit_event(
    event_type: str,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Project arbitrary caller fields into a closed, content-free audit shape."""

    normalized_event = str(event_type or "").strip()
    if not _SAFE_TOKEN_RE.fullmatch(normalized_event):
        raise ModelIntelligenceSecurityError("audit_event_type_invalid")
    sanitized: dict[str, Any] = {}
    categories: set[str] = set()
    redacted_count = 0
    for raw_field, raw_value in fields.items():
        field = str(raw_field or "").strip()
        if field not in _SAFE_AUDIT_FIELDS:
            redacted_count += 1
            categories.add(_redaction_category(field))
            continue
        try:
            sanitized[field] = _safe_audit_scalar(raw_value)
        except ModelIntelligenceSecurityError:
            redacted_count += 1
            categories.add(_redaction_category(field) if _redaction_category(field) != "unapproved_field" else "unsafe_value")
    return {
        "schema": AUDIT_EVENT_SCHEMA,
        "event_type": normalized_event,
        **dict(sorted(sanitized.items())),
        "redacted_field_count": redacted_count,
        "redaction_categories": sorted(categories),
    }


class RetentionClass(str, Enum):
    EPHEMERAL = "ephemeral"
    STANDARD = "standard"
    EXTENDED = "extended"


class RetentionState(str, Enum):
    ACTIVE = "active"
    DELETE_PENDING = "delete_pending"
    DELETED = "deleted"
    LEGAL_HOLD = "legal_hold"


class RetentionCause(str, Enum):
    USER_REQUEST = "user_request"
    RETENTION_EXPIRED = "retention_expired"


@dataclass(frozen=True, slots=True)
class ModelIntelligenceRetentionRecord:
    tenant_id: str
    artifact_ref: ArtifactRef
    retention_class: RetentionClass
    created_at_epoch_seconds: int
    retain_until_epoch_seconds: int
    state: RetentionState = RetentionState.ACTIVE

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "retention_tenant_invalid"))
        if not isinstance(self.artifact_ref, ArtifactRef):
            raise ModelIntelligenceSecurityError("retention_artifact_ref_invalid")
        try:
            retention_class = (
                self.retention_class
                if isinstance(self.retention_class, RetentionClass)
                else RetentionClass(str(self.retention_class))
            )
            state = self.state if isinstance(self.state, RetentionState) else RetentionState(str(self.state))
        except ValueError as exc:
            raise ModelIntelligenceSecurityError("retention_state_invalid") from exc
        if (
            isinstance(self.created_at_epoch_seconds, bool)
            or isinstance(self.retain_until_epoch_seconds, bool)
            or self.created_at_epoch_seconds < 0
            or self.retain_until_epoch_seconds < self.created_at_epoch_seconds
        ):
            raise ModelIntelligenceSecurityError("retention_deadline_invalid")
        object.__setattr__(self, "retention_class", retention_class)
        object.__setattr__(self, "state", state)


@dataclass(frozen=True, slots=True)
class ModelIntelligenceRetentionDecision:
    allowed: bool
    idempotent: bool
    reason_code: str
    previous_state: RetentionState
    next_state: RetentionState
    artifact_ref: ArtifactRef
    transition_id: str
    idempotency_digest: str

    def audit_event(self) -> dict[str, Any]:
        artifact_wire = self.artifact_ref.to_wire()
        artifact_digest = hashlib.sha256(
            json.dumps(artifact_wire, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return sanitize_model_intelligence_audit_event(
            "retention_transition",
            {
                "artifact_ref_digest": artifact_digest,
                "idempotency_digest": self.idempotency_digest,
                "idempotent": self.idempotent,
                "next_state": self.next_state.value,
                "outcome": "allowed" if self.allowed else "denied",
                "previous_state": self.previous_state.value,
                "reason_code": self.reason_code,
                "transition_id": self.transition_id,
            },
        )


class ModelIntelligenceRetentionPolicy:
    """Plan idempotent transitions without resolving or deleting artifact paths."""

    @staticmethod
    def _idempotency_digest(idempotency_key: str) -> str:
        normalized = str(idempotency_key or "").strip()
        if not 8 <= len(normalized) <= 256 or any(ord(character) < 33 for character in normalized):
            raise ModelIntelligenceSecurityError("retention_idempotency_key_invalid")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _transition_id(
        record: ModelIntelligenceRetentionRecord,
        idempotency_digest: str,
        next_state: RetentionState,
    ) -> str:
        material = ":".join(
            (
                record.tenant_id,
                record.artifact_ref.artifact_id,
                record.artifact_ref.sha256,
                idempotency_digest,
                next_state.value,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def plan_deletion(
        self,
        record: ModelIntelligenceRetentionRecord,
        *,
        requesting_tenant_id: str,
        idempotency_key: str,
        now_epoch_seconds: int,
        cause: RetentionCause | str,
    ) -> ModelIntelligenceRetentionDecision:
        tenant = _identifier(requesting_tenant_id, "retention_request_tenant_invalid")
        digest = self._idempotency_digest(idempotency_key)
        try:
            normalized_cause = cause if isinstance(cause, RetentionCause) else RetentionCause(str(cause))
        except ValueError as exc:
            raise ModelIntelligenceSecurityError("retention_cause_invalid") from exc
        allowed = True
        idempotent = False
        reason = "delete_planned"
        next_state = RetentionState.DELETE_PENDING
        if tenant != record.tenant_id:
            allowed, reason, next_state = False, "tenant_scope_mismatch", record.state
        elif record.state is RetentionState.LEGAL_HOLD:
            allowed, reason, next_state = False, "legal_hold_active", record.state
        elif record.state is RetentionState.DELETED:
            idempotent, reason, next_state = True, "already_deleted", RetentionState.DELETED
        elif record.state is RetentionState.DELETE_PENDING:
            idempotent, reason = True, "delete_already_pending"
        elif (
            normalized_cause is RetentionCause.RETENTION_EXPIRED
            and now_epoch_seconds < record.retain_until_epoch_seconds
        ):
            allowed, reason, next_state = False, "retention_not_expired", record.state
        transition_id = self._transition_id(record, digest, next_state)
        return ModelIntelligenceRetentionDecision(
            allowed,
            idempotent,
            reason,
            record.state,
            next_state,
            record.artifact_ref,
            transition_id,
            digest,
        )

    def confirm_deletion(
        self,
        record: ModelIntelligenceRetentionRecord,
        *,
        requesting_tenant_id: str,
        idempotency_key: str,
    ) -> ModelIntelligenceRetentionDecision:
        tenant = _identifier(requesting_tenant_id, "retention_request_tenant_invalid")
        digest = self._idempotency_digest(idempotency_key)
        allowed = tenant == record.tenant_id and record.state in {
            RetentionState.DELETE_PENDING,
            RetentionState.DELETED,
        }
        idempotent = record.state is RetentionState.DELETED and tenant == record.tenant_id
        reason = (
            "already_deleted"
            if idempotent
            else "delete_confirmed"
            if allowed
            else "tenant_scope_mismatch"
            if tenant != record.tenant_id
            else "delete_not_pending"
        )
        next_state = RetentionState.DELETED if allowed else record.state
        return ModelIntelligenceRetentionDecision(
            allowed,
            idempotent,
            reason,
            record.state,
            next_state,
            record.artifact_ref,
            self._transition_id(record, digest, next_state),
            digest,
        )


MODEL_INTELLIGENCE_THREAT_MATRIX: tuple[dict[str, object], ...] = (
    {
        "boundary": "api",
        "controls": ("authenticated_principal", "tenant_scope", "rbac_action", "bounded_request"),
        "admission_owner": "api",
    },
    {
        "boundary": "hub",
        "controls": ("hub_owned_job", "tenant_scope", "sanitized_audit", "artifact_ref_only"),
        "admission_owner": "hub",
    },
    {
        "boundary": "worker",
        "controls": ("delegated_job_only", "content_free_metrics", "container_hardening"),
        "admission_owner": "worker_runtime",
    },
    {
        "boundary": "parser",
        "controls": ("bounded_parser", "no_remote_code", "no_pickle"),
        "admission_owner": "OWMA-003",
    },
    {
        "boundary": "artifact_store",
        "controls": ("artifact_ref_only", "tenant_scope", "idempotent_retention_transition"),
        "admission_owner": "artifact_store",
    },
)


def model_intelligence_threat_matrix() -> dict[str, object]:
    return {
        "schema": THREAT_MATRIX_SCHEMA,
        "boundaries": [dict(item) for item in MODEL_INTELLIGENCE_THREAT_MATRIX],
    }


__all__ = [
    "MODEL_INTELLIGENCE_THREAT_MATRIX",
    "ModelIntelligenceAccessDecision",
    "ModelIntelligenceAccessPolicy",
    "ModelIntelligenceAction",
    "ModelIntelligencePrincipal",
    "ModelIntelligenceResourceKind",
    "ModelIntelligenceResourceScope",
    "ModelIntelligenceRetentionDecision",
    "ModelIntelligenceRetentionPolicy",
    "ModelIntelligenceRetentionRecord",
    "ModelIntelligenceRole",
    "ModelIntelligenceSecurityError",
    "RetentionCause",
    "RetentionClass",
    "RetentionState",
    "model_intelligence_threat_matrix",
    "sanitize_model_intelligence_audit_event",
]
