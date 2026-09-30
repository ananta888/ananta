"""Scoped compare-and-swap SQL stores for team handoff and workflow loop state."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from agent.db_models.organization_runtime import (
    OrganizationTeamHandoffDB,
    OrganizationWorkflowLoopStateDB,
)
from agent.repositories.organization_runtime_support import (
    SessionFactory,
    canonical_json,
    default_session,
)


class SqlHandoffStateStore:
    """Scoped CAS store plus lifecycle-safe open-handoff resolution."""

    OPEN_STATUSES = ("pending_acceptance", "needs_changes")

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._organization_id = organization_id
        self._session_factory = session_factory or default_session

    def get(self, handoff_id: str) -> dict | None:
        with self._session_factory() as session:
            row = self._row(session, handoff_id)
            return self._state(row) if row is not None else None

    def save_if_revision(
        self,
        handoff_id: str,
        expected_revision: int,
        value: dict,
    ) -> bool:
        if expected_revision < 0 or (
            expected_revision > 0 and int(value.get("revision") or 0) != expected_revision + 1
        ):
            return False
        try:
            with self._session_factory() as session, session.begin():
                if expected_revision == 0:
                    return self._insert(session, handoff_id=handoff_id, value=value)
                result = session.exec(
                    sa.update(OrganizationTeamHandoffDB)
                    .where(OrganizationTeamHandoffDB.tenant_id == self._tenant_id)
                    .where(OrganizationTeamHandoffDB.project_id == self._project_id)
                    .where(OrganizationTeamHandoffDB.organization_id == self._organization_id)
                    .where(OrganizationTeamHandoffDB.handoff_id == handoff_id)
                    .where(OrganizationTeamHandoffDB.revision == expected_revision)
                    .values(**self._update_values(value))
                )
                return int(result.rowcount or 0) == 1
        except (IntegrityError, OperationalError, ValueError, TypeError):
            return False

    def list_open(self) -> tuple[dict[str, Any], ...]:
        with self._session_factory() as session:
            rows = session.exec(
                select(OrganizationTeamHandoffDB)
                .where(OrganizationTeamHandoffDB.tenant_id == self._tenant_id)
                .where(OrganizationTeamHandoffDB.project_id == self._project_id)
                .where(OrganizationTeamHandoffDB.organization_id == self._organization_id)
                .where(OrganizationTeamHandoffDB.status.in_(self.OPEN_STATUSES))
                .order_by(OrganizationTeamHandoffDB.created_at)
            ).all()
            return tuple(self._state(row) for row in rows)

    def list_states(self) -> tuple[dict[str, Any], ...]:
        with self._session_factory() as session:
            rows = session.exec(
                select(OrganizationTeamHandoffDB)
                .where(OrganizationTeamHandoffDB.tenant_id == self._tenant_id)
                .where(OrganizationTeamHandoffDB.project_id == self._project_id)
                .where(OrganizationTeamHandoffDB.organization_id == self._organization_id)
                .order_by(OrganizationTeamHandoffDB.created_at)
            ).all()
            return tuple(self._state(row) for row in rows)

    def resolve_open(
        self,
        *,
        resolution: Literal["needs_changes", "cancelled"],
        reason_code: str,
        actor_principal_id: str,
        idempotency_key: str,
    ) -> tuple[str, ...]:
        if resolution not in {"needs_changes", "cancelled"}:
            raise ValueError("handoff_lifecycle_resolution_invalid")
        if any(not str(value or "").strip() for value in (reason_code, actor_principal_id, idempotency_key)):
            raise ValueError("handoff_lifecycle_resolution_binding_missing")
        resolved: list[str] = []
        with self._session_factory() as session, session.begin():
            rows = session.exec(
                select(OrganizationTeamHandoffDB)
                .where(OrganizationTeamHandoffDB.tenant_id == self._tenant_id)
                .where(OrganizationTeamHandoffDB.project_id == self._project_id)
                .where(OrganizationTeamHandoffDB.organization_id == self._organization_id)
                .order_by(OrganizationTeamHandoffDB.handoff_id)
                .with_for_update()
            ).all()
            now = time.time()
            for row in rows:
                operation_key = (
                    "handoff-lifecycle-"
                    + hashlib.sha256(f"{idempotency_key}:{row.handoff_id}".encode()).hexdigest()[:32]
                )
                if row.decision_idempotency_key == operation_key and row.status == resolution:
                    resolved.append(row.handoff_id)
                    continue
                if row.status not in self.OPEN_STATUSES:
                    continue
                row.status = resolution
                row.reason_code = reason_code
                row.decision_idempotency_key = operation_key
                row.decided_by_principal_id = actor_principal_id
                row.revision += 1
                row.updated_at = now
                row.resolved_at = now if resolution == "cancelled" else None
                resolved.append(row.handoff_id)
        return tuple(resolved)

    def _insert(self, session: Session, *, handoff_id: str, value: dict) -> bool:
        contract = value.get("contract")
        if not isinstance(contract, Mapping):
            return False
        normalized_contract = json.loads(canonical_json(dict(contract)))
        if (
            str(normalized_contract.get("handoff_id") or "") != handoff_id
            or str(normalized_contract.get("organization_id") or "") != self._organization_id
            or int(value.get("revision") or 0) != 1
        ):
            return False
        if self._row(session, handoff_id) is not None:
            return False
        session.add(
            OrganizationTeamHandoffDB(
                tenant_id=self._tenant_id,
                project_id=self._project_id,
                organization_id=self._organization_id,
                handoff_id=handoff_id,
                correlation_id=str(normalized_contract.get("correlation_id") or ""),
                goal_id=str(normalized_contract.get("goal_id") or ""),
                producer_unit_id=str(normalized_contract.get("producer_unit_id") or ""),
                producer_team_id=str(normalized_contract.get("producer_team_id") or ""),
                producer_role_slot_id=str(normalized_contract.get("producer_role_slot_id") or ""),
                producer_task_id=str(normalized_contract.get("producer_task_id") or ""),
                consumer_unit_id=str(normalized_contract.get("consumer_unit_id") or ""),
                consumer_team_id=str(normalized_contract.get("consumer_team_id") or ""),
                consumer_role_slot_id=str(normalized_contract.get("consumer_role_slot_id") or ""),
                consumer_task_id=str(normalized_contract.get("consumer_task_id") or ""),
                contract_json=normalized_contract,
                contract_digest=hashlib.sha256(canonical_json(normalized_contract).encode()).hexdigest(),
                artifact_digests=list(value.get("artifact_digests") or []),
                status=str(value.get("status") or "pending_acceptance"),
                reason_code=str(value.get("reason_code") or "handoff_submitted"),
                idempotency_key=str(value.get("idempotency_key") or ""),
                revision=1,
                due_at=str(normalized_contract.get("due_at") or ""),
                sla_seconds=int(normalized_contract.get("sla_seconds") or 0),
            )
        )
        session.flush()
        return True

    @staticmethod
    def _update_values(value: dict) -> dict[str, Any]:
        status = str(value.get("status") or "")
        return {
            "status": status,
            "reason_code": str(value.get("reason_code") or ""),
            "decision_idempotency_key": (str(value.get("decision_idempotency_key") or "") or None),
            "decided_by_principal_id": (str(value.get("decided_by_principal_id") or "") or None),
            "revision": int(value.get("revision") or 0),
            "updated_at": time.time(),
            "resolved_at": (time.time() if status in {"accepted", "rejected", "cancelled"} else None),
        }

    def _row(
        self,
        session: Session,
        handoff_id: str,
    ) -> OrganizationTeamHandoffDB | None:
        return session.exec(
            select(OrganizationTeamHandoffDB)
            .where(OrganizationTeamHandoffDB.tenant_id == self._tenant_id)
            .where(OrganizationTeamHandoffDB.project_id == self._project_id)
            .where(OrganizationTeamHandoffDB.organization_id == self._organization_id)
            .where(OrganizationTeamHandoffDB.handoff_id == handoff_id)
        ).first()

    @staticmethod
    def _state(row: OrganizationTeamHandoffDB) -> dict[str, Any]:
        contract = dict(row.contract_json or {})
        if row.contract_digest != hashlib.sha256(canonical_json(contract).encode()).hexdigest():
            raise ValueError("handoff_contract_integrity_mismatch")
        return {
            "handoff_id": row.handoff_id,
            "contract": contract,
            "status": row.status,
            "reason_code": row.reason_code,
            "revision": row.revision,
            "idempotency_key": row.idempotency_key,
            "decision_idempotency_key": row.decision_idempotency_key,
            "decided_by_principal_id": row.decided_by_principal_id,
            "artifact_digests": list(row.artifact_digests or []),
            "updated_at": row.updated_at,
        }


class SqlOrganizationWorkflowLoopStore:
    """Scoped state storage with create idempotency and revision CAS."""

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._organization_id = organization_id
        self._session_factory = session_factory or default_session

    def get(self, loop_instance_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = self._row(session, loop_instance_id)
            return self._state(row) if row is not None else None

    def create_once(self, value: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
        loop_instance_id = str(value.get("loop_instance_id") or "")
        request_digest = str(value.get("last_request_digest") or "")
        try:
            with self._session_factory() as session, session.begin():
                existing = self._row(session, loop_instance_id)
                if existing is not None:
                    return False, self._state(existing)
                row = OrganizationWorkflowLoopStateDB(
                    tenant_id=self._tenant_id,
                    project_id=self._project_id,
                    organization_id=self._organization_id,
                    loop_instance_id=loop_instance_id,
                    loop_id=str(value.get("loop_id") or ""),
                    workflow_id=str(value.get("workflow_id") or "") or None,
                    task_id=str(value.get("task_id") or "") or None,
                    unit_id=str(value.get("unit_id") or "") or None,
                    team_id=str(value.get("team_id") or "") or None,
                    definition_revision=str(value.get("definition_revision") or ""),
                    snapshot_hash=str(value.get("snapshot_hash") or ""),
                    policy_json=dict(value.get("policy") or {}),
                    iteration=int(value.get("iteration") or 0),
                    status=str(value.get("status") or "running"),
                    started_at=str(value.get("started_at") or ""),
                    updated_at=str(value.get("updated_at") or ""),
                    accumulated_cost=Decimal(str(value.get("accumulated_cost") or "0")),
                    artifact_versions=list(value.get("artifact_versions") or []),
                    selected_transition=(str(value.get("selected_transition") or "") or None),
                    reason_code=str(value.get("reason_code") or "loop_started"),
                    last_idempotency_key=str(value.get("last_idempotency_key") or ""),
                    last_request_digest=request_digest,
                    revision=1,
                )
                session.add(row)
                session.flush()
                state = self._state(row)
            return True, state
        except (IntegrityError, OperationalError):
            existing = self.get(loop_instance_id)
            if existing is None:
                raise ValueError("organization_loop_create_race") from None
            return False, existing

    def save_if_revision(
        self,
        *,
        loop_instance_id: str,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> bool:
        try:
            with self._session_factory() as session, session.begin():
                result = session.exec(
                    sa.update(OrganizationWorkflowLoopStateDB)
                    .where(OrganizationWorkflowLoopStateDB.tenant_id == self._tenant_id)
                    .where(OrganizationWorkflowLoopStateDB.project_id == self._project_id)
                    .where(OrganizationWorkflowLoopStateDB.organization_id == self._organization_id)
                    .where(OrganizationWorkflowLoopStateDB.loop_instance_id == loop_instance_id)
                    .where(OrganizationWorkflowLoopStateDB.revision == expected_revision)
                    .values(
                        iteration=int(value.get("iteration") or 0),
                        status=str(value.get("status") or ""),
                        updated_at=str(value.get("updated_at") or ""),
                        accumulated_cost=Decimal(str(value.get("accumulated_cost") or "0")),
                        artifact_versions=list(value.get("artifact_versions") or []),
                        selected_transition=(str(value.get("selected_transition") or "") or None),
                        reason_code=str(value.get("reason_code") or ""),
                        last_idempotency_key=str(value.get("last_idempotency_key") or ""),
                        last_request_digest=str(value.get("last_request_digest") or ""),
                        revision=expected_revision + 1,
                    )
                )
                return int(result.rowcount or 0) == 1
        except (IntegrityError, OperationalError, ValueError):
            return False

    def list_states(self) -> tuple[dict[str, Any], ...]:
        with self._session_factory() as session:
            rows = session.exec(
                select(OrganizationWorkflowLoopStateDB)
                .where(OrganizationWorkflowLoopStateDB.tenant_id == self._tenant_id)
                .where(OrganizationWorkflowLoopStateDB.project_id == self._project_id)
                .where(OrganizationWorkflowLoopStateDB.organization_id == self._organization_id)
                .order_by(OrganizationWorkflowLoopStateDB.created_at)
            ).all()
            return tuple(self._state(row) for row in rows)

    def _row(
        self,
        session: Session,
        loop_instance_id: str,
    ) -> OrganizationWorkflowLoopStateDB | None:
        return session.exec(
            select(OrganizationWorkflowLoopStateDB)
            .where(OrganizationWorkflowLoopStateDB.tenant_id == self._tenant_id)
            .where(OrganizationWorkflowLoopStateDB.project_id == self._project_id)
            .where(OrganizationWorkflowLoopStateDB.organization_id == self._organization_id)
            .where(OrganizationWorkflowLoopStateDB.loop_instance_id == loop_instance_id)
        ).first()

    @staticmethod
    def _state(row: OrganizationWorkflowLoopStateDB) -> dict[str, Any]:
        return {
            "loop_instance_id": row.loop_instance_id,
            "loop_id": row.loop_id,
            "workflow_id": row.workflow_id,
            "task_id": row.task_id,
            "unit_id": row.unit_id,
            "team_id": row.team_id,
            "definition_revision": row.definition_revision,
            "snapshot_hash": row.snapshot_hash,
            "policy": dict(row.policy_json or {}),
            "iteration": row.iteration,
            "status": row.status,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "accumulated_cost": str(row.accumulated_cost),
            "artifact_versions": list(row.artifact_versions or []),
            "selected_transition": row.selected_transition,
            "reason_code": row.reason_code,
            "last_idempotency_key": row.last_idempotency_key,
            "last_request_digest": row.last_request_digest,
            "revision": row.revision,
        }


__all__ = [
    "SqlHandoffStateStore",
    "SqlOrganizationWorkflowLoopStore",
]
