"""Persistent Hub adapters for approved CodeHug mutation dispatch."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.governance import ApprovalRequestDB
from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.db_models.source_control import (
    SourceAccessGrantDB,
    SourceRevisionDB,
)
from agent.services.codehug_mutation_authorization import (
    CodeHugMutationAuthorizationService,
)
from agent.services.codehug_mutation_composition import (
    CodeHugDestinationBinding,
    CodeHugRevisionBinding,
    RegisteredCodeHugMutationIntent,
)
from agent.services.source_access_enforcement import (
    SourceAccessEnforcementService,
    source_access_grant_digest,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
)
from agent.services.source_access_persistence_adapter import (
    SQLSourceAccessEnforcementAdapter,
)
from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    emit_source_control_audit,
)
from agent.services.source_destination_resolution import (
    DestinationSelection,
    SourceDestinationResolutionService,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
    SourceAccessGrant,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_LIVE_EXECUTION_STATES = frozenset({"issued", "queued", "running"})


class PersistentCodeHugAdapterError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SQLCodeHugMutationIntentCatalog:
    """Treat a granted, scoped ApprovalRequest as the opaque mutation intent."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        intent_id: str,
    ) -> RegisteredCodeHugMutationIntent | None:
        with Session(self._engine) as db:
            approval = db.get(ApprovalRequestDB, intent_id)
            if approval is None:
                return None
            scope = dict(approval.scope or {})
            if (
                approval.status != "granted"
                or (
                    approval.expires_at is not None
                    and approval.expires_at <= float(self._clock())
                )
                or scope.get("tenant_id") != tenant_id
                or scope.get("project_id") != project_id
                or scope.get("actor_id") != actor_id
            ):
                return None
            job_id = str(scope.get("job_id") or "")
            execution = db.get(KnowledgeIndexExecutionBindingDB, job_id)
            if (
                execution is None
                or execution.tenant_id != tenant_id
                or execution.project_id != project_id
                or execution.owner_id != actor_id
                or execution.state not in _LIVE_EXECUTION_STATES
                or execution.lease_expires_epoch_ms
                <= int(float(self._clock()) * 1000)
            ):
                return None
            tool_id = str(scope.get("tool_id") or approval.tool_name or "")
            payload_reference_id = str(
                approval.content_artifact_ref
                or scope.get("payload_reference_id")
                or ""
            )
            try:
                operation = GrantOperation(str(scope.get("operation") or ""))
                transformation = GrantTransformation(
                    str(scope.get("transformation") or "")
                )
            except ValueError:
                return None
            exact_scope = {
                "source_revision_id": execution.source_revision_id,
                "destination_id": execution.destination_id,
                "assignment_id": execution.assignment_id,
                "lease_id": execution.lease_id,
                "source_access_grant_id": execution.source_access_grant_id,
            }
            if any(
                scope.get(name) != value
                for name, value in exact_scope.items()
            ):
                return None
            values = (
                intent_id,
                job_id,
                tool_id,
                payload_reference_id,
                str(scope.get("purpose") or ""),
            )
            if any(_ID.fullmatch(value) is None for value in values):
                return None
            return RegisteredCodeHugMutationIntent(
                intent_id=intent_id,
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=actor_id,
                job_id=job_id,
                tool_id=tool_id,
                operation=operation,
                source_revision_id=execution.source_revision_id,
                destination_id=execution.destination_id,
                transformation=transformation,
                purpose=str(scope["purpose"]),
                approval_id=approval.id,
                assignment_id=execution.assignment_id,
                lease_id=execution.lease_id,
                payload_reference_id=payload_reference_id,
                source_access_grant_id=execution.source_access_grant_id,
                source_access_grant_digest=(
                    execution.source_access_grant_digest
                ),
            )


