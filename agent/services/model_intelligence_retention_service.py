"""Hub-side composition of policy, retention persistence, and artifact deletion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent.services.model_intelligence_artifact_store import (
    ModelIntelligenceArtifactRef,
)
from agent.services.model_intelligence_retention_adapter import (
    ModelIntelligenceRetentionStorePort,
)
from agent.services.model_intelligence_security_policy import (
    ModelIntelligenceAccessPolicy,
    ModelIntelligenceAction,
    ModelIntelligencePrincipal,
    ModelIntelligenceResourceKind,
    ModelIntelligenceResourceScope,
    ModelIntelligenceRetentionDecision,
    ModelIntelligenceRetentionPolicy,
    ModelIntelligenceRetentionRecord,
    RetentionCause,
    RetentionState,
)


class ModelIntelligenceRetentionServiceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@runtime_checkable
class ModelIntelligenceArtifactDeletePort(Protocol):
    """Narrow port intentionally separate from artifact storage and resolution."""

    def delete(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ModelIntelligenceDeletionResult:
    artifact_id: str
    state: RetentionState
    object_existed: bool
    idempotent: bool
    transitions: tuple[ModelIntelligenceRetentionDecision, ...]

    def audit_events(self) -> tuple[dict[str, object], ...]:
        return tuple(decision.audit_event() for decision in self.transitions)


class ModelIntelligenceRetentionService:
    """Coordinates deletion without exposing paths or bypassing tenant policy."""

    def __init__(
        self,
        *,
        retention_store: ModelIntelligenceRetentionStorePort,
        artifact_delete_port: ModelIntelligenceArtifactDeletePort,
        access_policy: ModelIntelligenceAccessPolicy | None = None,
        retention_policy: ModelIntelligenceRetentionPolicy | None = None,
    ) -> None:
        self._retention_store = retention_store
        self._artifact_delete_port = artifact_delete_port
        self._access_policy = access_policy or ModelIntelligenceAccessPolicy()
        self._retention_policy = retention_policy or ModelIntelligenceRetentionPolicy()

    @staticmethod
    def _store_reference(
        tenant_id: str,
        record: ModelIntelligenceRetentionRecord,
    ) -> ModelIntelligenceArtifactRef:
        return ModelIntelligenceArtifactRef(
            digest=f"sha256:{record.artifact_ref.sha256}",
            media_type=record.artifact_ref.media_type,
            size_bytes=record.artifact_ref.size_bytes,
            tenant_scope=hashlib.sha256(tenant_id.encode("utf-8")).hexdigest(),
            artifact_kind=record.artifact_ref.kind,
        )

    def delete(
        self,
        *,
        principal: ModelIntelligencePrincipal,
        artifact_id: str,
        idempotency_key: str,
        now_epoch_seconds: int,
        cause: RetentionCause | str,
    ) -> ModelIntelligenceDeletionResult:
        scope = ModelIntelligenceResourceScope(
            tenant_id=principal.tenant_id,
            kind=ModelIntelligenceResourceKind.ARTIFACT,
            resource_id=artifact_id,
        )
        try:
            self._access_policy.require(
                principal,
                scope,
                ModelIntelligenceAction.DELETE_ARTIFACT,
            )
        except ValueError as exc:
            raise ModelIntelligenceRetentionServiceError(
                getattr(exc, "reason_code", "retention_access_denied")
            ) from exc
        record = self._retention_store.get(
            tenant_id=principal.tenant_id,
            artifact_id=artifact_id,
        )
        if record is None:
            raise ModelIntelligenceRetentionServiceError("retention_record_not_found")
        pending = self._retention_policy.plan_deletion(
            record,
            requesting_tenant_id=principal.tenant_id,
            idempotency_key=idempotency_key,
            now_epoch_seconds=now_epoch_seconds,
            cause=cause,
        )
        if not pending.allowed:
            raise ModelIntelligenceRetentionServiceError(pending.reason_code)
        pending_record = self._retention_store.apply(
            pending,
            tenant_id=principal.tenant_id,
            recorded_at_epoch_seconds=now_epoch_seconds,
        )
        if pending_record.state is RetentionState.DELETED:
            return ModelIntelligenceDeletionResult(
                artifact_id,
                RetentionState.DELETED,
                False,
                True,
                (pending,),
            )
        reference = self._store_reference(principal.tenant_id, pending_record)
        try:
            object_existed = bool(
                self._artifact_delete_port.delete(principal.tenant_id, reference)
            )
        except Exception as exc:
            raise ModelIntelligenceRetentionServiceError("artifact_delete_failed") from exc
        confirmed = self._retention_policy.confirm_deletion(
            pending_record,
            requesting_tenant_id=principal.tenant_id,
            idempotency_key=f"{idempotency_key}:confirm",
        )
        deleted_record = self._retention_store.apply(
            confirmed,
            tenant_id=principal.tenant_id,
            recorded_at_epoch_seconds=now_epoch_seconds,
        )
        return ModelIntelligenceDeletionResult(
            artifact_id,
            deleted_record.state,
            object_existed,
            pending.idempotent or confirmed.idempotent or not object_existed,
            (pending, confirmed),
        )


__all__ = [
    "ModelIntelligenceArtifactDeletePort",
    "ModelIntelligenceDeletionResult",
    "ModelIntelligenceRetentionService",
    "ModelIntelligenceRetentionServiceError",
]
