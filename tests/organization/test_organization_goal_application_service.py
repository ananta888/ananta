from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from agent.models.organization_goal_models import OrganizationGoalCreateCommand
from agent.services.organization_goal_application_service import (
    OrganizationGoalApplicationError,
    OrganizationGoalApplicationService,
)
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)


@dataclass
class _State:
    organizations: list[Any] = field(default_factory=list)
    memberships: list[Any] = field(default_factory=list)
    grants: list[Any] = field(default_factory=list)
    goals: list[Any] = field(default_factory=list)
    operations: list[Any] = field(default_factory=list)
    audit_events: list[Any] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "organizations": len(self.organizations),
            "memberships": len(self.memberships),
            "grants": len(self.grants),
            "goals": len(self.goals),
            "operations": len(self.operations),
            "audit_events": len(self.audit_events),
        }


class _InstanceRepository:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def get_scoped(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        *,
        for_update: bool = False,
    ):
        del for_update
        return next(
            (
                row
                for row in self._rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.organization_id == organization_id
            ),
            None,
        )


class _GoalRepository:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def add(self, row):
        self._rows.append(row)
        return row

    def get_scoped(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        goal_id: str,
        *,
        for_update: bool = False,
    ):
        del for_update
        return next(
            (
                row
                for row in self._rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.organization_id == organization_id
                and row.id == goal_id
            ),
            None,
        )


class _MembershipRepository:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def get_for_principal(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        *,
        for_update: bool = False,
    ):
        assert for_update is True
        return next(
            (
                row
                for row in self._rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.organization_id == organization_id
                and row.principal_id == principal_id
            ),
            None,
        )


class _AdminGrantRepository:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_for_principal(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        *,
        for_update: bool = False,
    ) -> list[Any]:
        assert for_update is True
        return [
            row
            for row in self._rows
            if row.tenant_id == tenant_id
            and row.project_id == project_id
            and row.organization_id == organization_id
            and row.principal_id == principal_id
        ]


class _OperationRepository:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def add(self, row):
        if not any(existing.operation_id == row.operation_id for existing in self._rows):
            self._rows.append(row)
        return row

    def get_by_idempotency_key(
        self,
        tenant_id: str,
        project_id: str,
        operation_kind: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ):
        del for_update
        return next(
            (
                row
                for row in self._rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.operation_kind == operation_kind
                and row.idempotency_key == idempotency_key
            ),
            None,
        )


class _AddRepository:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def add(self, row):
        self._rows.append(row)
        return row


class _TransactionalUow:
    def __init__(self, state: _State) -> None:
        self._state = state
        self._working: _State | None = None

    def __enter__(self):
        self._working = deepcopy(self._state)
        self.instances = _InstanceRepository(self._working.organizations)
        self.memberships = _MembershipRepository(self._working.memberships)
        self.admin_grants = _AdminGrantRepository(self._working.grants)
        self.goals = _GoalRepository(self._working.goals)
        self.operations = _OperationRepository(self._working.operations)
        self.audit_outbox = _AddRepository(self._working.audit_events)
        return self

    def flush(self) -> None:
        return None

    def __exit__(self, exc_type, _exc_value, _traceback) -> None:
        if exc_type is None and self._working is not None:
            self._state.organizations = self._working.organizations
            self._state.memberships = self._working.memberships
            self._state.grants = self._working.grants
            self._state.goals = self._working.goals
            self._state.operations = self._working.operations
            self._state.audit_events = self._working.audit_events


