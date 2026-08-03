"""Hub-owned intake for passive Organization root Goals."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable

from agent.db_models import GoalDB, OrganizationAuditOutboxDB, OrganizationOperationDB
from agent.models.organization_goal_models import (
    OrganizationGoalCreateCommand,
    OrganizationGoalCreateResult,
)
from agent.models.organization_models import canonical_sha256
from agent.repositories.organizations.ports import OrganizationUnitOfWorkPort
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork

_IDEMPOTENCY_KEY = re.compile(r"^[^\s]{8,191}$")
_ALLOWED_CREDENTIAL_TYPES = frozenset({"user", "service", "hub_service"})
_GOAL_LIFECYCLES = frozenset({"validated", "active"})
_OPERATION_KIND = "organization_goal_create"


class OrganizationGoalApplicationError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int) -> None:
        self.reason_code = reason_code
        self.public_status = int(public_status)
        super().__init__(reason_code)


class OrganizationGoalApplicationService:
    """Create one idempotent Goal without invoking planning or the Task queue."""

    def __init__(
        self,
        *,
        membership_service: OrganizationMembershipService | None = None,
        uow_factory: Callable[[], OrganizationUnitOfWorkPort] | None = None,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._membership = membership_service or OrganizationMembershipService()
        self._uow_factory = uow_factory or OrganizationUnitOfWork
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _step: None)

    def create(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        organization_id: str,
        command: OrganizationGoalCreateCommand,
        idempotency_key: str,
    ) -> OrganizationGoalCreateResult:
        normalized_key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY_KEY.fullmatch(normalized_key) is None:
            raise OrganizationGoalApplicationError(
                "organization_goal_idempotency_key_invalid",
                public_status=400,
            )
        if (
            not principal.principal_id
            or not principal.tenant_id
            or str(principal.credential_type or "").strip().lower() not in _ALLOWED_CREDENTIAL_TYPES
        ):
            raise OrganizationGoalApplicationError(
                "organization_goal_credential_forbidden",
                public_status=403,
            )

        result: OrganizationGoalCreateResult | None = None
        with self._uow_factory() as uow:
            organization = uow.instances.get_scoped(
                principal.tenant_id,
                str(principal.project_id or ""),
                str(organization_id or ""),
                for_update=True,
            )
            if organization is None:
                raise OrganizationGoalApplicationError(
                    "organization_goal_not_found",
                    public_status=404,
                )
            now = self._clock()
            membership = uow.memberships.get_for_principal(
                organization.tenant_id,
                organization.project_id,
                organization.organization_id,
                principal.principal_id,
                for_update=True,
            )
            grants = uow.admin_grants.list_for_principal(
                organization.tenant_id,
                organization.project_id,
                organization.organization_id,
                principal.principal_id,
                for_update=True,
            )
            if not self._membership.mutation_allowed(
                principal=principal,
                tenant_id=organization.tenant_id,
                project_id=organization.project_id,
                organization_id=organization.organization_id,
                grant_kind="planning:goal_create",
                membership=membership,
                grants=grants,
                now=now,
            ):
                raise OrganizationGoalApplicationError(
                    "organization_goal_not_found",
                    public_status=404,
                )
            if str(organization.lifecycle or "").strip().lower() not in _GOAL_LIFECYCLES:
                raise OrganizationGoalApplicationError(
                    "organization_goal_lifecycle_blocked",
                    public_status=409,
                )

            request_payload = {
                "tenant_id": organization.tenant_id,
                "project_id": organization.project_id,
                "organization_id": organization.organization_id,
                "principal_id": principal.principal_id,
                "command": command.model_dump(mode="json"),
            }
            request_digest = canonical_sha256(request_payload)
            existing = uow.operations.get_by_idempotency_key(
                organization.tenant_id,
                organization.project_id,
                _OPERATION_KIND,
                normalized_key,
                for_update=True,
            )
            if existing is not None:
                result = self._replay(
                    uow,
                    operation=existing,
                    organization=organization,
                    request_digest=request_digest,
                )
            else:
                result = self._stage_goal(
                    uow,
                    organization=organization,
                    principal=principal,
                    command=command,
                    idempotency_key=normalized_key,
                    request_digest=request_digest,
                    now=now,
                )
        if result is None:  # pragma: no cover - defensive invariant
            raise OrganizationGoalApplicationError(
                "organization_goal_result_missing",
                public_status=500,
            )
        return result

    def _stage_goal(
        self,
        uow: OrganizationUnitOfWorkPort,
        *,
        organization,
        principal: OrganizationAccessPrincipal,
        command: OrganizationGoalCreateCommand,
        idempotency_key: str,
        request_digest: str,
        now: float,
    ) -> OrganizationGoalCreateResult:
        goal_id = self._stable_id(
            "orggoal",
            organization.tenant_id,
            organization.project_id,
            organization.organization_id,
            idempotency_key,
        )
        if uow.goals.get_scoped(
            organization.tenant_id,
            organization.project_id,
            organization.organization_id,
            goal_id,
            for_update=True,
        ) is not None:
            raise OrganizationGoalApplicationError(
                "organization_goal_identity_conflict",
                public_status=409,
            )
        trace_id = self._stable_id("goal", organization.organization_id, goal_id, length=32)
        goal = GoalDB(
            id=goal_id,
            trace_id=trace_id,
            goal=command.goal,
            summary=command.summary,
            status="received",
            source="organization_planning",
            requested_by=principal.principal_id,
            tenant_id=organization.tenant_id,
            project_id=organization.project_id,
            organization_id=organization.organization_id,
            unit_id=None,
            team_id=None,
            parent_goal_id=None,
            goal_kind="organization",
            constraints=list(command.constraints),
            acceptance_criteria=list(command.acceptance_criteria),
            execution_preferences={
                "organization_id": organization.organization_id,
                "organization_goal_request_digest": request_digest,
                "planning_pipeline": "organization_category_first",
            },
            mode="generic",
            mode_data={
                "goal_kind": "organization",
                "organization_id": organization.organization_id,
            },
            created_at=now,
            updated_at=now,
        )
        operation = OrganizationOperationDB(
            tenant_id=organization.tenant_id,
            project_id=organization.project_id,
            organization_id=organization.organization_id,
            operation_kind=_OPERATION_KIND,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            plan_digest=organization.plan_digest,
            expected_revision=organization.definition_revision,
            status="pending",
        )
        result = self._result(goal, replayed=False)
        uow.goals.add(goal)
        uow.operations.add(operation)
        self._fault_injector("goal_and_operation")
        uow.audit_outbox.add(
            OrganizationAuditOutboxDB(
                tenant_id=organization.tenant_id,
                project_id=organization.project_id,
                organization_id=organization.organization_id,
                event_key=f"organization-goal-created:{goal_id}",
                event_kind="organization.goal_created.v1",
                payload_json={
                    "goal_id": goal_id,
                    "organization_id": organization.organization_id,
                    "principal_id": principal.principal_id,
                    "request_digest": request_digest,
                    "goal_kind": "organization",
                    "status": "received",
                },
            )
        )
        operation.status = "applied"
        operation.result_ref = goal_id
        operation.result_json = result.model_dump(mode="json")
        operation.applied_at = now
        uow.operations.add(operation)
        uow.flush()
        self._fault_injector("audit_outbox")
        return result

    def _replay(
        self,
        uow: OrganizationUnitOfWorkPort,
        *,
        operation,
        organization,
        request_digest: str,
    ) -> OrganizationGoalCreateResult:
        if (
            operation.organization_id != organization.organization_id
            or operation.request_digest != request_digest
        ):
            raise OrganizationGoalApplicationError(
                "organization_goal_idempotency_conflict",
                public_status=409,
            )
        if operation.status != "applied" or not operation.result_ref:
            raise OrganizationGoalApplicationError(
                "organization_goal_creation_in_progress",
                public_status=409,
            )
        goal = uow.goals.get_scoped(
            organization.tenant_id,
            organization.project_id,
            organization.organization_id,
            operation.result_ref,
            for_update=True,
        )
        if (
            goal is None
            or goal.goal_kind != "organization"
            or goal.parent_goal_id is not None
            or dict(goal.execution_preferences or {}).get("organization_goal_request_digest") != request_digest
        ):
            raise OrganizationGoalApplicationError(
                "organization_goal_idempotency_result_missing",
                public_status=409,
            )
        return self._result(goal, replayed=True)

    @staticmethod
    def _result(goal: GoalDB, *, replayed: bool) -> OrganizationGoalCreateResult:
        return OrganizationGoalCreateResult(
            goal_id=goal.id,
            trace_id=goal.trace_id,
            organization_id=str(goal.organization_id or ""),
            status="received",
            goal_kind="organization",
            replayed=replayed,
        )

    @staticmethod
    def _stable_id(prefix: str, *values: str, length: int = 24) -> str:
        digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:length]
        return f"{prefix}-{digest}"


__all__ = ["OrganizationGoalApplicationError", "OrganizationGoalApplicationService"]
