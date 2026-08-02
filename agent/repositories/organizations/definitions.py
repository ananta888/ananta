"""Session-bound definition repository; it never commits its Session."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from agent.db_models.organizations import (
    OrganizationBlueprintRevisionDB,
    OrganizationHandoffDefinitionRevisionDB,
    OrganizationLimitProfileRevisionDB,
    OrganizationPolicyRevisionDB,
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)


class SqlOrganizationDefinitionRepository:
    _SUPPORTED = (
        OrganizationBlueprintRevisionDB,
        OrganizationHandoffDefinitionRevisionDB,
        OrganizationLimitProfileRevisionDB,
        OrganizationPolicyRevisionDB,
        RoleTemplateRevisionDB,
        TeamBlueprintRevisionDB,
        WorkflowDefinitionRevisionDB,
    )

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: Any) -> Any:
        if not isinstance(row, self._SUPPORTED):
            raise TypeError("organization_definition_row_unsupported")
        self._session.add(row)
        return row

    def get_organization_blueprint(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
        *,
        for_update: bool = False,
    ):
        return self._get(
            OrganizationBlueprintRevisionDB,
            tenant_id,
            project_id,
            "definition_key",
            key,
            "version",
            version,
            for_update=for_update,
        )

    def get_team_blueprint(self, tenant_id: str, project_id: str, key: str, version: int):
        return self._get(TeamBlueprintRevisionDB, tenant_id, project_id, "definition_key", key, "version", version)

    def get_role_template(self, tenant_id: str, project_id: str, key: str, version: int):
        return self._get(RoleTemplateRevisionDB, tenant_id, project_id, "definition_key", key, "version", version)

    def get_workflow(self, tenant_id: str, project_id: str, key: str, version: int):
        return self._get(WorkflowDefinitionRevisionDB, tenant_id, project_id, "definition_key", key, "version", version)

    def get_handoff(self, tenant_id: str, project_id: str, key: str, version: int):
        return self._get(
            OrganizationHandoffDefinitionRevisionDB, tenant_id, project_id, "definition_key", key, "version", version
        )

    def get_limit_profile(self, tenant_id: str, project_id: str, key: str, revision: int):
        return self._get(
            OrganizationLimitProfileRevisionDB, tenant_id, project_id, "policy_key", key, "revision", revision
        )

    def get_policy(self, tenant_id: str, project_id: str, key: str, revision: int):
        return self._get(OrganizationPolicyRevisionDB, tenant_id, project_id, "policy_key", key, "revision", revision)

    def list_active_organization_blueprints(self, tenant_id: str, project_id: str):
        statement = (
            select(OrganizationBlueprintRevisionDB)
            .where(OrganizationBlueprintRevisionDB.tenant_id == tenant_id)
            .where(OrganizationBlueprintRevisionDB.project_id == project_id)
            .where(OrganizationBlueprintRevisionDB.lifecycle == "active")
            .order_by(OrganizationBlueprintRevisionDB.definition_key, OrganizationBlueprintRevisionDB.version.desc())
        )
        return list(self._session.exec(statement).all())

    def list_organization_blueprint_revisions(
        self,
        tenant_id: str,
        project_id: str,
        *,
        key: str | None = None,
        for_update: bool = False,
    ):
        statement = (
            select(OrganizationBlueprintRevisionDB)
            .where(OrganizationBlueprintRevisionDB.tenant_id == tenant_id)
            .where(OrganizationBlueprintRevisionDB.project_id == project_id)
            .order_by(
                OrganizationBlueprintRevisionDB.definition_key,
                OrganizationBlueprintRevisionDB.version.desc(),
            )
        )
        if key is not None:
            statement = statement.where(OrganizationBlueprintRevisionDB.definition_key == key)
        if for_update:
            statement = statement.with_for_update()
        return list(self._session.exec(statement).all())

    def _get(
        self,
        model,
        tenant_id,
        project_id,
        key_field,
        key,
        revision_field,
        revision,
        *,
        for_update: bool = False,
    ):
        statement = (
            select(model)
            .where(model.tenant_id == tenant_id)
            .where(model.project_id == project_id)
            .where(getattr(model, key_field) == key)
            .where(getattr(model, revision_field) == revision)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()


__all__ = ["SqlOrganizationDefinitionRepository"]
