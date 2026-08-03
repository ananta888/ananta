"""Strict production loader for portable Organization definition fragments.

The file catalog is a read-only, process-local definition source.  It never
creates Organization instances and deliberately removes test-only fixtures
from the production snapshot.  Project-scoped database definitions may be
layered in front of it through :class:`FileCatalogDefinitionRepositoryAdapter`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlmodel import select

from agent.db_models.teams import TeamBlueprintDB
from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    OrganizationLimitProfile,
    TeamBlueprintDefinition,
    VersionedDefinitionRef,
    canonical_definition_sha256,
)
from agent.services.organization_blueprint_validation_service import (
    OrganizationBlueprintValidationService,
)

ROOT = Path(__file__).resolve().parents[2]


class OrganizationDefinitionCatalogError(RuntimeError):
    def __init__(self, reason_code: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ProductionOrganizationCatalog:
    aggregate: dict[str, Any]
    organization_blueprints: dict[tuple[str, int], OrganizationBlueprintDefinition]
    team_blueprints: dict[tuple[str, int], TeamBlueprintDefinition]
    role_templates: dict[tuple[str, int], dict[str, Any]]
    workflows: dict[tuple[str, int], dict[str, Any]]
    handoffs: dict[tuple[str, int], dict[str, Any]]
    policies: dict[tuple[str, int], dict[str, Any]]
    limit_profiles: dict[tuple[str, int], OrganizationLimitProfile]
    legacy_team_names: dict[tuple[str, int], str]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrganizationDefinitionCatalogError(
            "organization_catalog_json_invalid",
            details={"path": str(path)},
        ) from exc
    if not isinstance(value, dict):
        raise OrganizationDefinitionCatalogError(
            "organization_catalog_document_invalid",
            details={"path": str(path)},
        )
    return value


def _canonical_digest(value: Any, *, prefix: bool = False) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"sha256:{digest}" if prefix else digest


def _validate_instance(
    *,
    instance: Mapping[str, Any],
    schema: Mapping[str, Any],
    label: str,
    registry: Registry | None = None,
) -> None:
    validator = (
        Draft202012Validator(dict(schema), registry=registry)
        if registry is not None
        else Draft202012Validator(dict(schema))
    )
    errors = sorted(
        validator.iter_errors(dict(instance)),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
    raise OrganizationDefinitionCatalogError(
        "organization_catalog_schema_invalid",
        details={"document": label, "path": path, "message": error.message},
    )


def _insert_unique(
    target: dict[tuple[str, int], Any],
    item: Mapping[str, Any],
    *,
    label: str,
    key_field: str = "key",
    version_field: str = "version",
) -> tuple[str, int]:
    key = str(item.get(key_field) or "").strip()
    version = int(item.get(version_field) or 0)
    ref = (key, version)
    if not key or version < 1:
        raise OrganizationDefinitionCatalogError(
            "organization_catalog_reference_invalid",
            details={"section": label, "key": key, "version": version},
        )
    if ref in target:
        raise OrganizationDefinitionCatalogError(
            "organization_catalog_reference_duplicate",
            details={"section": label, "reference": f"{key}@{version}"},
        )
    target[ref] = copy.deepcopy(dict(item))
    return ref


class OrganizationDefinitionCatalogService:
    """Load and expose one immutable production definition snapshot."""

    def __init__(self, *, repository_root: Path | None = None) -> None:
        self._root = (repository_root or ROOT).resolve()
        self._lock = threading.RLock()
        self._snapshot: ProductionOrganizationCatalog | None = None

    def reload(self) -> ProductionOrganizationCatalog:
        snapshot = self._load_snapshot()
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> ProductionOrganizationCatalog:
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._load_snapshot()
            return self._snapshot

    def production_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.snapshot().aggregate)

    def list_organization_blueprints(self) -> list[OrganizationBlueprintDefinition]:
        values = self.snapshot().organization_blueprints
        return [values[key] for key in sorted(values)]

    def get_organization_blueprint(self, key: str, version: int) -> OrganizationBlueprintDefinition | None:
        return self.snapshot().organization_blueprints.get((str(key), int(version)))

    def latest_organization_blueprint(self, key: str) -> OrganizationBlueprintDefinition | None:
        matches = [
            value
            for (candidate, _version), value in self.snapshot().organization_blueprints.items()
            if candidate == str(key)
        ]
        return max(matches, key=lambda value: value.version) if matches else None

    def get_team_blueprint(self, key: str, version: int) -> TeamBlueprintDefinition | None:
        return self.snapshot().team_blueprints.get((str(key), int(version)))

    def get_role_template(self, key: str, version: int) -> dict[str, Any] | None:
        value = self.snapshot().role_templates.get((str(key), int(version)))
        return copy.deepcopy(value) if value is not None else None

    def has_role_template(self, key: str, version: int) -> bool:
        return (str(key), int(version)) in self.snapshot().role_templates

    def has_workflow_definition(self, key: str, version: int) -> bool:
        return (str(key), int(version)) in self.snapshot().workflows

    def get_workflow_definition(self, key: str, version: int) -> dict[str, Any] | None:
        value = self.snapshot().workflows.get((str(key), int(version)))
        return copy.deepcopy(value) if value is not None else None

    def has_handoff_definition(self, key: str, version: int) -> bool:
        return (str(key), int(version)) in self.snapshot().handoffs

    def has_policy(self, portable_ref: str) -> bool:
        try:
            ref = VersionedDefinitionRef.parse(portable_ref)
        except ValueError:
            return False
        key = (ref.key, ref.version)
        return key in self.snapshot().policies or key in self.snapshot().limit_profiles

    def content_hash_for_ref(self, portable_ref: str) -> str | None:
        """Resolve one unambiguous immutable file-catalog reference hash."""

        try:
            ref = VersionedDefinitionRef.parse(portable_ref)
        except ValueError:
            return None
        key = (ref.key, ref.version)
        snapshot = self.snapshot()
        values = [
            collection[key]
            for collection in (
                snapshot.team_blueprints,
                snapshot.role_templates,
                snapshot.workflows,
                snapshot.handoffs,
                snapshot.policies,
                snapshot.limit_profiles,
            )
            if key in collection
        ]
        if not values:
            return None
        if len(values) > 1:
            raise OrganizationDefinitionCatalogError(
                "organization_catalog_reference_ambiguous",
                details={"reference": portable_ref},
            )
        value = values[0]
        if isinstance(value, OrganizationLimitProfile):
            return value.content_hash()
        return canonical_definition_sha256(value)

    def get_policy(self, key: str, version: int) -> dict[str, Any] | None:
        value = self.snapshot().policies.get((str(key), int(version)))
        return copy.deepcopy(value) if value is not None else None

    def resolve_limit_profile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_ref: str,
    ) -> OrganizationLimitProfile:
        if not str(tenant_id or "").strip() or not str(project_id or "").strip():
            raise OrganizationDefinitionCatalogError("organization_limit_scope_required")
        ref = VersionedDefinitionRef.parse(policy_ref)
        profile = self.snapshot().limit_profiles.get((ref.key, ref.version))
        if profile is None:
            raise OrganizationDefinitionCatalogError("organization_limit_profile_not_found")
        return profile

    def legacy_team_name(self, key: str, version: int) -> str | None:
        return self.snapshot().legacy_team_names.get((str(key), int(version)))

    def _load_snapshot(self) -> ProductionOrganizationCatalog:
        schema_root = self._root / "schemas"
        config_root = self._root / "config" / "blueprints" / "standard"
        aggregate_schema = _load_json(schema_root / "blueprints" / "organization_blueprint_catalog.v1.json")
        fragment_schema = _load_json(schema_root / "blueprints" / "organization_blueprint_fragment.v1.json")
        workflow_schema = _load_json(schema_root / "blueprints" / "organization_workflow_catalog.v1.json")
        seed_template_schema = _load_json(schema_root / "blueprints" / "seed_template_catalog.v1.json")
        seed_blueprint_schema = _load_json(schema_root / "blueprints" / "seed_blueprint_catalog.v1.json")
        proposal_policy_schema = _load_json(schema_root / "policies" / "worker_task_proposal_policy.v1.json")
        organization_policy_schema = _load_json(schema_root / "policies" / "organization_policy_catalog.v1.json")
        registry = Registry().with_resources(
            [
                (aggregate_schema["$id"], Resource.from_contents(aggregate_schema)),
                (fragment_schema["$id"], Resource.from_contents(fragment_schema)),
                (workflow_schema["$id"], Resource.from_contents(workflow_schema)),
            ]
        )

        role_items = self._load_role_templates(
            config_root=config_root,
            schema=seed_template_schema,
        )
        team_items, legacy_team_names = self._load_team_blueprints(
            config_root=config_root,
            schema=seed_blueprint_schema,
        )
        organization_items, handoff_items, acceptance_fixtures, source_test_fixtures, limit_items, metadata = (
            self._load_organization_fragments(
                config_root=config_root,
                schema=fragment_schema,
                registry=registry,
            )
        )
        workflow_items = self._load_workflows(
            config_root=config_root,
            schema=workflow_schema,
            registry=registry,
        )
        policy_items, policy_descriptors = self._load_policies(
            config_root=config_root,
            proposal_schema=proposal_policy_schema,
            organization_schema=organization_policy_schema,
        )

        role_fields = set(aggregate_schema["$defs"]["role_template"]["properties"])
        team_fields = set(aggregate_schema["$defs"]["team_blueprint"]["properties"])
        aggregate_roles = [
            {key: copy.deepcopy(value) for key, value in item.items() if key in role_fields} for item in role_items
        ]
        aggregate_teams: list[dict[str, Any]] = []
        for item in team_items:
            mapped = {
                key: copy.deepcopy(value) for key, value in item.items() if key in team_fields and key != "artifacts"
            }
            mapped["artifacts"] = copy.deepcopy(item["artifact_contracts"])
            aggregate_teams.append(mapped)
        source_aggregate = {
            "schema": "organization_blueprint_catalog.v1",
            "metadata": metadata,
            "role_templates": aggregate_roles,
            "team_blueprints": aggregate_teams,
            "organization_blueprints": organization_items,
            "handoff_definitions": handoff_items,
            "acceptance_fixtures": acceptance_fixtures,
            "test_only_fixtures": source_test_fixtures,
            "workflow_definitions": workflow_items,
            "policies": policy_descriptors,
            "limit_profiles": limit_items,
        }
        _validate_instance(
            instance=source_aggregate,
            schema=aggregate_schema,
            label="assembled source organization catalog",
        )
        production_aggregate = copy.deepcopy(source_aggregate)
        production_aggregate["test_only_fixtures"] = []
        _validate_instance(
            instance=production_aggregate,
            schema=aggregate_schema,
            label="sanitized production organization catalog",
        )

        organizations: dict[tuple[str, int], OrganizationBlueprintDefinition] = {}
        teams: dict[tuple[str, int], TeamBlueprintDefinition] = {}
        roles: dict[tuple[str, int], dict[str, Any]] = {}
        workflows: dict[tuple[str, int], dict[str, Any]] = {}
        handoffs: dict[tuple[str, int], dict[str, Any]] = {}
        policies: dict[tuple[str, int], dict[str, Any]] = {}
        limits: dict[tuple[str, int], OrganizationLimitProfile] = {}
        for item in organization_items:
            ref = _insert_unique(organizations, item, label="organization_blueprints")
            organizations[ref] = OrganizationBlueprintDefinition.model_validate(item)
        for item in aggregate_teams:
            ref = _insert_unique(teams, item, label="team_blueprints")
            teams[ref] = TeamBlueprintDefinition.model_validate(item)
        for item in aggregate_roles:
            _insert_unique(roles, item, label="role_templates")
        for item in workflow_items:
            _insert_unique(workflows, item, label="workflow_definitions")
        for item in handoff_items:
            _insert_unique(handoffs, item, label="handoff_definitions")
        for item in policy_items:
            _insert_unique(policies, item, label="policies")
        for item in limit_items:
            ref = _insert_unique(
                limits,
                item,
                label="limit_profiles",
                key_field="policy_id",
                version_field="revision",
            )
            limits[ref] = OrganizationLimitProfile.model_validate(item)

        catalog_view = _CatalogValidationView(
            organizations=organizations,
            teams=teams,
            roles=roles,
            workflows=workflows,
            handoffs=handoffs,
            policies=policies,
            limits=limits,
        )
        validator = OrganizationBlueprintValidationService()
        for definition in organizations.values():
            limit_ref = VersionedDefinitionRef.parse(definition.limit_policy_ref)
            limit_profile = limits.get((limit_ref.key, limit_ref.version))
            if limit_profile is None:
                raise OrganizationDefinitionCatalogError("organization_limit_profile_not_found")
            validator.ensure_valid(definition, catalog=catalog_view, limits=limit_profile)

        return ProductionOrganizationCatalog(
            aggregate=production_aggregate,
            organization_blueprints=organizations,
            team_blueprints=teams,
            role_templates=roles,
            workflows=workflows,
            handoffs=handoffs,
            policies=policies,
            limit_profiles=limits,
            legacy_team_names=legacy_team_names,
        )

    @staticmethod
    def _load_role_templates(*, config_root: Path, schema: Mapping[str, Any]) -> list[dict[str, Any]]:
        base = _load_json(config_root / "templates.json")
        merged = {
            "schema": base.get("schema"),
            "version": base.get("version"),
            "appendixes": copy.deepcopy(dict(base.get("appendixes") or {})),
            "team_types": copy.deepcopy(dict(base.get("team_types") or {})),
            "templates": copy.deepcopy(list(base.get("templates") or [])),
        }
        for path in sorted((config_root / "templates.d").glob("*.json")):
            fragment = _load_json(path)
            merged["appendixes"].update(copy.deepcopy(dict(fragment.get("appendixes") or {})))
            for team_type, value in dict(fragment.get("team_types") or {}).items():
                if team_type in merged["team_types"]:
                    raise OrganizationDefinitionCatalogError(
                        "organization_catalog_team_type_duplicate",
                        details={"team_type": team_type, "path": str(path)},
                    )
                merged["team_types"][team_type] = copy.deepcopy(value)
            merged["templates"].extend(copy.deepcopy(list(fragment.get("templates") or [])))
        _validate_instance(instance=merged, schema=schema, label="merged role template catalog")
        return [dict(item) for item in merged["templates"] if isinstance(item, dict) and item.get("key")]

    @staticmethod
    def _load_team_blueprints(
        *,
        config_root: Path,
        schema: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[tuple[str, int], str]]:
        items: list[dict[str, Any]] = []
        legacy_names: dict[tuple[str, int], str] = {}
        paths = [config_root / "blueprints.json", *sorted((config_root / "blueprints.d").glob("*.json"))]
        for path in paths:
            document = _load_json(path)
            _validate_instance(instance=document, schema=schema, label=str(path))
            for item in list(document.get("blueprints") or []):
                if not isinstance(item, dict) or not item.get("key"):
                    continue
                value = copy.deepcopy(item)
                key = str(value["key"])
                version = int(value["version"])
                ref = (key, version)
                if ref in legacy_names:
                    raise OrganizationDefinitionCatalogError(
                        "organization_catalog_reference_duplicate",
                        details={"section": "team_blueprints", "reference": f"{key}@{version}"},
                    )
                legacy_names[ref] = str(value.get("name") or key)
                items.append(value)
        return items, legacy_names

    @staticmethod
    def _load_organization_fragments(
        *,
        config_root: Path,
        schema: Mapping[str, Any],
        registry: Registry,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        paths = sorted((config_root / "organizations.d").glob("*.json"))
        if not paths:
            raise OrganizationDefinitionCatalogError("organization_catalog_fragment_missing")
        organizations: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = []
        acceptance: list[dict[str, Any]] = []
        test_only: list[dict[str, Any]] = []
        limits: list[dict[str, Any]] = []
        metadata: dict[str, Any] | None = None
        for path in paths:
            document = _load_json(path)
            _validate_instance(instance=document, schema=schema, label=str(path), registry=registry)
            current_metadata = dict(document["metadata"])
            if metadata is None:
                metadata = current_metadata
            elif current_metadata != metadata:
                raise OrganizationDefinitionCatalogError("organization_catalog_metadata_conflict")
            organizations.extend(copy.deepcopy(list(document["organization_blueprints"])))
            handoffs.extend(copy.deepcopy(list(document["handoff_definitions"])))
            acceptance.extend(copy.deepcopy(list(document["acceptance_fixtures"])))
            test_only.extend(copy.deepcopy(list(document["test_only_fixtures"])))
            limits.extend(copy.deepcopy(list(document["limit_profiles"])))
        return organizations, handoffs, acceptance, test_only, limits, dict(metadata or {})

    @staticmethod
    def _load_workflows(
        *,
        config_root: Path,
        schema: Mapping[str, Any],
        registry: Registry,
    ) -> list[dict[str, Any]]:
        workflows: list[dict[str, Any]] = []
        paths = sorted((config_root / "workflows.d").glob("*.json"))
        if not paths:
            raise OrganizationDefinitionCatalogError("organization_workflow_catalog_missing")
        for path in paths:
            document = _load_json(path)
            _validate_instance(instance=document, schema=schema, label=str(path), registry=registry)
            workflows.extend(copy.deepcopy(list(document["workflow_definitions"])))
        return workflows

    @staticmethod
    def _load_policies(
        *,
        config_root: Path,
        proposal_schema: Mapping[str, Any],
        organization_schema: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        items: list[dict[str, Any]] = []
        descriptors: list[dict[str, Any]] = []
        for path in sorted((config_root / "policies.d").glob("*.json")):
            document = _load_json(path)
            schema_name = str(document.get("schema") or "")
            relative_path = str(path.relative_to(config_root.parents[2]))
            if schema_name == "worker_task_proposal_policy.v1":
                _validate_instance(instance=document, schema=proposal_schema, label=str(path))
                item = copy.deepcopy(document)
                items.append(item)
                descriptors.append(
                    {
                        "key": item["key"],
                        "version": item["version"],
                        "policy_type": "task_proposal",
                        "contract_ref": relative_path,
                        "content_digest": _canonical_digest(item, prefix=True),
                    }
                )
            elif schema_name == "organization_policy_catalog.v1":
                _validate_instance(instance=document, schema=organization_schema, label=str(path))
                for raw in document["policies"]:
                    item = copy.deepcopy(raw)
                    items.append(item)
                    descriptors.append(
                        {
                            "key": item["key"],
                            "version": item["version"],
                            "policy_type": item["policy_type"],
                            "contract_ref": f"{relative_path}#{item['key']}@{item['version']}",
                            "content_digest": _canonical_digest(item, prefix=True),
                        }
                    )
            else:
                raise OrganizationDefinitionCatalogError(
                    "organization_policy_schema_unknown",
                    details={"path": str(path), "schema": schema_name},
                )
        return items, descriptors


class _CatalogValidationView:
    def __init__(self, *, organizations, teams, roles, workflows, handoffs, policies, limits) -> None:
        self._organizations = organizations
        self._teams = teams
        self._roles = roles
        self._workflows = workflows
        self._handoffs = handoffs
        self._policies = policies
        self._limits = limits

    def get_organization_blueprint(self, key, version):
        return self._organizations.get((key, version))

    def get_team_blueprint(self, key, version):
        return self._teams.get((key, version))

    def has_role_template(self, key, version):
        return (key, version) in self._roles

    def has_workflow_definition(self, key, version):
        return (key, version) in self._workflows

    def get_workflow_definition(self, key, version):
        return self._workflows.get((key, version))

    def has_handoff_definition(self, key, version):
        return (key, version) in self._handoffs

    def has_policy(self, portable_ref):
        try:
            ref = VersionedDefinitionRef.parse(portable_ref)
        except ValueError:
            return False
        return (ref.key, ref.version) in self._policies or (ref.key, ref.version) in self._limits


class FileCatalogDefinitionRepositoryAdapter:
    """Repository-shaped, scoped DB override plus production-file fallback."""

    def __init__(self, repository: Any, catalog: OrganizationDefinitionCatalogService, session: Any) -> None:
        self._repository = repository
        self._catalog = catalog
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def get_organization_blueprint(self, tenant_id, project_id, key, version):
        row = self._repository.get_organization_blueprint(tenant_id, project_id, key, version)
        if row is not None:
            return row
        definition = self._catalog.get_organization_blueprint(key, version)
        if definition is None:
            return None
        payload = definition.model_dump(mode="json")
        return SimpleNamespace(
            tenant_id=tenant_id,
            project_id=project_id,
            definition_key=key,
            version=version,
            lifecycle="active",
            content_hash=canonical_definition_sha256(payload),
            definition_json=payload,
            limit_policy_ref=definition.limit_policy_ref,
        )

    def get_team_blueprint(self, tenant_id, project_id, key, version):
        row = self._repository.get_team_blueprint(tenant_id, project_id, key, version)
        if row is not None:
            return row
        definition = self._catalog.get_team_blueprint(key, version)
        if definition is None:
            return None
        legacy = self._session.exec(
            select(TeamBlueprintDB).where(
                TeamBlueprintDB.definition_key == key,
                TeamBlueprintDB.definition_version == version,
            )
        ).first()
        if legacy is None:
            legacy_name = self._catalog.legacy_team_name(key, version)
            if legacy_name:
                legacy = self._session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == legacy_name)).first()
        payload = definition.model_dump(mode="json")
        return SimpleNamespace(
            tenant_id=tenant_id,
            project_id=project_id,
            definition_key=key,
            version=version,
            lifecycle="active",
            content_hash=canonical_definition_sha256(payload),
            definition_json=payload,
            legacy_blueprint_id=(legacy.id if legacy is not None else None),
        )

    def get_role_template(self, tenant_id, project_id, key, version):
        row = self._repository.get_role_template(tenant_id, project_id, key, version)
        if row is not None:
            return row
        definition = self._catalog.get_role_template(key, version)
        return self._definition_row(tenant_id, project_id, key, version, definition)

    def get_workflow(self, tenant_id, project_id, key, version):
        row = self._repository.get_workflow(tenant_id, project_id, key, version)
        if row is not None:
            return row
        definition = self._catalog.get_workflow_definition(key, version)
        return self._definition_row(tenant_id, project_id, key, version, definition)

    def get_handoff(self, tenant_id, project_id, key, version):
        row = self._repository.get_handoff(tenant_id, project_id, key, version)
        if row is not None:
            return row
        definition = self._catalog.snapshot().handoffs.get((key, version))
        return self._definition_row(tenant_id, project_id, key, version, definition)

    def get_policy(self, tenant_id, project_id, key, revision):
        row = self._repository.get_policy(tenant_id, project_id, key, revision)
        if row is not None:
            return row
        definition = self._catalog.get_policy(key, revision)
        return self._definition_row(tenant_id, project_id, key, revision, definition, revision=True)

    def get_limit_profile(self, tenant_id, project_id, key, revision):
        row = self._repository.get_limit_profile(tenant_id, project_id, key, revision)
        if row is not None:
            return row
        profile = self._catalog.snapshot().limit_profiles.get((key, revision))
        if profile is None:
            return None
        payload = profile.model_dump(mode="json")
        limits_json = dict(payload)
        limits_json.pop("policy_id", None)
        limits_json.pop("revision", None)
        return SimpleNamespace(
            tenant_id=tenant_id,
            project_id=project_id,
            policy_key=key,
            revision=revision,
            lifecycle="active",
            profile_hash=profile.content_hash(),
            limits_json=limits_json,
        )

    @staticmethod
    def _definition_row(tenant_id, project_id, key, version, definition, *, revision=False):
        if definition is None:
            return None
        payload = copy.deepcopy(dict(definition))
        return SimpleNamespace(
            tenant_id=tenant_id,
            project_id=project_id,
            **({"policy_key": key, "revision": version} if revision else {"definition_key": key, "version": version}),
            lifecycle="active",
            content_hash=canonical_definition_sha256(payload),
            definition_json=payload,
        )


_default_catalog = OrganizationDefinitionCatalogService()


def get_organization_definition_catalog() -> OrganizationDefinitionCatalogService:
    return _default_catalog


__all__ = [
    "FileCatalogDefinitionRepositoryAdapter",
    "OrganizationDefinitionCatalogError",
    "OrganizationDefinitionCatalogService",
    "ProductionOrganizationCatalog",
    "get_organization_definition_catalog",
]
