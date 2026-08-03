from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.models.organization_models import OrganizationCompileRequest, canonical_definition_sha256
from agent.services.organization_blueprint_instantiation_service import (
    OrganizationBlueprintInstantiationService,
    OrganizationInstantiationError,
)
from tests.organization_support import FakeDefinitionCatalog, FakeLimitProfiles, organization_compiler

_REPOSITORIES = (
    "operations",
    "instances",
    "units",
    "teams",
    "team_links",
    "role_slots",
    "assignments",
    "relations",
    "memberships",
    "admin_grants",
    "snapshots",
    "audit_outbox",
)

_KEY_FIELDS = {
    "operations": "operation_id",
    "instances": "organization_id",
    "units": "id",
    "teams": "id",
    "team_links": "id",
    "role_slots": "id",
    "assignments": "id",
    "relations": "id",
    "memberships": "membership_id",
    "admin_grants": "grant_id",
    "snapshots": "id",
    "audit_outbox": "event_id",
}


class TransactionState:
    def __init__(self) -> None:
        self.rows = {name: [] for name in _REPOSITORIES}

    def counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.rows.items()}


class _Repository:
    def __init__(self, rows: list, key_field: str) -> None:
        self._rows = rows
        self._key_field = key_field

    def add(self, row):
        identity = getattr(row, self._key_field)
        for index, existing in enumerate(self._rows):
            if getattr(existing, self._key_field) == identity:
                self._rows[index] = row
                return row
        self._rows.append(row)
        return row

    def add_many(self, rows):
        return [self.add(row) for row in rows]

    def list_for_organization(self, tenant_id: str, project_id: str, organization_id: str):
        return [
            row
            for row in self._rows
            if row.tenant_id == tenant_id and row.project_id == project_id and row.organization_id == organization_id
        ]


class _OperationRepository(_Repository):
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


class _InstanceRepository(_Repository):
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


class _SnapshotRepository(_Repository):
    def latest(self, tenant_id: str, project_id: str, organization_id: str):
        matching = self.list_for_organization(tenant_id, project_id, organization_id)
        return max(matching, key=lambda row: row.revision, default=None)


class _DefinitionRepository:
    def __init__(self, plan, catalog: FakeDefinitionCatalog) -> None:
        self._plan = plan
        self._catalog = catalog

    def get_organization_blueprint(self, tenant_id, project_id, key, version):
        if (tenant_id, project_id, key, version) != (
            self._plan.tenant_id,
            self._plan.project_id,
            "enterprise_scrum_organization",
            1,
        ):
            return None
        return SimpleNamespace(content_hash=self._plan.definition_revision)

    def get_team_blueprint(self, tenant_id, project_id, key, version):
        if tenant_id != self._plan.tenant_id or project_id != self._plan.project_id or version != 1:
            return None
        definition = self._catalog.team_blueprints.get(key)
        if definition is None:
            return None
        return SimpleNamespace(
            legacy_blueprint_id=None,
            content_hash=canonical_definition_sha256(definition),
        )


class TransactionalOrganizationUow:
    def __init__(self, state: TransactionState, plan, catalog: FakeDefinitionCatalog) -> None:
        self._state = state
        self._plan = plan
        self._catalog = catalog
        self._working: dict[str, list] | None = None

    def __enter__(self):
        self._working = deepcopy(self._state.rows)
        for name in _REPOSITORIES:
            repository_type = {
                "operations": _OperationRepository,
                "instances": _InstanceRepository,
                "snapshots": _SnapshotRepository,
            }.get(name, _Repository)
            setattr(self, name, repository_type(self._working[name], _KEY_FIELDS[name]))
        self.definitions = _DefinitionRepository(self._plan, self._catalog)
        return self

    def flush(self) -> None:
        return None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_value, traceback
        if exc_type is None and self._working is not None:
            self._state.rows = self._working


def _compiled_plan():
    return organization_compiler().compile(
        OrganizationCompileRequest(
            tenant_id="tenant-atomic",
            project_id="project-atomic",
            organization_id="organization-atomic",
            definition_ref="enterprise_scrum_organization@1",
            composition_mode="standard",
            team_count=8,
        )
    )


def _service(state: TransactionState, plan, *, fail_at: str | None = None):
    catalog = FakeDefinitionCatalog()

    def inject(step: str) -> None:
        if step == fail_at:
            raise RuntimeError(f"fault-injected:{step}")

    return OrganizationBlueprintInstantiationService(
        limit_profiles=FakeLimitProfiles(),
        uow_factory=lambda: TransactionalOrganizationUow(state, plan, catalog),
        fault_injector=inject,
    )


def _instantiate(service, plan, *, authorization_ref: str | None = None):
    return service.instantiate(
        plan=plan,
        name="Atomic Organization",
        idempotency_key="instantiate-atomic-one",
        expected_definition_revision=plan.definition_revision,
        expected_plan_digest=plan.plan_digest,
        principal_id="organization-operator",
        authorization_ref=authorization_ref,
    )


@pytest.mark.parametrize(
    "fault_step",
    (
        "operation",
        "organization",
        "organization_access",
        "units",
        "teams",
        "role_slots",
        "relations",
        "snapshot",
        "audit_outbox",
    ),
)
def test_fault_after_each_aggregate_write_rolls_back_every_row(fault_step: str) -> None:
    plan = _compiled_plan()
    state = TransactionState()

    with pytest.raises(RuntimeError, match=f"fault-injected:{fault_step}"):
        _instantiate(_service(state, plan, fail_at=fault_step), plan)

    assert state.counts() == {name: 0 for name in _REPOSITORIES}


