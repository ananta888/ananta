from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.organizations import (
    OrganizationAdminGrantDB,
    OrganizationAuditOutboxDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationOperationDB,
    OrganizationRelationDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.projects import ProjectDB
from agent.db_models.teams import TeamBlueprintDB, TeamDB, TeamTypeDB
from agent.models.organization_models import OrganizationCompileRequest, canonical_definition_sha256
from agent.services.organization_blueprint_instantiation_service import (
    OrganizationBlueprintInstantiationService,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork
from tests.organization_support import FakeDefinitionCatalog, FakeLimitProfiles, organization_compiler

_PERSISTED_MODELS = (
    OrganizationOperationDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationAdminGrantDB,
    OrganizationUnitDB,
    TeamDB,
    OrganizationTeamLinkDB,
    OrganizationRoleSlotDB,
    OrganizationRelationDB,
    OrganizationTopologySnapshotDB,
    OrganizationAuditOutboxDB,
)


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


class _ForeignKeyOrganizationUow(OrganizationUnitOfWork):
    def __init__(self, *, database, plan, catalog: FakeDefinitionCatalog) -> None:
        super().__init__(session_factory=lambda: Session(database))
        self._plan = plan
        self._catalog = catalog

    def __enter__(self):
        super().__enter__()
        self.definitions = _DefinitionRepository(self._plan, self._catalog)
        return self


def _compiled_plan():
    return organization_compiler().compile(
        OrganizationCompileRequest(
            tenant_id="tenant-foreign-key",
            project_id="project-foreign-key",
            organization_id="organization-foreign-key",
            definition_ref="enterprise_scrum_organization@1",
            composition_mode="standard",
            team_count=8,
        )
    )


def _database(plan):
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    event.listen(database, "connect", enable_foreign_keys)
    SQLModel.metadata.create_all(
        database,
        tables=[
            TeamTypeDB.__table__,
            TeamBlueprintDB.__table__,
            TeamDB.__table__,
            ProjectDB.__table__,
            *[model.__table__ for model in _PERSISTED_MODELS if model is not TeamDB],
        ],
    )
    with Session(database) as session:
        session.add(
            ProjectDB(
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                name="Foreign-key test project",
                created_by_subject_id="organization-operator",
            )
        )
        session.commit()
    return database


def _service(database, plan, *, fail_at: str | None = None):
    catalog = FakeDefinitionCatalog()

    def inject(step: str) -> None:
        if step == fail_at:
            raise RuntimeError(f"fault-injected:{step}")

    return OrganizationBlueprintInstantiationService(
        limit_profiles=FakeLimitProfiles(),
        uow_factory=lambda: _ForeignKeyOrganizationUow(
            database=database,
            plan=plan,
            catalog=catalog,
        ),
        fault_injector=inject,
    )


def _instantiate(service, plan):
    return service.instantiate(
        plan=plan,
        name="Foreign-key Organization",
        idempotency_key="instantiate-foreign-key-one",
        expected_definition_revision=plan.definition_revision,
        expected_plan_digest=plan.plan_digest,
        principal_id="organization-operator",
    )


def test_instantiation_persists_fk_parents_before_children() -> None:
    plan = _compiled_plan()
    database = _database(plan)

    result = _instantiate(_service(database, plan), plan)

    assert result.organization_id == plan.organization_id
    with Session(database) as session:
        operation = session.exec(select(OrganizationOperationDB)).one()
        assert operation.status == "applied"
        assert operation.organization_id == plan.organization_id
        assert len(session.exec(select(TeamDB)).all()) == plan.requested_team_count
        assert len(session.exec(select(OrganizationTeamLinkDB)).all()) == plan.requested_team_count
        assert len(session.exec(select(OrganizationAdminGrantDB)).all()) == 1


@pytest.mark.parametrize("fault_step", ("operation", "organization", "teams"))
def test_intermediate_fk_flushes_remain_atomic_on_fault(fault_step: str) -> None:
    plan = _compiled_plan()
    database = _database(plan)

    with pytest.raises(RuntimeError, match=f"fault-injected:{fault_step}"):
        _instantiate(_service(database, plan, fail_at=fault_step), plan)

    with Session(database) as session:
        assert all(not session.exec(select(model)).all() for model in _PERSISTED_MODELS)
