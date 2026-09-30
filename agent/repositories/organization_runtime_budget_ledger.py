"""Atomic hierarchical Organization budget reservation/settlement SQL adapter."""

from __future__ import annotations

import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from agent.db_models.organization_runtime import (
    OrganizationBudgetReservationDB,
    OrganizationBudgetUsageDB,
)
from agent.db_models.organizations import OrganizationInstanceDB
from agent.models.organization_budget import (
    OrganizationBudgetDecision,
    OrganizationBudgetLimit,
    OrganizationBudgetRequest,
    OrganizationBudgetUsage,
    organization_budget_policy_hash,
    organization_budget_request_digest,
    organization_budget_settlement_digest,
)
from agent.repositories.organization_runtime_support import (
    SessionFactory,
    default_session,
)


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
        self._session_factory = session_factory or default_session

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
            and row.policy_hash == organization_budget_policy_hash(limits)
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


__all__ = [
    "SqlOrganizationBudgetLedger",
]
