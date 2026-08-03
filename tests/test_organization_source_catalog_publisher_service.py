from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from agent.models.organization_source_catalog_models import (
    OrganizationSourceCatalogPublishCommand,
)
from agent.repositories.organization_source_catalog_repository import (
    OrganizationSourceCatalogPersistenceError,
    OrganizationSourceCatalogUniqueRaceError,
    SourceCatalogPublishingAuthority,
)
from agent.services.organization_source_catalog_publisher_service import (
    OrganizationSourceCatalogPublisherError,
    OrganizationSourceCatalogPublisherPrincipal,
    OrganizationSourceCatalogPublisherService,
)
from agent.services.organization_source_catalog_query_adapter import (
    OrganizationSourceCatalogQueryBatch,
)


@dataclass
class _State:
    organizations: list[Any] = field(default_factory=list)
    memberships: list[Any] = field(default_factory=list)
    grants: list[Any] = field(default_factory=list)
    operations: list[Any] = field(default_factory=list)
    tasks: list[Any] = field(default_factory=list)
    audits: list[Any] = field(default_factory=list)
    verified_bindings: list[list[dict[str, Any]]] = field(default_factory=list)
    verification_error: str | None = None


class _ScopedRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def get_scoped(self, tenant_id, project_id, organization_id, *, for_update=False):
        assert for_update is True
        return next(
            (
                row
                for row in self.rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.organization_id == organization_id
            ),
            None,
        )


class _MembershipRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def get_for_principal(
        self,
        tenant_id,
        project_id,
        organization_id,
        principal_id,
        *,
        for_update=False,
    ):
        assert for_update is True
        return next(
            (
                row
                for row in self.rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.organization_id == organization_id
                and row.principal_id == principal_id
            ),
            None,
        )


class _GrantRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def list_for_principal(
        self,
        tenant_id,
        project_id,
        organization_id,
        principal_id,
        *,
        for_update=False,
    ):
        assert for_update is True
        return [
            row
            for row in self.rows
            if row.tenant_id == tenant_id
            and row.project_id == project_id
            and row.organization_id == organization_id
            and row.principal_id == principal_id
        ]


class _OperationRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def get_by_idempotency_key(
        self,
        tenant_id,
        project_id,
        operation_kind,
        idempotency_key,
        *,
        for_update=False,
    ):
        assert for_update is True
        return next(
            (
                row
                for row in self.rows
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and row.operation_kind == operation_kind
                and row.idempotency_key == idempotency_key
            ),
            None,
        )

    def add(self, row):
        if row not in self.rows:
            self.rows.append(row)
        return row


class _AddRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def add(self, row):
        self.rows.append(row)
        return row


class _CatalogRepository:
    def __init__(self, state: _State, authority: SourceCatalogPublishingAuthority) -> None:
        self.state = state
        self.authority = authority

    def resolve_publishing_authority(self, **kwargs):
        assert kwargs["for_update"] is True
        if kwargs["expected_knowledge_index_id"] != self.authority.knowledge_index_id:
            raise OrganizationSourceCatalogPersistenceError(
                "organization_source_catalog_active_index_changed"
            )
        return self.authority

    def task_id_exists(self, task_id):
        return any(row.id == task_id for row in self.state.tasks)

    def add_task(self, task):
        self.state.tasks.append(task)
        return task

    def verify_bound_records(self, *, authority, record_bindings):
        assert authority == self.authority
        if self.state.verification_error:
            raise OrganizationSourceCatalogPersistenceError(
                self.state.verification_error
            )
        self.state.verified_bindings.append(
            [deepcopy(dict(item)) for item in record_bindings]
        )

    def get_task_scoped(self, **kwargs):
        return next(
            (
                row
                for row in self.state.tasks
                if row.id == kwargs["task_id"]
                and row.tenant_id == kwargs["tenant_id"]
                and row.project_id == kwargs["project_id"]
                and row.organization_id == kwargs["organization_id"]
            ),
            None,
        )


class _Uow:
    def __init__(self, state: _State, authority: SourceCatalogPublishingAuthority) -> None:
        self._state = state
        self._authority = authority
        self._working = None

    def __enter__(self):
        self._working = deepcopy(self._state)
        self.instances = _ScopedRepository(self._working.organizations)
        self.memberships = _MembershipRepository(self._working.memberships)
        self.admin_grants = _GrantRepository(self._working.grants)
        self.operations = _OperationRepository(self._working.operations)
        self.audit_outbox = _AddRepository(self._working.audits)
        self.catalogs = _CatalogRepository(self._working, self._authority)
        return self

    def flush(self):
        return None

    def __exit__(self, exc_type, _exc_value, _traceback):
        if exc_type is None:
            self._state.organizations = self._working.organizations
            self._state.memberships = self._working.memberships
            self._state.grants = self._working.grants
            self._state.operations = self._working.operations
            self._state.tasks = self._working.tasks
            self._state.audits = self._working.audits
            self._state.verified_bindings = self._working.verified_bindings


