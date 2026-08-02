"""SQL adapters for Hub-owned Organization runtime ports.

Every adapter is bound to one immutable tenant/project/Organization scope.
This keeps callers from accidentally turning a local identifier into a
cross-tenant lookup and makes the scope part of every compare-and-swap.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.db_models.blueprints import ArtifactDB, ArtifactVersionDB
from agent.db_models.organization_runtime import (
    OrganizationBudgetReservationDB,
    OrganizationBudgetUsageDB,
    OrganizationRuntimeEventDB,
    OrganizationTeamHandoffDB,
    OrganizationWorkflowLoopStateDB,
)
from agent.db_models.organizations import OrganizationInstanceDB
from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerJobDB, WorkerSlotLeaseDB
from agent.ports.artifact_handoff import VerifiedArtifactVersion
from agent.services.organization_budget_service import (
    OrganizationBudgetDecision,
    OrganizationBudgetLimit,
    OrganizationBudgetRequest,
    OrganizationBudgetService,
    OrganizationBudgetUsage,
    organization_budget_request_digest,
    organization_budget_settlement_digest,
)
from agent.services.organization_event_service import OrganizationEvent

SessionFactory = Callable[[], Session]
_GROUNDING_REF = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")


def _default_session() -> Session:
    from agent.database import engine

    return Session(engine)


class SqlOrganizationBudgetLedger:
    """Atomic hierarchical budget reservation/settlement adapter."""

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
        self._session_factory = session_factory or _default_session

    def reserve(
        self,
        *,
        request: OrganizationBudgetRequest,
        limits: tuple[OrganizationBudgetLimit, ...],
        policy_hash: str,
    ) -> OrganizationBudgetDecision:
        if request.organization_id != self._organization_id:
            return self._denied(request, policy_hash, "budget_organization_scope_mismatch")
        request_digest = organization_budget_request_digest(request)
        try:
            with self._session_factory() as session, session.begin():
                if not self._lock_organization(session):
                    return self._denied(request, policy_hash, "budget_organization_not_found")
                existing = self._reservation(session, request.reservation_id, for_update=True)
                if existing is not None:
                    return self._replay_decision(
                        existing,
                        request_digest=request_digest,
                        policy_hash=policy_hash,
                    )

                usage_rows: dict[tuple[str, str], OrganizationBudgetUsageDB] = {}
                exceeded: list[str] = []
                for limit in sorted(limits, key=lambda row: (row.scope_kind, row.scope_id)):
                    usage = self._usage_row(
                        session,
                        scope_kind=limit.scope_kind,
                        scope_id=limit.scope_id,
                        for_update=True,
                    )
                    if usage is None:
                        usage = OrganizationBudgetUsageDB(
                            tenant_id=self._tenant_id,
                            project_id=self._project_id,
                            organization_id=self._organization_id,
                            scope_kind=limit.scope_kind,
                            scope_id=limit.scope_id,
                        )
                        session.add(usage)
                        session.flush()
                    usage_rows[(limit.scope_kind, limit.scope_id)] = usage
                    exceeded.extend(self._exceeded(usage, request, limit))
                if exceeded:
                    decision = OrganizationBudgetDecision(
                        allowed=False,
                        reason_code="organization_budget_exhausted",
                        reservation_id=request.reservation_id,
                        policy_hash=policy_hash,
                        exceeded_scopes=tuple(sorted(exceeded)),
                    )
                    session.add(
                        self._new_reservation(
                            request=request,
                            limits=limits,
                            request_digest=request_digest,
                            policy_hash=policy_hash,
                            status="denied",
                            reason_code=decision.reason_code,
                            exceeded_scopes=decision.exceeded_scopes,
                        )
                    )
                    return decision

                now = time.time()
                for usage in usage_rows.values():
                    usage.tokens_used += request.tokens
                    usage.cost_used = Decimal(usage.cost_used) + request.cost
                    usage.wall_seconds_used += request.wall_seconds
                    usage.parallel_slots_reserved += request.parallel_slots
                    usage.revision += 1
                    usage.updated_at = now
                session.add(
                    self._new_reservation(
                        request=request,
                        limits=limits,
                        request_digest=request_digest,
                        policy_hash=policy_hash,
                        status="reserved",
                        reason_code="organization_budget_reserved",
                        exceeded_scopes=(),
                    )
                )
            return OrganizationBudgetDecision(
                True,
                "organization_budget_reserved",
                request.reservation_id,
                policy_hash,
                (),
            )
        except (IntegrityError, OperationalError):
            return self._authoritative_race_decision(
                request=request,
                request_digest=request_digest,
                policy_hash=policy_hash,
            )

    def settle(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal,
        actual_wall_seconds: int,
    ) -> bool:
        settlement_digest = self._settlement_digest(
            actual_tokens=actual_tokens,
            actual_cost=actual_cost,
            actual_wall_seconds=actual_wall_seconds,
        )
        try:
            with self._session_factory() as session, session.begin():
                if not self._lock_organization(session):
                    return False
                reservation = self._reservation(session, reservation_id, for_update=True)
                if reservation is None:
                    return False
                if not self._reservation_integrity(reservation):
                    return False
                if reservation.status == "settled":
                    return reservation.settlement_digest == settlement_digest
                if reservation.status != "reserved":
                    return False

                for raw_limit in sorted(
                    list(reservation.limits_json or []),
                    key=lambda row: (str(row.get("scope_kind")), str(row.get("scope_id"))),
                ):
                    usage = self._usage_row(
                        session,
                        scope_kind=str(raw_limit.get("scope_kind") or ""),
                        scope_id=str(raw_limit.get("scope_id") or ""),
                        for_update=True,
                    )
                    if usage is None:
                        return False
                    usage.tokens_used = max(
                        0,
                        usage.tokens_used - reservation.requested_tokens + actual_tokens,
                    )
                    usage.cost_used = max(
                        Decimal("0"),
                        Decimal(usage.cost_used) - Decimal(reservation.requested_cost) + actual_cost,
                    )
                    usage.wall_seconds_used = max(
                        0,
                        usage.wall_seconds_used - reservation.requested_wall_seconds + actual_wall_seconds,
                    )
                    usage.parallel_slots_reserved = max(
                        0,
                        usage.parallel_slots_reserved - reservation.requested_parallel_slots,
                    )
                    usage.revision += 1
                    usage.updated_at = time.time()
                reservation.actual_tokens = actual_tokens
                reservation.actual_cost = actual_cost
                reservation.actual_wall_seconds = actual_wall_seconds
                reservation.settlement_digest = settlement_digest
                reservation.status = "settled"
                reservation.revision += 1
                reservation.settled_at = time.time()
            return True
        except (IntegrityError, OperationalError):
            with self._session_factory() as session:
                reservation = self._reservation(session, reservation_id)
                return bool(
                    reservation is not None
                    and reservation.status == "settled"
                    and reservation.settlement_digest == settlement_digest
                )

    def usage(self) -> dict[str, OrganizationBudgetUsage]:
        with self._session_factory() as session:
            rows = session.exec(
                select(OrganizationBudgetUsageDB)
                .where(OrganizationBudgetUsageDB.tenant_id == self._tenant_id)
                .where(OrganizationBudgetUsageDB.project_id == self._project_id)
                .where(OrganizationBudgetUsageDB.organization_id == self._organization_id)
                .order_by(
                    OrganizationBudgetUsageDB.scope_kind,
                    OrganizationBudgetUsageDB.scope_id,
                )
            ).all()
            return {
                f"{row.scope_kind}:{row.scope_id}": OrganizationBudgetUsage(
                    tokens=row.tokens_used,
                    cost=Decimal(row.cost_used),
                    wall_seconds=row.wall_seconds_used,
                    parallel_slots=row.parallel_slots_reserved,
                )
                for row in rows
            }

    def _lock_organization(self, session: Session) -> bool:
        return (
            session.exec(
                select(OrganizationInstanceDB)
                .where(OrganizationInstanceDB.tenant_id == self._tenant_id)
                .where(OrganizationInstanceDB.project_id == self._project_id)
                .where(OrganizationInstanceDB.organization_id == self._organization_id)
                .with_for_update()
            ).first()
            is not None
        )

    def _reservation(
        self,
        session: Session,
        reservation_id: str,
        *,
        for_update: bool = False,
    ) -> OrganizationBudgetReservationDB | None:
        statement = (
            select(OrganizationBudgetReservationDB)
            .where(OrganizationBudgetReservationDB.tenant_id == self._tenant_id)
            .where(OrganizationBudgetReservationDB.project_id == self._project_id)
            .where(OrganizationBudgetReservationDB.organization_id == self._organization_id)
            .where(OrganizationBudgetReservationDB.reservation_id == reservation_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return session.exec(statement).first()

    def _usage_row(
        self,
        session: Session,
        *,
        scope_kind: str,
        scope_id: str,
        for_update: bool = False,
    ) -> OrganizationBudgetUsageDB | None:
        statement = (
            select(OrganizationBudgetUsageDB)
            .where(OrganizationBudgetUsageDB.tenant_id == self._tenant_id)
            .where(OrganizationBudgetUsageDB.project_id == self._project_id)
            .where(OrganizationBudgetUsageDB.organization_id == self._organization_id)
            .where(OrganizationBudgetUsageDB.scope_kind == scope_kind)
            .where(OrganizationBudgetUsageDB.scope_id == scope_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return session.exec(statement).first()

    @staticmethod
    def _exceeded(
        usage: OrganizationBudgetUsageDB,
        request: OrganizationBudgetRequest,
        limit: OrganizationBudgetLimit,
    ) -> list[str]:
        prefix = f"{limit.scope_kind}:{limit.scope_id}"
        values: list[str] = []
        if usage.tokens_used + request.tokens > limit.max_tokens:
            values.append(f"{prefix}:tokens")
        if Decimal(usage.cost_used) + request.cost > limit.max_cost:
            values.append(f"{prefix}:cost")
        if usage.wall_seconds_used + request.wall_seconds > limit.max_wall_seconds:
            values.append(f"{prefix}:time")
        if usage.parallel_slots_reserved + request.parallel_slots > limit.max_parallelism:
            values.append(f"{prefix}:parallelism")
        return values

    @staticmethod
    def _limit_payload(limit: OrganizationBudgetLimit) -> dict[str, Any]:
        return {
            "scope_kind": limit.scope_kind,
            "scope_id": limit.scope_id,
            "max_tokens": limit.max_tokens,
            "max_cost": str(limit.max_cost),
            "max_wall_seconds": limit.max_wall_seconds,
            "max_parallelism": limit.max_parallelism,
            "revision": limit.revision,
        }

    def _new_reservation(
        self,
        *,
        request: OrganizationBudgetRequest,
        limits: tuple[OrganizationBudgetLimit, ...],
        request_digest: str,
        policy_hash: str,
        status: str,
        reason_code: str,
        exceeded_scopes: tuple[str, ...],
    ) -> OrganizationBudgetReservationDB:
        return OrganizationBudgetReservationDB(
            tenant_id=self._tenant_id,
            project_id=self._project_id,
            organization_id=self._organization_id,
            reservation_id=request.reservation_id,
            unit_id=request.unit_id,
            team_id=request.team_id,
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            model_profile=request.model_profile,
            requested_tokens=request.tokens,
            requested_cost=request.cost,
            requested_wall_seconds=request.wall_seconds,
            requested_parallel_slots=request.parallel_slots,
            limits_json=[self._limit_payload(row) for row in limits],
            request_digest=request_digest,
            policy_hash=policy_hash,
            status=status,
            reason_code=reason_code,
            exceeded_scopes=list(exceeded_scopes),
        )

    @staticmethod
    def _settlement_digest(
        *,
        actual_tokens: int,
        actual_cost: Decimal,
        actual_wall_seconds: int,
    ) -> str:
        return organization_budget_settlement_digest(
            actual_tokens=actual_tokens,
            actual_cost=actual_cost,
            actual_wall_seconds=actual_wall_seconds,
        )

    @staticmethod
    def _replay_decision(
        existing: OrganizationBudgetReservationDB,
        *,
        request_digest: str,
        policy_hash: str,
    ) -> OrganizationBudgetDecision:
        if not SqlOrganizationBudgetLedger._reservation_integrity(existing):
            return OrganizationBudgetDecision(
                allowed=False,
                reason_code="budget_reservation_integrity_mismatch",
                reservation_id=existing.reservation_id,
                policy_hash=policy_hash,
                exceeded_scopes=(),
            )
        matches = existing.request_digest == request_digest and existing.policy_hash == policy_hash
        if not matches:
            return OrganizationBudgetDecision(
                allowed=False,
                reason_code="budget_reservation_conflict",
                reservation_id=existing.reservation_id,
                policy_hash=policy_hash,
                exceeded_scopes=(),
            )
        allowed = existing.status in {"reserved", "settled"}
        return OrganizationBudgetDecision(
            allowed=allowed,
            reason_code=("budget_reservation_replayed" if allowed else existing.reason_code),
            reservation_id=existing.reservation_id,
            policy_hash=policy_hash,
            exceeded_scopes=tuple(existing.exceeded_scopes or ()),
            replayed=True,
        )

    @staticmethod
    def _reservation_integrity(row: OrganizationBudgetReservationDB) -> bool:
        try:
            request = OrganizationBudgetRequest(
                reservation_id=row.reservation_id,
                organization_id=row.organization_id,
                unit_id=row.unit_id,
                team_id=row.team_id,
                workflow_id=row.workflow_id,
                task_id=row.task_id,
                tokens=row.requested_tokens,
                cost=Decimal(row.requested_cost),
                wall_seconds=row.requested_wall_seconds,
                parallel_slots=row.requested_parallel_slots,
                model_profile=row.model_profile,
            )
            limits = tuple(
                OrganizationBudgetLimit(
                    scope_kind=str(raw["scope_kind"]),
                    scope_id=str(raw["scope_id"]),
                    max_tokens=int(raw["max_tokens"]),
                    max_cost=Decimal(str(raw["max_cost"])),
                    max_wall_seconds=int(raw["max_wall_seconds"]),
                    max_parallelism=int(raw["max_parallelism"]),
                    revision=str(raw["revision"]),
                )
                for raw in list(row.limits_json or [])
                if isinstance(raw, Mapping)
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return False
        base_valid = (
            len(limits) == len(list(row.limits_json or []))
            and row.request_digest == organization_budget_request_digest(request)
            and row.policy_hash == OrganizationBudgetService.policy_hash(limits)
        )
        if not base_valid:
            return False
        if row.status == "settled":
            if (
                row.actual_tokens is None
                or row.actual_cost is None
                or row.actual_wall_seconds is None
                or row.settlement_digest is None
            ):
                return False
            return row.settlement_digest == organization_budget_settlement_digest(
                actual_tokens=row.actual_tokens,
                actual_cost=Decimal(row.actual_cost),
                actual_wall_seconds=row.actual_wall_seconds,
            )
        return (
            row.actual_tokens is None
            and row.actual_cost is None
            and row.actual_wall_seconds is None
            and row.settlement_digest is None
        )

    def _authoritative_race_decision(
        self,
        *,
        request: OrganizationBudgetRequest,
        request_digest: str,
        policy_hash: str,
    ) -> OrganizationBudgetDecision:
        with self._session_factory() as session:
            existing = self._reservation(session, request.reservation_id)
            if existing is not None:
                return self._replay_decision(
                    existing,
                    request_digest=request_digest,
                    policy_hash=policy_hash,
                )
        return self._denied(request, policy_hash, "budget_reservation_race")

    @staticmethod
    def _denied(
        request: OrganizationBudgetRequest,
        policy_hash: str,
        reason_code: str,
    ) -> OrganizationBudgetDecision:
        return OrganizationBudgetDecision(
            False,
            reason_code,
            request.reservation_id,
            policy_hash,
            (),
        )


class SqlOrganizationEventStore:
    """Per-Organization ordered append-only event store."""

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
        self._session_factory = session_factory or _default_session

    def append_once(self, event: OrganizationEvent) -> tuple[bool, OrganizationEvent]:
        if event.organization_id != self._organization_id:
            raise ValueError("organization_event_scope_mismatch")
        try:
            with self._session_factory() as session, session.begin():
                organization = session.exec(
                    select(OrganizationInstanceDB)
                    .where(OrganizationInstanceDB.tenant_id == self._tenant_id)
                    .where(OrganizationInstanceDB.project_id == self._project_id)
                    .where(OrganizationInstanceDB.organization_id == self._organization_id)
                    .with_for_update()
                ).first()
                if organization is None:
                    raise ValueError("organization_event_organization_not_found")
                existing = self._event(session, event.event_id)
                if existing is not None:
                    return False, self._domain_event(existing)
                last_sequence = session.exec(
                    select(sa.func.max(OrganizationRuntimeEventDB.sequence))
                    .where(OrganizationRuntimeEventDB.tenant_id == self._tenant_id)
                    .where(OrganizationRuntimeEventDB.project_id == self._project_id)
                    .where(OrganizationRuntimeEventDB.organization_id == self._organization_id)
                ).one()
                normalized = OrganizationEvent(
                    **{
                        **asdict(event),
                        "sequence": int(last_sequence or 0) + 1,
                    }
                )
                session.add(
                    OrganizationRuntimeEventDB(
                        tenant_id=self._tenant_id,
                        project_id=self._project_id,
                        organization_id=self._organization_id,
                        event_id=normalized.event_id,
                        event_type=normalized.event_type,
                        definition_revision=normalized.definition_revision,
                        snapshot_hash=normalized.snapshot_hash,
                        correlation_id=normalized.correlation_id,
                        sequence=normalized.sequence,
                        occurred_at=normalized.occurred_at,
                        payload_json=dict(normalized.payload),
                        semantic_digest=self._semantic_digest(normalized),
                    )
                )
            return True, normalized
        except (IntegrityError, OperationalError):
            with self._session_factory() as session:
                existing = self._event(session, event.event_id)
                if existing is None:
                    raise ValueError("organization_event_append_race") from None
                return False, self._domain_event(existing)

    def list_for_organization(self, organization_id: str) -> tuple[OrganizationEvent, ...]:
        if organization_id != self._organization_id:
            return ()
        with self._session_factory() as session:
            rows = session.exec(
                select(OrganizationRuntimeEventDB)
                .where(OrganizationRuntimeEventDB.tenant_id == self._tenant_id)
                .where(OrganizationRuntimeEventDB.project_id == self._project_id)
                .where(OrganizationRuntimeEventDB.organization_id == self._organization_id)
                .order_by(OrganizationRuntimeEventDB.sequence)
            ).all()
            return tuple(self._domain_event(row) for row in rows)

    def _event(
        self,
        session: Session,
        event_id: str,
    ) -> OrganizationRuntimeEventDB | None:
        return session.exec(
            select(OrganizationRuntimeEventDB)
            .where(OrganizationRuntimeEventDB.tenant_id == self._tenant_id)
            .where(OrganizationRuntimeEventDB.project_id == self._project_id)
            .where(OrganizationRuntimeEventDB.organization_id == self._organization_id)
            .where(OrganizationRuntimeEventDB.event_id == event_id)
        ).first()

    @staticmethod
    def _domain_event(row: OrganizationRuntimeEventDB) -> OrganizationEvent:
        event = OrganizationEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            organization_id=row.organization_id,
            definition_revision=row.definition_revision,
            snapshot_hash=row.snapshot_hash,
            correlation_id=row.correlation_id,
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            payload=dict(row.payload_json or {}),
        )
        if row.semantic_digest != SqlOrganizationEventStore._semantic_digest(event):
            raise ValueError("organization_event_integrity_mismatch")
        return event

    @staticmethod
    def _semantic_digest(event: OrganizationEvent) -> str:
        payload = asdict(event)
        payload.pop("sequence", None)
        payload.pop("occurred_at", None)
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


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
        self._session_factory = session_factory or _default_session

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
        normalized_contract = json.loads(_canonical_json(dict(contract)))
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
                contract_digest=hashlib.sha256(_canonical_json(normalized_contract).encode()).hexdigest(),
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
        if row.contract_digest != hashlib.sha256(_canonical_json(contract).encode()).hexdigest():
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
        self._session_factory = session_factory or _default_session

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


class SqlArtifactVersionReader:
    """Adapter over the goal graph and existing artifact/version tables.

    The goal graph owns goal/provenance/verification membership; the SQL
    artifact tables own immutable version bytes and their SHA-256.  A handoff
    is released only when both authorities name the same digest.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        goal_artifacts: GoalArtifactService | None = None,
    ) -> None:
        self._session_factory = session_factory or _default_session
        self._goal_artifacts = goal_artifacts or GoalArtifactService()

    def get_verified_version(
        self,
        *,
        goal_id: str,
        artifact_id: str,
        version: str,
    ) -> VerifiedArtifactVersion | None:
        try:
            graph = self._goal_artifacts.find_goal_graph(goal_id)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if graph is None:
            return None
        candidates = [
            dict(row)
            for row in list(graph.get("output_artifacts") or [])
            if isinstance(row, Mapping)
            and (
                str(row.get("output_artifact_id") or "") == artifact_id
                or str(row.get("artifact_ref") or "") == artifact_id
            )
        ]
        if len(candidates) != 1:
            return None
        output = candidates[0]
        if str(output.get("goal_id") or "") != goal_id or str(output.get("status") or "") != "verified":
            return None
        with self._session_factory() as session:
            artifact = session.get(ArtifactDB, artifact_id)
            if artifact is None:
                return None
            statement = select(ArtifactVersionDB).where(ArtifactVersionDB.artifact_id == artifact_id)
            rows = session.exec(statement).all()
            matching = [row for row in rows if str(row.id) == version or str(row.version_number) == version]
            if len(matching) != 1:
                return None
            selected = matching[0]
            output_extensions = output.get("extensions")
            declared_version_ref = (
                str(output_extensions.get("artifact_version_ref") or "")
                if isinstance(output_extensions, Mapping)
                else ""
            )
            if declared_version_ref:
                if declared_version_ref != str(selected.id):
                    return None
            elif str(artifact.latest_version_id or "") != str(selected.id):
                return None
            output_hash = str(output.get("content_hash") or "").removeprefix("sha256:")
            if not output_hash or output_hash != str(selected.sha256 or ""):
                return None
            evidence_refs, context_scope_refs = self._grounding_refs(
                graph=graph,
                output=output,
            )
            return VerifiedArtifactVersion(
                artifact_id=artifact_id,
                version=version,
                digest=f"sha256:{selected.sha256}",
                verification_status="hub_verified",
                evidence_refs=evidence_refs,
                context_scope_refs=context_scope_refs,
                producer_task_id=(str(output.get("task_id") or "") or None),
            )

    @staticmethod
    def _grounding_refs(
        *,
        graph: Mapping[str, Any],
        output: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        evidence: set[str] = set()
        context: set[str] = set()

        def collect_extensions(value: Mapping[str, Any]) -> None:
            extensions = value.get("extensions")
            if not isinstance(extensions, Mapping):
                return
            evidence.update(
                str(item)
                for item in list(extensions.get("evidence_refs") or [])
                if _GROUNDING_REF.fullmatch(str(item)) is not None
            )
            context.update(str(item) for item in list(extensions.get("context_scope_refs") or []) if str(item))

        collect_extensions(output)
        usage_refs = {str(value) for value in list(output.get("input_usage_refs") or []) if str(value)}
        provenance_id = str(output.get("provenance_id") or "")
        for raw in list(dict(graph.get("extensions") or {}).get("execution_provenance") or []):
            if not isinstance(raw, Mapping) or str(raw.get("provenance_id") or "") != provenance_id:
                continue
            usage_refs.update(str(value) for value in list(raw.get("input_usage_refs") or []) if str(value))
            collect_extensions(raw)
        for raw in list(graph.get("source_usages") or []):
            if not isinstance(raw, Mapping) or str(raw.get("usage_id") or "") not in usage_refs:
                continue
            artifact_ref = str(raw.get("artifact_ref") or "")
            if _GROUNDING_REF.fullmatch(artifact_ref) is not None:
                evidence.add(artifact_ref)
        return tuple(sorted(evidence)), tuple(sorted(context))


class SqlAssignmentEvidenceVerifier:
    """Fail-closed exact allowlist check bound to a Hub dispatch lease."""

    def __init__(self, *, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory or _default_session

    def verify(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context_scope_refs: tuple[str, ...],
        assignment_id: str,
        dispatch_lease_id: str,
    ) -> tuple[bool, tuple[str, ...]]:
        with self._session_factory() as session:
            job = session.get(WorkerJobDB, dispatch_lease_id)
            slot_lease = (
                session.get(WorkerSlotLeaseDB, str(job.slot_lease_id or ""))
                if job is not None and job.slot_lease_id
                else None
            )
            if (
                job is None
                or str(job.subtask_id or "") != assignment_id
                or not job.parent_task_id
                or str(job.status or "") not in {"delegated", "running"}
                or job.finished_at is not None
                or (job.slot_lease_id and slot_lease is None)
                or (
                    slot_lease is not None
                    and (
                        slot_lease.status != "active"
                        or float(slot_lease.deadline_at) <= time.time()
                        or slot_lease.released_at is not None
                        or str(slot_lease.parent_task_id or "") not in {"", str(job.parent_task_id or "")}
                        or str(slot_lease.worker_job_id or "") not in {"", dispatch_lease_id}
                    )
                )
            ):
                return False, ("handoff_dispatch_lease_binding_invalid",)
            task = session.get(TaskDB, job.parent_task_id)
            if task is None:
                return False, ("handoff_source_task_not_found",)
            if str(task.current_worker_job_id or "") != str(job.id) or str(task.status or "").strip().lower() in {
                "completed",
                "failed",
                "cancelled",
                "verification_failed",
                "skipped",
                "aborted",
                "timeout",
                "archived",
            }:
                return False, ("handoff_dispatch_lease_stale",)
            context = dict(task.worker_execution_context or {})
            allowed_evidence = {
                str(value)
                for key in ("allowed_source_refs", "allowed_run_refs")
                for value in list(context.get(key) or [])
                if str(value)
            }
            allowed_context = {str(value) for value in list(context.get("allowed_context_refs") or []) if str(value)}
        reasons: list[str] = []
        if not evidence_refs or any(
            _GROUNDING_REF.fullmatch(reference) is None or reference not in allowed_evidence
            for reference in evidence_refs
        ):
            reasons.append("handoff_evidence_allowlist_mismatch")
        if not context_scope_refs or any(reference not in allowed_context for reference in context_scope_refs):
            reasons.append("handoff_context_scope_not_allowed")
        return not reasons, tuple(reasons)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "SqlArtifactVersionReader",
    "SqlAssignmentEvidenceVerifier",
    "SqlHandoffStateStore",
    "SqlOrganizationBudgetLedger",
    "SqlOrganizationEventStore",
    "SqlOrganizationWorkflowLoopStore",
]
