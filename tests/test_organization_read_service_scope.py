from __future__ import annotations

import base64

import pytest

from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.services.organization_read_service import (
    OrganizationReadError,
    OrganizationReadService,
)


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _SummarySession:
    def __init__(self, rows_by_entity):
        self._rows_by_entity = rows_by_entity
        self.statements = []

    def exec(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        return _Rows(self._rows_by_entity.get(entity, ()))


def _organization(*, tenant_id: str, project_id: str) -> OrganizationInstanceDB:
    return OrganizationInstanceDB(
        organization_id="shared-organization-id",
        tenant_id=tenant_id,
        project_id=project_id,
        name=f"{tenant_id}/{project_id}",
        definition_key="enterprise-scrum",
        definition_version=1,
        definition_revision="d" * 64,
        effective_limit_profile_ref="organization-limits@1",
        effective_limit_profile_revision=1,
        effective_limit_profile_hash="l" * 64,
        composition_mode="standard",
        plan_digest="p" * 64,
        idempotency_key=f"create-{tenant_id}-{project_id}",
    )


def test_summaries_bind_aggregates_to_tenant_project_and_organization() -> None:
    organizations = [
        _organization(tenant_id="tenant-a", project_id="project-a"),
        _organization(tenant_id="tenant-b", project_id="project-b"),
    ]
    units = [
        OrganizationUnitDB(
            id="unit-a-1",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="shared-organization-id",
            unit_key="unit-a-1",
            name="Unit A 1",
            unit_kind="team",
        ),
        OrganizationUnitDB(
            id="unit-a-2",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="shared-organization-id",
            unit_key="unit-a-2",
            name="Unit A 2",
            unit_kind="team",
        ),
        OrganizationUnitDB(
            id="unit-b-1",
            tenant_id="tenant-b",
            project_id="project-b",
            organization_id="shared-organization-id",
            unit_key="unit-b-1",
            name="Unit B 1",
            unit_kind="team",
        ),
    ]
    links = [
        OrganizationTeamLinkDB(
            id="link-a-1",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="shared-organization-id",
            unit_id="unit-a-1",
            team_id="team-a-1",
        ),
        OrganizationTeamLinkDB(
            id="link-b-1",
            tenant_id="tenant-b",
            project_id="project-b",
            organization_id="shared-organization-id",
            unit_id="unit-b-1",
            team_id="team-b-1",
        ),
        OrganizationTeamLinkDB(
            id="link-b-2",
            tenant_id="tenant-b",
            project_id="project-b",
            organization_id="shared-organization-id",
            unit_id="unit-b-2",
            team_id="team-b-2",
        ),
    ]
    snapshots = [
        OrganizationTopologySnapshotDB(
            id="snapshot-a",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="shared-organization-id",
            revision=2,
            definition_revision="d" * 64,
            snapshot_hash="a" * 64,
        ),
        OrganizationTopologySnapshotDB(
            id="snapshot-b",
            tenant_id="tenant-b",
            project_id="project-b",
            organization_id="shared-organization-id",
            revision=3,
            definition_revision="d" * 64,
            snapshot_hash="b" * 64,
        ),
    ]
    session = _SummarySession(
        {
            OrganizationUnitDB: units,
            OrganizationTeamLinkDB: links,
            OrganizationTopologySnapshotDB: snapshots,
        }
    )

    result = OrganizationReadService._summaries(session, organizations)

    assert [(row["tenant_id"], row["unit_count"], row["team_count"], row["snapshot_hash"]) for row in result] == [
        ("tenant-a", 2, 1, "a" * 64),
        ("tenant-b", 1, 2, "b" * 64),
    ]
    assert len(session.statements) == 3
    for statement in session.statements:
        sql = str(statement)
        entity = statement.column_descriptions[0]["entity"]
        assert f"{entity.__tablename__}.tenant_id" in sql
        assert f"{entity.__tablename__}.project_id" in sql
        assert f"{entity.__tablename__}.organization_id" in sql


def test_organization_list_cursor_is_signed_and_principal_scoped() -> None:
    service = OrganizationReadService(
        catalog=object(),
        cursor_secret="organization-read-cursor-secret",
    )
    cursor = service._encode_cursor(
        "organization-a",
        tenant_id="tenant-a",
        project_id="project-a",
        principal_id="principal-a",
    )

    assert (
        service._decode_cursor(
            cursor,
            tenant_id="tenant-a",
            project_id="project-a",
            principal_id="principal-a",
        )
        == "organization-a"
    )
    with pytest.raises(OrganizationReadError, match="organization_cursor_scope_invalid"):
        service._decode_cursor(
            cursor,
            tenant_id="tenant-a",
            project_id="project-a",
            principal_id="principal-b",
        )

    decoded = bytearray(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    decoded[10] ^= 1
    tampered = base64.urlsafe_b64encode(decoded).decode().rstrip("=")
    with pytest.raises(OrganizationReadError, match="organization_cursor_invalid"):
        service._decode_cursor(
            tampered,
            tenant_id="tenant-a",
            project_id="project-a",
            principal_id="principal-a",
        )