class SQLCodeHugRevisionCatalog:
    """Resolve admitted revision, policy and exact persistent grant binding."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        intent: RegisteredCodeHugMutationIntent,
    ) -> CodeHugRevisionBinding | None:
        with Session(self._engine) as db:
            revision = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                    SourceRevisionDB.owner_id == intent.actor_id,
                    SourceRevisionDB.admission_state == "admitted",
                )
            ).first()
            execution = db.get(
                KnowledgeIndexExecutionBindingDB, intent.job_id
            )
            grant = db.exec(
                select(SourceAccessGrantDB).where(
                    SourceAccessGrantDB.grant_id
                    == intent.source_access_grant_id,
                    SourceAccessGrantDB.tenant_id == tenant_id,
                    SourceAccessGrantDB.project_id == project_id,
                    SourceAccessGrantDB.owner_id == intent.actor_id,
                    SourceAccessGrantDB.source_revision_id
                    == source_revision_id,
                    SourceAccessGrantDB.destination_id
                    == intent.destination_id,
                    SourceAccessGrantDB.operation
                    == intent.operation.value,
                    SourceAccessGrantDB.transformation
                    == intent.transformation.value,
                    SourceAccessGrantDB.purpose == intent.purpose,
                    SourceAccessGrantDB.state == "active",
                    SourceAccessGrantDB.expires_at_epoch
                    > float(self._clock()),
                )
            ).first()
            if revision is None or execution is None or grant is None:
                return None
            contract = _grant_contract(grant)
            digest = source_access_grant_digest(contract)
            if (
                digest != intent.source_access_grant_digest
                or execution.source_access_grant_digest != digest
                or execution.source_revision_digest
                != revision.revision_digest
                or execution.policy_snapshot_id != grant.policy_version
                or execution.policy_snapshot_digest
                != str(grant.policy_snapshot_digest or "")
            ):
                return None
            return CodeHugRevisionBinding(
                source_revision_id=revision.source_revision_id,
                revision_digest=revision.revision_digest,
                policy_digest=execution.policy_snapshot_digest,
                content_manifest_id=revision.content_manifest_id,
                content_manifest_digest=(
                    revision.content_manifest_digest
                ),
                source_access_grant_id=grant.grant_id,
                source_access_grant_digest=digest,
            )


class ResolvedCodeHugDestinationCatalog:
    """Resolve destination IDs through the real Hub destination resolver."""

    def __init__(self, catalog: object) -> None:
        self._catalog = catalog
        self._resolver = SourceDestinationResolutionService(catalog)

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> CodeHugDestinationBinding | None:
        get = getattr(self._catalog, "get", None)
        descriptor = (
            get(
                tenant_id=tenant_id,
                project_id=project_id,
                destination_id=destination_id,
            )
            if callable(get)
            else None
        )
        if descriptor is None:
            return None
        resolved = self._resolver.resolve(
            DestinationSelection(
                worker_id=descriptor.worker_id,
                runtime_id=descriptor.runtime_id,
                provider_id=descriptor.provider_id,
                model_id=descriptor.model_id,
            )
        )
        if resolved.descriptor.destination_id != destination_id:
            return None
        return CodeHugDestinationBinding(
            destination_id=destination_id,
            destination_digest=resolved.destination_digest,
        )


class SQLCodeHugApprovalStore:
    """Atomically consume the exact persisted approval once."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    def consume(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        intent_id: str,
        source_revision_id: str,
        destination_id: str,
        tool_id: str,
        transformation: str,
    ) -> bool:
        if approval_id != intent_id:
            return False
        now = float(self._clock())
        with Session(self._engine) as db:
            approval = db.get(ApprovalRequestDB, approval_id)
            if approval is None:
                return False
            scope = dict(approval.scope or {})
            expected = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "actor_id": actor_id,
                "source_revision_id": source_revision_id,
                "destination_id": destination_id,
                "tool_id": tool_id,
                "transformation": transformation,
            }
            if (
                approval.status != "granted"
                or approval.tool_name != tool_id
                or (
                    approval.expires_at is not None
                    and approval.expires_at <= now
                )
                or any(scope.get(name) != value for name, value in expected.items())
            ):
                return False
            transition = db.exec(
                update(ApprovalRequestDB)
                .where(
                    ApprovalRequestDB.id == approval_id,
                    ApprovalRequestDB.status == "granted",
                )
                .values(status="consumed", consumed_at=now)
            )
            if int(getattr(transition, "rowcount", 0) or 0) != 1:
                db.rollback()
                return False
            db.commit()
        _emit_codehug_audit(
            actor_id=actor_id,
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            decision="allow",
            reason_code="codehug_approval_consumed",
        )
        return True