class _UniqueRaceUow(_Uow):
    def __init__(
        self,
        state: _State,
        authority: SourceCatalogPublishingAuthority,
        *,
        conflicting_request: bool = False,
    ) -> None:
        super().__init__(state, authority)
        self._conflicting_request = conflicting_request

    def flush(self):
        winning = deepcopy(self._working)
        if self._conflicting_request:
            winning.operations[0].request_digest = "f" * 64
        self._state.operations = winning.operations
        self._state.tasks = winning.tasks
        self._state.audits = winning.audits
        self._state.verified_bindings = winning.verified_bindings
        raise OrganizationSourceCatalogUniqueRaceError()


class _QueryPort:
    def __init__(self, *, index_id: str = "index-1") -> None:
        self.index_id = index_id
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["query"]
        content = "Grounded HRM evidence" if "HRM" in query else "Planning evidence"
        record_id = "record-hrm" if "HRM" in query else "record-plan"
        path = "docs/hrm.md" if "HRM" in query else "docs/plan.md"
        return OrganizationSourceCatalogQueryBatch(
            knowledge_index_id=self.index_id,
            matches=(
                {
                    "content": content,
                    "source": path,
                    "metadata": {
                        "record_file": "index.jsonl",
                        "record_id": record_id,
                        "repo_relative_path": path,
                        "record_kind": "document",
                        "line_start": 1,
                        "line_end": 4,
                    },
                },
            ),
        )


def _authority() -> SourceCatalogPublishingAuthority:
    return SourceCatalogPublishingAuthority(
        tenant_id="tenant-1",
        project_id="project-1",
        owner_id="operator-1",
        connection_id="connection-1",
        connector_type="registered_workspace",
        sensitivity="internal",
        source_revision_id="revision-1",
        revision_digest="1" * 64,
        source_manifest_digest="2" * 64,
        admission_receipt_id="receipt-1",
        admission_digest="3" * 64,
        knowledge_index_id="index-1",
        index_run_id="run-1",
        index_source_scope="repo_path",
        index_manifest_digest="4" * 64,
        policy_snapshot_digest="5" * 64,
        active_generation=3,
    )


def _state() -> _State:
    common = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "organization_id": "org-1",
    }
    return _State(
        organizations=[
            SimpleNamespace(
                **common,
                lifecycle="active",
                definition_revision="6" * 64,
            )
        ],
        memberships=[
            SimpleNamespace(
                **common,
                principal_id="operator-1",
                membership_kind="organization_admin",
                expires_at=None,
            )
        ],
        grants=[
            SimpleNamespace(
                **common,
                principal_id="operator-1",
                grant_kind="organization_admin",
                expires_at=None,
                revoked_at=None,
            )
        ],
    )


def _principal() -> OrganizationSourceCatalogPublisherPrincipal:
    return OrganizationSourceCatalogPublisherPrincipal(
        subject_id="operator-1",
        tenant_id="tenant-1",
        project_id="project-1",
        roles=frozenset({"admin"}),
        project_role="owner",
    )


def _command() -> OrganizationSourceCatalogPublishCommand:
    return OrganizationSourceCatalogPublishCommand(
        connection_id="connection-1",
        queries=["HRM architecture", "Planning evidence"],
        limit=10,
    )


def _service(
    state: _State,
    query: _QueryPort,
    *,
    fault_injector=None,
    uow_factory=None,
):
    authority = _authority()
    return OrganizationSourceCatalogPublisherService(
        query_port=query,
        uow_factory=uow_factory or (lambda: _Uow(state, authority)),
        clock=lambda: 1234.5,
        fault_injector=fault_injector,
    )


