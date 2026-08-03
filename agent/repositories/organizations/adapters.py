"""Domain-facing adapters over scoped Organization definition rows."""

from __future__ import annotations

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    OrganizationLimitProfile,
    TeamBlueprintDefinition,
    VersionedDefinitionRef,
    canonical_definition_sha256,
)


class SqlOrganizationDefinitionCatalogAdapter:
    def __init__(self, repository, *, tenant_id: str, project_id: str) -> None:
        self._repository = repository
        self._tenant_id = tenant_id
        self._project_id = project_id

    def get_organization_blueprint(self, key: str, version: int):
        row = self._repository.get_organization_blueprint(self._tenant_id, self._project_id, key, version)
        return OrganizationBlueprintDefinition.model_validate(row.definition_json) if row else None

    def get_team_blueprint(self, key: str, version: int):
        row = self._repository.get_team_blueprint(self._tenant_id, self._project_id, key, version)
        return TeamBlueprintDefinition.model_validate(row.definition_json) if self._is_active_reference(row) else None

    def has_role_template(self, key: str, version: int) -> bool:
        return self._is_active_reference(
            self._repository.get_role_template(
                self._tenant_id,
                self._project_id,
                key,
                version,
            )
        )

    def has_workflow_definition(self, key: str, version: int) -> bool:
        return self._is_active_reference(
            self._repository.get_workflow(
                self._tenant_id,
                self._project_id,
                key,
                version,
            )
        )

    def get_workflow_definition(self, key: str, version: int):
        row = self._repository.get_workflow(self._tenant_id, self._project_id, key, version)
        if not self._is_active_reference(row):
            return None
        definition_json = getattr(row, "definition_json", None)
        if definition_json:
            return dict(definition_json)
        return {
            "key": row.definition_key,
            "version": row.version,
            "mode": row.mode,
            "default_failure_policy": row.default_failure_policy,
            "steps": list(row.steps_json or []),
            "checks": dict(row.checks_json or {}),
            "required_capabilities": list(row.required_capabilities or []),
        }

    def has_handoff_definition(self, key: str, version: int) -> bool:
        return self._is_active_reference(
            self._repository.get_handoff(
                self._tenant_id,
                self._project_id,
                key,
                version,
            )
        )

    def has_policy(self, portable_ref: str) -> bool:
        try:
            ref = VersionedDefinitionRef.parse(portable_ref)
        except ValueError:
            return False
        return self._is_active_reference(
            self._repository.get_policy(
                self._tenant_id,
                self._project_id,
                ref.key,
                ref.version,
            )
        ) or self._is_active_reference(
            self._repository.get_limit_profile(
                self._tenant_id,
                self._project_id,
                ref.key,
                ref.version,
            )
        )

    def content_hash_for_ref(self, portable_ref: str) -> str | None:
        """Return authoritative content hash for a directly referenced definition."""

        try:
            ref = VersionedDefinitionRef.parse(portable_ref)
        except ValueError:
            return None
        readers = (
            self._repository.get_team_blueprint,
            self._repository.get_role_template,
            self._repository.get_workflow,
            self._repository.get_handoff,
            self._repository.get_policy,
            self._repository.get_limit_profile,
        )
        for reader in readers:
            row = reader(self._tenant_id, self._project_id, ref.key, ref.version)
            if not self._is_active_reference(row):
                continue
            value = getattr(row, "content_hash", None) or getattr(row, "profile_hash", None)
            if value:
                return str(value)
        return None

    @staticmethod
    def _is_active(row) -> bool:
        return row is not None and str(getattr(row, "lifecycle", "")) == "active"

    @classmethod
    def _is_active_reference(cls, row) -> bool:
        if not cls._is_active(row):
            return False
        value = getattr(row, "content_hash", None) or getattr(
            row,
            "profile_hash",
            None,
        )
        if value:
            cls._ensure_hash_matches(row, str(value))
        return True

    @staticmethod
    def _ensure_hash_matches(row, expected: str) -> None:
        definition_json = getattr(row, "definition_json", None)
        if definition_json:
            actual = canonical_definition_sha256(dict(definition_json))
        elif getattr(row, "profile_hash", None):
            actual = OrganizationLimitProfile(
                policy_id=row.policy_key,
                revision=row.revision,
                **dict(row.limits_json or {}),
            ).content_hash()
        else:
            return
        if actual != expected:
            raise ValueError("organization_referenced_definition_hash_mismatch")


class SqlOrganizationLimitProfileAdapter:
    def __init__(self, repository) -> None:
        self._repository = repository

    def resolve_limit_profile(self, *, tenant_id: str, project_id: str, policy_ref: str):
        ref = VersionedDefinitionRef.parse(policy_ref)
        row = self._repository.get_limit_profile(tenant_id, project_id, ref.key, ref.version)
        if row is None or row.lifecycle != "active":
            raise ValueError("organization_limit_profile_not_active")
        profile = OrganizationLimitProfile(
            policy_id=row.policy_key,
            revision=row.revision,
            **dict(row.limits_json or {}),
        )
        if profile.content_hash() != row.profile_hash:
            raise ValueError("organization_limit_profile_hash_mismatch")
        return profile


__all__ = ["SqlOrganizationDefinitionCatalogAdapter", "SqlOrganizationLimitProfileAdapter"]