class _Membership:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict[str, Any]] = []

    def mutation_allowed(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return self.allowed


def _state(*, lifecycle: str = "active") -> _State:
    return _State(
        organizations=[
            SimpleNamespace(
                tenant_id="tenant-a",
                project_id="project-a",
                organization_id="organization-a",
                lifecycle=lifecycle,
                plan_digest="a" * 64,
                definition_revision="b" * 64,
            )
        ],
        memberships=[
            SimpleNamespace(
                tenant_id="tenant-a",
                project_id="project-a",
                organization_id="organization-a",
                principal_id="operator-a",
                membership_kind="organization_admin",
                expires_at=None,
            )
        ],
        grants=[
            SimpleNamespace(
                tenant_id="tenant-a",
                project_id="project-a",
                organization_id="organization-a",
                principal_id="operator-a",
                grant_kind="organization_admin",
                expires_at=None,
                revoked_at=None,
            )
        ],
    )


def _principal(*, credential_type: str = "user") -> OrganizationAccessPrincipal:
    return OrganizationAccessPrincipal(
        principal_id="operator-a",
        tenant_id="tenant-a",
        project_id="project-a",
        credential_type=credential_type,
    )


def _command(*, goal: str = "Research the organization") -> OrganizationGoalCreateCommand:
    return OrganizationGoalCreateCommand(
        goal=goal,
        summary="Create a grounded Category plan",
        constraints=["Use only assignment-bound evidence"],
        acceptance_criteria=["Conforms to todo.schema.json"],
    )


def _service(
    state: _State,
    *,
    membership: Any | None = None,
    fault_injector=None,
) -> OrganizationGoalApplicationService:
    return OrganizationGoalApplicationService(
        membership_service=membership or OrganizationMembershipService(),
        uow_factory=lambda: _TransactionalUow(state),
        clock=lambda: 1234.5,
        fault_injector=fault_injector,
    )


def test_create_and_replay_persist_one_passive_scoped_root_goal() -> None:
    state = _state()
    membership = _Membership()
    service = _service(state, membership=membership)

    created = service.create(
        principal=_principal(),
        organization_id="organization-a",
        command=_command(),
        idempotency_key="goal-create-key-1",
    )
    replayed = service.create(
        principal=_principal(),
        organization_id="organization-a",
        command=_command(),
        idempotency_key="goal-create-key-1",
    )

    assert created.replayed is False
    assert replayed == created.model_copy(update={"replayed": True})
    assert state.counts() == {
        "organizations": 1,
        "memberships": 1,
        "grants": 1,
        "goals": 1,
        "operations": 1,
        "audit_events": 1,
    }
    goal = state.goals[0]
    assert (goal.tenant_id, goal.project_id, goal.organization_id) == (
        "tenant-a",
        "project-a",
        "organization-a",
    )
    assert (goal.goal_kind, goal.status, goal.source) == (
        "organization",
        "received",
        "organization_planning",
    )
    assert goal.requested_by == "operator-a"
    assert (goal.parent_goal_id, goal.unit_id, goal.team_id) == (None, None, None)
    assert goal.execution_preferences["planning_pipeline"] == "organization_category_first"
    assert state.operations[0].status == "applied"
    assert state.operations[0].result_ref == created.goal_id
    assert state.audit_events[0].event_kind == "organization.goal_created.v1"
    assert "goal" not in state.audit_events[0].payload_json
    assert all(call["grant_kind"] == "planning:goal_create" for call in membership.calls)
    assert all(call["membership"].principal_id == "operator-a" for call in membership.calls)
    assert not hasattr(_TransactionalUow(state), "tasks")


def test_reusing_idempotency_key_for_other_intent_fails_without_writes() -> None:
    state = _state()
    service = _service(state)
    service.create(
        principal=_principal(),
        organization_id="organization-a",
        command=_command(),
        idempotency_key="goal-create-key-1",
    )
    before = state.counts()

    with pytest.raises(OrganizationGoalApplicationError) as caught:
        service.create(
            principal=_principal(),
            organization_id="organization-a",
            command=_command(goal="Different intent"),
            idempotency_key="goal-create-key-1",
        )

    assert caught.value.reason_code == "organization_goal_idempotency_conflict"
    assert caught.value.public_status == 409
    assert state.counts() == before


@pytest.mark.parametrize("fault_step", ("goal_and_operation", "audit_outbox"))
def test_failure_inside_goal_transaction_rolls_back_all_new_rows(fault_step: str) -> None:
    state = _state()

    def fail(step: str) -> None:
        if step == fault_step:
            raise RuntimeError(f"injected:{step}")

    with pytest.raises(RuntimeError, match=fault_step):
        _service(state, fault_injector=fail).create(
            principal=_principal(),
            organization_id="organization-a",
            command=_command(),
            idempotency_key="goal-create-key-1",
        )

    assert state.counts() == {
        "organizations": 1,
        "memberships": 1,
        "grants": 1,
        "goals": 0,
        "operations": 0,
        "audit_events": 0,
    }


def test_worker_credential_is_rejected_before_opening_a_transaction() -> None:
    state = _state()
    opened = 0

    def uow_factory():
        nonlocal opened
        opened += 1
        return _TransactionalUow(state)

    service = OrganizationGoalApplicationService(
        membership_service=_Membership(),
        uow_factory=uow_factory,
    )
    with pytest.raises(OrganizationGoalApplicationError) as caught:
        service.create(
            principal=_principal(credential_type="worker"),
            organization_id="organization-a",
            command=_command(),
            idempotency_key="goal-create-key-1",
        )

    assert (caught.value.reason_code, caught.value.public_status) == (
        "organization_goal_credential_forbidden",
        403,
    )
    assert opened == 0


@pytest.mark.parametrize("credential_type", ("service", "hub_service"))
def test_bound_non_worker_service_credentials_can_create_goal(credential_type: str) -> None:
    state = _state()

    result = _service(state).create(
        principal=_principal(credential_type=credential_type),
        organization_id="organization-a",
        command=_command(),
        idempotency_key=f"goal-{credential_type}-key",
    )

    assert result.replayed is False
    assert state.goals[0].requested_by == "operator-a"


def test_missing_mutation_grant_is_non_enumerable() -> None:
    state = _state()
    state.grants = []

    with pytest.raises(OrganizationGoalApplicationError) as caught:
        _service(state).create(
            principal=_principal(),
            organization_id="organization-a",
            command=_command(),
            idempotency_key="goal-create-key-1",
        )

    assert (caught.value.reason_code, caught.value.public_status) == (
        "organization_goal_not_found",
        404,
    )
    assert state.counts()["goals"] == 0


@pytest.mark.parametrize("lifecycle", ("draft", "paused", "completed", "archived"))
def test_non_plannable_organization_lifecycle_fails_closed(lifecycle: str) -> None:
    state = _state(lifecycle=lifecycle)

    with pytest.raises(OrganizationGoalApplicationError) as caught:
        _service(state).create(
            principal=_principal(),
            organization_id="organization-a",
            command=_command(),
            idempotency_key="goal-create-key-1",
        )

    assert (caught.value.reason_code, caught.value.public_status) == (
        "organization_goal_lifecycle_blocked",
        409,
    )


def test_goal_command_is_closed_and_rejects_ambiguous_text() -> None:
    with pytest.raises(ValidationError):
        OrganizationGoalCreateCommand.model_validate(
            {"goal": "Research", "team_id": "caller-selected-team"}
        )
    with pytest.raises(ValidationError):
        OrganizationGoalCreateCommand.model_validate({"goal": "Research", "summary": 42})
    with pytest.raises(ValidationError):
        OrganizationGoalCreateCommand.model_validate(
            {"goal": "Research", "constraints": ["same", "same"]}
        )