class ScopedEffectiveSourceAccessRouter:
    """Create the effective access evaluator from the request's Hub scope."""

    def __init__(self, service_or_factory: object) -> None:
        self._value = service_or_factory

    def verify_dispatch(self, **kwargs: object):
        value = self._value
        service = (
            value(
                tenant_id=str(kwargs["tenant_id"]),
                project_id=str(kwargs["project_id"]),
            )
            if callable(value)
            else value
        )
        verify = getattr(service, "verify_dispatch", None)
        if not callable(verify):
            raise PersistentCodeHugAdapterError(
                "effective_source_access_unavailable", status_code=503
            )
        return verify(**kwargs)


class ContentFreeCodeHugSecurityAudit:
    def record(self, **event: object) -> None:
        _emit_codehug_audit(
            actor_id=str(event.get("actor_id") or ""),
            tenant_id=str(event.get("tenant_id") or ""),
            project_id=str(event.get("project_id") or ""),
            source_revision_id=str(
                event.get("source_revision_id") or ""
            ),
            decision=str(event.get("decision") or "deny"),
            reason_code=str(event.get("reason_code") or "codehug_denied"),
        )


def build_persistent_codehug_authorization(
    *,
    engine: Engine,
    tools: object,
    executor: object,
    effective_access: object,
    signing_key: SourceAccessSigningKey,
) -> CodeHugMutationAuthorizationService:
    if not callable(getattr(tools, "resolve", None)):
        raise PersistentCodeHugAdapterError(
            "codehug_tool_provider_unavailable", status_code=503
        )
    if not callable(getattr(executor, "execute", None)):
        raise PersistentCodeHugAdapterError(
            "codehug_mutation_executor_unavailable", status_code=503
        )
    grants = SQLSourceAccessEnforcementAdapter(engine)
    return CodeHugMutationAuthorizationService(
        tools=tools,
        effective_access=ScopedEffectiveSourceAccessRouter(
            effective_access
        ),
        grants=SourceAccessEnforcementService(
            grants=grants,
            consumptions=grants,
            signer=HubSourceAccessManifestSigner(signing_key),
        ),
        executor=executor,
        audit=ContentFreeCodeHugSecurityAudit(),
    )


def _grant_contract(row: SourceAccessGrantDB) -> SourceAccessGrant:
    return SourceAccessGrant(
        schema="ananta.source-control.source-access-grant.v1",
        authority="hub",
        grant_id=row.grant_id,
        version=row.grant_version,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        source_revision_id=row.source_revision_id,
        destination_id=row.destination_id,
        operation=row.operation,
        transformation=row.transformation,
        purpose=row.purpose,
        policy_version=row.policy_version,
        state=row.state,
        issued_at=datetime.fromtimestamp(
            row.issued_at_epoch, tz=timezone.utc
        ),
        expires_at=datetime.fromtimestamp(
            row.expires_at_epoch, tz=timezone.utc
        ),
    )


def _emit_codehug_audit(
    *,
    actor_id: str,
    tenant_id: str,
    project_id: str,
    source_revision_id: str,
    decision: str,
    reason_code: str,
) -> None:
    safe_reason = str(reason_code or "").strip().lower().replace("-", "_")
    if _REASON.fullmatch(safe_reason) is None:
        safe_reason = "codehug_denied"
    trace = hashlib.sha256(
        f"{actor_id}\0{source_revision_id}\0{safe_reason}".encode("utf-8")
    ).hexdigest()[:24]
    emit_source_control_audit(
        SourceControlAuditEvent(
            operation=SourceControlAuditOperation.approval,
            actor_id=actor_id,
            tenant_id=tenant_id,
            project_id=project_id,
            resource_kind="source_revision",
            resource_id=source_revision_id,
            trace_id=f"codehug-{trace}",
            decision=(
                SourceControlDecision.allow
                if decision == "allow"
                else SourceControlDecision.deny
            ),
            reason_code=safe_reason,
        )
    )


__all__ = [
    "ContentFreeCodeHugSecurityAudit",
    "PersistentCodeHugAdapterError",
    "ResolvedCodeHugDestinationCatalog",
    "SQLCodeHugApprovalStore",
    "SQLCodeHugMutationIntentCatalog",
    "SQLCodeHugRevisionCatalog",
    "ScopedEffectiveSourceAccessRouter",
    "build_persistent_codehug_authorization",
]
