"""Small adapters wired by the Source Control composition root.

Context Policy source/destination resolvers over the Hub persistence and
destination catalog, and the content-free route-denial audit sink.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlmodel import Session, select

from agent.bootstrap.source_control_audit_fields import (
    bounded_id as _bounded_id,
)
from agent.bootstrap.source_control_audit_fields import (
    bounded_reason as _bounded_reason,
)
from agent.db_models.source_control import SourceRevisionDB
from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    bounded_metric_labels,
    emit_source_control_audit,
)


# Keep the established log channel of the composition root.
_LOG = logging.getLogger("agent.bootstrap.source_control_api")


class SQLContextPolicySources:
    def __init__(self, database_engine) -> None:
        self._engine = database_engine

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> Mapping[str, Any] | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                )
            ).first()
            if row is None:
                return None
            return {
                "connector_type": row.connector_type,
                "sensitivity": row.sensitivity,
                "admission_state": row.admission_state,
            }


class ContextPolicyDestinations:
    def __init__(self, catalog: object) -> None:
        self._catalog = catalog

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> Mapping[str, Any] | None:
        get = getattr(self._catalog, "get", None)
        if not callable(get):
            return None
        value = get(
            tenant_id=tenant_id,
            project_id=project_id,
            destination_id=destination_id,
        )
        if value is None:
            return None
        to_wire = getattr(value, "to_wire", None)
        if callable(to_wire):
            return dict(to_wire())
        if isinstance(value, Mapping):
            return dict(value)
        return None


class SourceControlRouteDenyAudit:
    """Adapt bounded route denials to the shared content-free audit contract."""

    def __init__(self, *, health: object, metrics: object) -> None:
        self._health = health
        self._metrics = metrics

    def record_denial(self, event: Mapping[str, object]) -> None:
        reason_code = _bounded_reason(
            event.get("reason_code"), fallback="authorization_denied"
        )
        try:
            emit_source_control_audit(
                SourceControlAuditEvent(
                    operation=SourceControlAuditOperation.deny,
                    actor_id=_bounded_id(
                        event.get("actor_id")
                        or event.get("subject_id"),
                        fallback="actor",
                    ),
                    tenant_id=_bounded_id(
                        event.get("tenant_id"), fallback="tenant"
                    ),
                    project_id=_bounded_id(
                        event.get("project_id"), fallback="project"
                    ),
                    resource_kind=_bounded_id(
                        event.get("resource_kind"), fallback="resource"
                    ),
                    resource_id=_bounded_id(
                        event.get("resource_id"), fallback="collection"
                    ),
                    trace_id=_bounded_id(
                        event.get("trace_id"), fallback="route-deny"
                    ),
                    decision=SourceControlDecision.deny,
                    reason_code=reason_code,
                )
            )
            self._health.record_failure(reason_code)
            self._metrics.increment(
                "source_control_operations_total",
                bounded_metric_labels(
                    operation="deny",
                    decision="deny",
                    reason_code="authorization",
                    status="failed",
                ),
            )
        except Exception:
            _LOG.error(
                "source_control_route_deny_observability_failed",
                exc_info=True,
            )


__all__ = [
    "ContextPolicyDestinations",
    "SQLContextPolicySources",
    "SourceControlRouteDenyAudit",
]