def test_successful_replay_reuses_one_complete_aggregate_without_duplicates() -> None:
    plan = _compiled_plan()
    state = TransactionState()
    service = _service(state, plan)

    created = _instantiate(service, plan)
    counts_after_create = state.counts()
    replayed = _instantiate(service, plan)

    assert created.idempotent_replay is False
    assert replayed.idempotent_replay is True
    assert replayed.organization_id == created.organization_id
    assert state.counts() == counts_after_create
    assert counts_after_create == {
        "operations": 1,
        "instances": 1,
        "units": len(plan.units),
        "teams": plan.requested_team_count,
        "team_links": plan.requested_team_count,
        "role_slots": len(plan.role_slots),
        "assignments": 0,
        "relations": len(plan.relations),
        "memberships": 1,
        "admin_grants": 1,
        "snapshots": 1,
        "audit_outbox": 1,
    }


def test_completed_instantiation_can_be_recovered_without_a_compiled_plan() -> None:
    plan = _compiled_plan()
    state = TransactionState()
    service = _service(state, plan)
    created = _instantiate(service, plan)

    recovered = service.recover_applied_instantiation(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        plan_digest=plan.plan_digest,
        name="Atomic Organization",
        idempotency_key="instantiate-atomic-one",
        principal_id="organization-operator",
        expected_organization_id=plan.organization_id,
        expected_definition_revision=plan.definition_revision,
    )

    assert recovered is not None
    assert recovered.idempotent_replay is True
    assert recovered.organization_id == created.organization_id


@pytest.mark.parametrize(
    "changed_binding",
    (
        {"plan_digest": "f" * 64},
        {"name": "Different Organization"},
        {"principal_id": "different-operator"},
    ),
)
def test_precompile_recovery_rejects_any_changed_request_binding(
    changed_binding: dict[str, str],
) -> None:
    plan = _compiled_plan()
    state = TransactionState()
    service = _service(state, plan)
    _instantiate(service, plan)
    binding = {
        "tenant_id": plan.tenant_id,
        "project_id": plan.project_id,
        "plan_digest": plan.plan_digest,
        "name": "Atomic Organization",
        "idempotency_key": "instantiate-atomic-one",
        "principal_id": "organization-operator",
        "expected_organization_id": plan.organization_id,
        "expected_definition_revision": plan.definition_revision,
    }
    binding.update(changed_binding)

    with pytest.raises(OrganizationInstantiationError) as exc:
        service.recover_applied_instantiation(**binding)

    assert exc.value.reason_code == "organization_idempotency_key_conflict"


def test_precompile_recovery_fails_closed_for_unfinished_operation() -> None:
    plan = _compiled_plan()
    state = TransactionState()
    service = _service(state, plan)
    _instantiate(service, plan)
    state.rows["operations"][0].status = "pending"

    with pytest.raises(OrganizationInstantiationError) as exc:
        service.recover_applied_instantiation(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            plan_digest=plan.plan_digest,
            name="Atomic Organization",
            idempotency_key="instantiate-atomic-one",
            principal_id="organization-operator",
            expected_organization_id=plan.organization_id,
            expected_definition_revision=plan.definition_revision,
        )

    assert exc.value.reason_code == "organization_instantiation_in_progress"


def test_precompile_recovery_returns_none_when_operation_is_missing() -> None:
    plan = _compiled_plan()
    state = TransactionState()

    recovered = _service(state, plan).recover_applied_instantiation(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        plan_digest=plan.plan_digest,
        name="Atomic Organization",
        idempotency_key="instantiate-atomic-one",
        principal_id="organization-operator",
        expected_organization_id=plan.organization_id,
        expected_definition_revision=plan.definition_revision,
    )

    assert recovered is None
    assert state.counts() == {name: 0 for name in _REPOSITORIES}


def test_receipt_binds_the_consumed_precreation_grant_and_stabilizes_replay() -> None:
    plan = _compiled_plan()
    state = TransactionState()
    service = _service(state, plan)
    created = _instantiate(service, plan, authorization_ref="precreation-grant-1")
    state.rows["snapshots"][0].snapshot_hash = "changed-after-instantiation"

    recovered = service.recover_applied_instantiation(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        plan_digest=plan.plan_digest,
        name="Atomic Organization",
        idempotency_key="instantiate-atomic-one",
        principal_id="organization-operator",
        expected_organization_id=plan.organization_id,
        expected_definition_revision=plan.definition_revision,
        authorization_ref="precreation-grant-1",
    )

    assert recovered is not None
    assert recovered.topology_snapshot_hash == created.topology_snapshot_hash
    assert state.rows["operations"][0].result_json["schema"] == "organization_instantiation_receipt.v1"

    with pytest.raises(OrganizationInstantiationError) as exc:
        service.recover_applied_instantiation(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            plan_digest=plan.plan_digest,
            name="Atomic Organization",
            idempotency_key="instantiate-atomic-one",
            principal_id="organization-operator",
            expected_organization_id=plan.organization_id,
            expected_definition_revision=plan.definition_revision,
            authorization_ref="different-precreation-grant",
        )

    assert exc.value.reason_code == "organization_idempotency_admin_grant_conflict"


def test_stale_plan_digest_fails_before_opening_the_transaction() -> None:
    plan = _compiled_plan()
    opened = 0

    def uow_factory():
        nonlocal opened
        opened += 1
        return TransactionalOrganizationUow(TransactionState(), plan, FakeDefinitionCatalog())

    service = OrganizationBlueprintInstantiationService(
        limit_profiles=FakeLimitProfiles(),
        uow_factory=uow_factory,
    )
    stale = plan.model_copy(update={"plan_digest": "0" * 64})

    with pytest.raises(OrganizationInstantiationError) as exc:
        _instantiate(service, stale)

    assert exc.value.reason_code == "organization_plan_digest_stale"
    assert opened == 0