def test_publish_assigns_deterministic_ids_and_persists_only_content_free_projections() -> None:
    state = _state()
    query = _QueryPort()
    result = _service(state, query).publish(
        principal=_principal(),
        organization_id="org-1",
        command=_command(),
        idempotency_key="catalog-publish-key-1",
    )

    assert result.source_count == 2
    assert result.source_scope == "organization:org-1"
    assert len(state.tasks) == len(state.operations) == len(state.audits) == 1
    assert len(state.verified_bindings) == 1
    assert [
        row["source_id"] for row in state.verified_bindings[0]
    ] == ["SRC_0001", "SRC_0002"]
    task = state.tasks[0]
    catalog = task.verification_status["source_catalog"]
    publication = task.verification_status["source_catalog_publication"]
    assert [row["source_id"] for row in catalog["sources"]] == [
        "SRC_0001",
        "SRC_0002",
    ]
    assert all(
        row["source_ref"]["scope"] == "organization:org-1"
        and row["source_ref"]["source_version"] == "1" * 64
        and row["manifest_hash"] == "4" * 64
        for row in catalog["sources"]
    )
    assert publication["knowledge_index_id"] == "index-1"
    assert publication["active_generation"] == 3
    assert [row["source_id"] for row in publication["record_bindings"]] == [
        "SRC_0001",
        "SRC_0002",
    ]
    assert len(task.history) == 1
    assert task.history[0]["event_type"] == "task_ingested"
    assert task.history[0]["actor"] == "operator-1"
    persisted = repr(
        {
            "task": task.model_dump(),
            "operation": state.operations[0].model_dump(),
            "audit": state.audits[0].model_dump(),
            "result": result.model_dump(),
        }
    )
    assert "Grounded HRM evidence" not in persisted
    assert "Planning evidence" not in persisted


def test_exact_replay_skips_query_and_changed_request_conflicts() -> None:
    state = _state()
    query = _QueryPort()
    service = _service(state, query)
    first = service.publish(
        principal=_principal(),
        organization_id="org-1",
        command=_command(),
        idempotency_key="catalog-publish-key-1",
    )
    calls = len(query.calls)
    second = service.publish(
        principal=_principal(),
        organization_id="org-1",
        command=_command(),
        idempotency_key="catalog-publish-key-1",
    )

    assert second.catalog_task_id == first.catalog_task_id
    assert second.replayed is True
    assert len(query.calls) == calls
    with pytest.raises(
        OrganizationSourceCatalogPublisherError,
        match="organization_source_catalog_idempotency_conflict",
    ):
        service.publish(
            principal=_principal(),
            organization_id="org-1",
            command=OrganizationSourceCatalogPublishCommand(
                connection_id="connection-1",
                queries=["changed query"],
            ),
            idempotency_key="catalog-publish-key-1",
        )


def test_active_index_change_and_fault_roll_back_without_task_or_audit() -> None:
    stale_state = _state()
    with pytest.raises(
        OrganizationSourceCatalogPublisherError,
        match="organization_source_catalog_active_index_changed",
    ):
        _service(stale_state, _QueryPort(index_id="index-new")).publish(
            principal=_principal(),
            organization_id="org-1",
            command=_command(),
            idempotency_key="catalog-publish-key-2",
        )
    assert stale_state.tasks == stale_state.operations == stale_state.audits == []

    rollback_state = _state()

    def fail(step: str) -> None:
        if step == "task_and_operation":
            raise RuntimeError("injected_failure")

    with pytest.raises(RuntimeError, match="injected_failure"):
        _service(
            rollback_state,
            _QueryPort(),
            fault_injector=fail,
        ).publish(
            principal=_principal(),
            organization_id="org-1",
            command=_command(),
            idempotency_key="catalog-publish-key-3",
        )
    assert rollback_state.tasks == rollback_state.operations == rollback_state.audits == []


def test_unique_race_rereads_and_replays_the_scoped_winner() -> None:
    state = _state()
    authority = _authority()
    service = _service(
        state,
        _QueryPort(),
        uow_factory=lambda: _UniqueRaceUow(state, authority),
    )

    result = service.publish(
        principal=_principal(),
        organization_id="org-1",
        command=_command(),
        idempotency_key="catalog-publish-race-1",
    )

    assert result.replayed is True
    assert len(state.tasks) == len(state.operations) == len(state.audits) == 1


def test_unique_race_with_changed_payload_remains_a_conflict() -> None:
    state = _state()
    authority = _authority()
    service = _service(
        state,
        _QueryPort(),
        uow_factory=lambda: _UniqueRaceUow(
            state,
            authority,
            conflicting_request=True,
        ),
    )

    with pytest.raises(
        OrganizationSourceCatalogPublisherError,
        match="organization_source_catalog_idempotency_conflict",
    ):
        service.publish(
            principal=_principal(),
            organization_id="org-1",
            command=_command(),
            idempotency_key="catalog-publish-race-2",
        )


def test_unhydratable_query_snapshot_never_commits_a_current_catalog() -> None:
    state = _state()
    state.verification_error = (
        "organization_source_catalog_output_record_mismatch"
    )

    with pytest.raises(
        OrganizationSourceCatalogPublisherError,
        match="organization_source_catalog_output_record_mismatch",
    ):
        _service(state, _QueryPort()).publish(
            principal=_principal(),
            organization_id="org-1",
            command=_command(),
            idempotency_key="catalog-publish-stale-output",
        )

    assert state.tasks == state.operations == state.audits == []
