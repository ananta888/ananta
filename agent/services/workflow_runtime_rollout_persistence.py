"""SQLAlchemy persistence adapters for Hub workflow-runtime rollout policy."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowRuntimeRolloutAuditDB,
    WorkflowRuntimeRolloutPolicyDB,
)
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)
from agent.services.workflow_runtime_rollout_service import (
    StoredWorkflowRolloutPolicy,
    WorkflowRolloutAuditEvent,
    WorkflowRolloutPolicy,
    WorkflowRolloutScope,
)


class WorkflowRolloutPersistenceError(RuntimeError):
    """Stable fail-closed persistence/CAS error."""


class SQLAlchemyWorkflowRolloutPolicyStore(SQLAlchemyStoreSupport):
    """Persist a policy change and its immutable audit event atomically."""

    def __init__(self, bind: Engine | SessionFactory) -> None:
        super().__init__(bind)

    def get(self, scope: WorkflowRolloutScope) -> StoredWorkflowRolloutPolicy | None:
        scope.assert_valid()
        with self._read_session() as session:
            row = session.get(WorkflowRuntimeRolloutPolicyDB, scope.scope_key)
            return _stored_policy(row) if row is not None else None

    def commit(
        self,
        policy: WorkflowRolloutPolicy,
        *,
        expected_revision: int,
        parent_revision: int | None,
        audit: WorkflowRolloutAuditEvent,
    ) -> StoredWorkflowRolloutPolicy:
        policy.assert_valid()
        audit.assert_valid()
        try:
            with self._transaction() as session:
                parent_scope = policy.scope.parent()
                if parent_scope is not None:
                    parent = session.execute(
                        self._for_update(
                            sa.select(WorkflowRuntimeRolloutPolicyDB).where(
                                WorkflowRuntimeRolloutPolicyDB.id
                                == parent_scope.scope_key
                            )
                        )
                    ).scalar_one_or_none()
                    if parent is None or int(parent.revision) != int(
                        parent_revision or 0
                    ):
                        raise WorkflowRolloutPersistenceError(
                            "workflow_rollout_parent_revision_conflict"
                        )

                current = session.execute(
                    self._for_update(
                        sa.select(WorkflowRuntimeRolloutPolicyDB).where(
                            WorkflowRuntimeRolloutPolicyDB.id
                            == policy.scope.scope_key
                        )
                    )
                ).scalar_one_or_none()
                actual = int(current.revision) if current is not None else 0
                if actual != int(expected_revision):
                    raise WorkflowRolloutPersistenceError(
                        "workflow_rollout_policy_cas_conflict"
                    )
                next_revision = actual + 1
                if current is None:
                    current = _policy_row(
                        policy,
                        revision=next_revision,
                        timestamp=audit.occurred_at,
                    )
                    session.add(current)
                else:
                    current.scope_type = policy.scope.scope_type
                    current.policy_version = policy.policy_version
                    current.mode = policy.mode
                    current.revision = next_revision
                    current.updated_at = audit.occurred_at
                    current.policy = policy.to_dict()
                session.add(_audit_row(audit))
                session.flush()
                return StoredWorkflowRolloutPolicy(
                    policy=policy,
                    revision=next_revision,
                    updated_at=audit.occurred_at,
                )
        except IntegrityError as exc:
            raise WorkflowRolloutPersistenceError(
                "workflow_rollout_change_conflict"
            ) from exc

    def append_audit(self, event: WorkflowRolloutAuditEvent) -> None:
        event.assert_valid()
        try:
            with self._transaction() as session:
                session.add(_audit_row(event))
                session.flush()
        except IntegrityError as exc:
            raise WorkflowRolloutPersistenceError(
                "workflow_rollout_audit_event_duplicate"
            ) from exc

    def list_audit(
        self, scope: WorkflowRolloutScope
    ) -> tuple[WorkflowRolloutAuditEvent, ...]:
        scope.assert_valid()
        statement = (
            sa.select(WorkflowRuntimeRolloutAuditDB)
            .where(WorkflowRuntimeRolloutAuditDB.scope_key == scope.scope_key)
            .order_by(
                WorkflowRuntimeRolloutAuditDB.occurred_at.asc(),
                WorkflowRuntimeRolloutAuditDB.id.asc(),
            )
        )
        with self._read_session() as session:
            return tuple(
                WorkflowRolloutAuditEvent(
                    event_id=str(row.event["event_id"]),
                    scope=WorkflowRolloutScope.from_mapping(row.event["scope"]),
                    action=str(row.event["action"]),
                    actor_id=str(row.event["actor_id"]),
                    reason_code=str(row.event["reason_code"]),
                    occurred_at=float(row.event["occurred_at"]),
                    details=dict(row.event.get("details") or {}),
                    schema=str(row.event.get("schema") or ""),
                )
                for row in session.execute(statement).scalars().all()
            )


def _policy_row(
    policy: WorkflowRolloutPolicy,
    *,
    revision: int,
    timestamp: float,
) -> WorkflowRuntimeRolloutPolicyDB:
    scope = policy.scope
    return WorkflowRuntimeRolloutPolicyDB(
        id=scope.scope_key,
        scope_type=scope.scope_type,
        project_id=scope.project_id,
        tenant_id=scope.tenant_id,
        profile_id=scope.profile_id,
        workflow_id=scope.workflow_id,
        policy_version=policy.policy_version,
        mode=policy.mode,
        revision=revision,
        created_at=timestamp,
        updated_at=timestamp,
        policy=policy.to_dict(),
    )


def _audit_row(event: WorkflowRolloutAuditEvent) -> WorkflowRuntimeRolloutAuditDB:
    scope = event.scope
    return WorkflowRuntimeRolloutAuditDB(
        id=stable_row_id("wfra", scope.scope_key, event.event_id),
        scope_key=scope.scope_key,
        scope_type=scope.scope_type,
        project_id=scope.project_id,
        tenant_id=scope.tenant_id,
        profile_id=scope.profile_id,
        workflow_id=scope.workflow_id,
        action=event.action,
        actor_id=event.actor_id,
        reason_code=event.reason_code,
        occurred_at=event.occurred_at,
        event=event.to_dict(),
    )


def _stored_policy(row: WorkflowRuntimeRolloutPolicyDB) -> StoredWorkflowRolloutPolicy:
    return StoredWorkflowRolloutPolicy(
        policy=WorkflowRolloutPolicy.from_mapping(dict(row.policy)),
        revision=int(row.revision),
        updated_at=float(row.updated_at),
    )


__all__ = [
    "SQLAlchemyWorkflowRolloutPolicyStore",
    "WorkflowRolloutPersistenceError",
]
