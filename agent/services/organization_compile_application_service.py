"""Application-layer composition around the pure Organization compiler."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from agent.models.organization_models import (
    CompiledOrganizationPlan,
    OrganizationCompileRequest,
    VersionedDefinitionRef,
    canonical_definition_sha256,
)
from agent.repositories.organizations.adapters import (
    SqlOrganizationDefinitionCatalogAdapter,
    SqlOrganizationLimitProfileAdapter,
)
from agent.services.organization_blueprint_compiler import (
    OrganizationBlueprintCompiler,
    OrganizationCompilationError,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork


class OrganizationCompileBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class DenyOrganizationAdmissionPolicy:
    """Fail closed until a scoped admission-grant adapter is configured."""

    def validate_exception(self, **_kwargs) -> tuple[bool, str | None]:
        return False, "organization_admission_exception_not_configured"


class OrganizationCompileApplicationService:
    _TOKEN_SALT = "ananta.organization.compile-plan.v1"

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        admission_policy: Any | None,
        signing_secret: str,
        token_ttl_seconds: int = 900,
        session_factory=None,
    ) -> None:
        secret = str(signing_secret or "")
        if len(secret.encode("utf-8")) < 16:
            raise OrganizationCompileBindingError("organization_compile_signing_secret_invalid")
        self._catalog = catalog
        self._admission = admission_policy or DenyOrganizationAdmissionPolicy()
        self._signer = URLSafeTimedSerializer(secret, salt=self._TOKEN_SALT)
        self._ttl = max(60, min(int(token_ttl_seconds), 3600))
        self._session_factory = session_factory

    def list_blueprint_summaries(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        with self._scoped_ports(tenant_id, project_id) as (definitions, limits_port, repository):
            scoped_definitions = self._active_definitions(
                definitions=definitions,
                repository=repository,
                tenant_id=tenant_id,
                project_id=project_id,
            )
            for definition in scoped_definitions:
                revision = canonical_definition_sha256(definition)
                standard = definition.standard_composition
                limits = limits_port.resolve_limit_profile(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    policy_ref=definition.limit_policy_ref,
                )
                custom_blueprints = self._custom_blueprint_options(
                    definition,
                    limits.max_team_instances_per_organization,
                )
                supported_team_counts = list(range(standard.minimum, standard.maximum + 1))
                for team_count in range(standard.minimum, standard.maximum + 1):
                    summary_plan = self._compiler(
                        definitions=definitions,
                        limit_profiles=limits_port,
                    ).compile(
                        OrganizationCompileRequest(
                            tenant_id=tenant_id,
                            project_id=project_id,
                            organization_id=f"summary-{definition.key}-{team_count}",
                            definition_ref=f"{definition.key}@{definition.version}",
                            composition_mode="standard",
                            team_count=team_count,
                        )
                    )
                    profile = definition.profile
                    summaries.append(
                        {
                            "key": self.selector(definition.key, team_count),
                            "definition_key": definition.key,
                            "version": str(definition.version),
                            "title": self._title(definition.key, team_count),
                            "description": definition.description,
                            "team_count": team_count,
                            "standard": True,
                            "recommended": team_count == standard.default,
                            "test_only": False,
                            "activation_summary": self._activation_summary(definition, team_count),
                            "capabilities": self._capability_summary(
                                definition,
                                team_count,
                                definitions=definitions,
                            ),
                            "revision": revision,
                            "profile_family": profile.family if profile is not None else definition.key,
                            "profile_label": (
                                profile.label if profile is not None else definition.key.replace("_", " ").title()
                            ),
                            "size_label": profile.size_label(team_count) if profile is not None else None,
                            "role_slot_count": len(summary_plan.role_slots),
                            "default_assignment_capacity": int(
                                summary_plan.expected_counts.get("assignment_capacity_default", 0)
                            ),
                            "supported_team_counts": supported_team_counts,
                            "supported_team_count_min": standard.minimum,
                            "supported_team_count_default": standard.default,
                            "supported_team_count_max": standard.maximum,
                            "custom_team_count_min": 2,
                            "custom_team_count_max": limits.max_team_instances_per_organization,
                            "custom_team_blueprints": custom_blueprints,
                        }
                    )
        return summaries

    def compile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
        path_blueprint_key: str,
    ) -> tuple[CompiledOrganizationPlan, dict[str, Any]]:
        selector_key, selector_count = self.parse_selector(path_blueprint_key)
        body_key, body_count = self.parse_selector(str(payload.get("blueprint_key") or path_blueprint_key))
        if selector_key != body_key or (selector_count and body_count and selector_count != body_count):
            raise OrganizationCompileBindingError("organization_blueprint_selector_mismatch")
        version = self._version(payload.get("blueprint_version"))
        composition_mode, team_count, custom_composition = self._composition(
            payload,
            selector_count=selector_count or body_count,
        )
        organization_id = str(uuid.uuid4())
        with self._scoped_ports(tenant_id, project_id) as (definitions, limits_port, repository):
            definition = self._resolve_active_definition(
                definitions=definitions,
                repository=repository,
                tenant_id=tenant_id,
                project_id=project_id,
                key=selector_key,
                version=version,
            )
            if definition is None:
                raise OrganizationCompilationError(
                    "ORGANIZATION_BLUEPRINT_NOT_FOUND",
                    path="$.blueprint_key",
                )
            request = OrganizationCompileRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
                organization_id=organization_id,
                definition_ref=f"{definition.key}@{definition.version}",
                composition_mode=composition_mode,
                team_count=team_count,
                custom_composition=custom_composition,
                admission_exception_ref=(str(payload.get("admission_exception_ref") or "").strip() or None),
            )
            plan = self._compiler(definitions=definitions, limit_profiles=limits_port).compile(request)
        admin_policy_hash = plan.effective_limit_profile_hash
        title = str(payload.get("title") or "").strip()
        if not title or len(title) > 255:
            raise OrganizationCompileBindingError("organization_title_invalid")
        token_payload = {
            "schema": "organization_compile_token.v1",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "principal_id": principal_id,
            "organization_id": organization_id,
            "definition_ref": plan.definition_ref,
            "definition_revision": plan.definition_revision,
            "composition_mode": composition_mode,
            "team_count": team_count,
            "custom_composition": custom_composition,
            "admission_exception_ref": request.admission_exception_ref,
            "plan_digest": plan.plan_digest,
            "admin_policy_hash": admin_policy_hash,
            "title": title,
        }
        response = self._response(
            plan,
            compile_token=self._signer.dumps(token_payload),
            expires_at=time.time() + self._ttl,
            admin_policy_hash=admin_policy_hash,
            admission_exception_ref=request.admission_exception_ref,
            title=title,
        )
        return plan, response

    def recompile_bound_plan(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        client_plan: Mapping[str, Any],
    ) -> tuple[CompiledOrganizationPlan, dict[str, Any]]:
        bound = self._decode_bound_plan(
            tenant_id=tenant_id,
            project_id=project_id,
            principal_id=principal_id,
            client_plan=client_plan,
            enforce_ttl=True,
        )
        definition_ref = VersionedDefinitionRef.parse(str(bound.get("definition_ref") or ""))
        request = OrganizationCompileRequest(
            tenant_id=tenant_id,
            project_id=project_id,
            principal_id=principal_id,
            organization_id=str(bound.get("organization_id") or ""),
            definition_ref=definition_ref.portable_ref(),
            composition_mode=str(bound.get("composition_mode") or ""),
            team_count=bound.get("team_count"),
            custom_composition=bound.get("custom_composition"),
            admission_exception_ref=bound.get("admission_exception_ref"),
        )
        with self._scoped_ports(tenant_id, project_id) as (definitions, limits_port, _repository):
            current = self._compiler(definitions=definitions, limit_profiles=limits_port).compile(request)
        checks = {
            "organization_id": current.organization_id,
            "definition_revision": current.definition_revision,
            "plan_digest": current.plan_digest,
            "admin_policy_hash": current.effective_limit_profile_hash,
        }
        for key, current_value in checks.items():
            if str(bound.get(key) or "") != str(current_value):
                raise OrganizationCompileBindingError(f"organization_{key}_stale")
        return current, bound

    def verify_replay_binding(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        client_plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Verify a historical compile binding without granting a new write.

        Signature, schema, authenticated scope, and mirrored client fields are
        still mandatory.  Only token age is ignored so the caller can look up
        an already committed idempotency receipt; a missing receipt must return
        to ``recompile_bound_plan`` and its normal TTL/catalog checks.
        """

        return self._decode_bound_plan(
            tenant_id=tenant_id,
            project_id=project_id,
            principal_id=principal_id,
            client_plan=client_plan,
            enforce_ttl=False,
        )

    def _decode_bound_plan(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        client_plan: Mapping[str, Any],
        enforce_ttl: bool,
    ) -> dict[str, Any]:
        token = str(client_plan.get("compile_token") or "").strip()
        if not token:
            raise OrganizationCompileBindingError("organization_compile_token_required")
        try:
            bound = self._signer.loads(token, max_age=self._ttl if enforce_ttl else None)
        except SignatureExpired as exc:
            raise OrganizationCompileBindingError("organization_compile_plan_expired") from exc
        except BadData as exc:
            raise OrganizationCompileBindingError("organization_compile_token_invalid") from exc
        if not isinstance(bound, dict) or bound.get("schema") != "organization_compile_token.v1":
            raise OrganizationCompileBindingError("organization_compile_token_invalid")
        expected_scope = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "principal_id": principal_id,
        }
        if any(str(bound.get(key) or "") != value for key, value in expected_scope.items()):
            raise OrganizationCompileBindingError("organization_compile_scope_mismatch")
        for key in ("organization_id", "definition_revision", "plan_digest", "admin_policy_hash"):
            if str(client_plan.get(key) or "") != str(bound.get(key) or ""):
                raise OrganizationCompileBindingError(f"organization_{key}_binding_invalid")
        return bound

    def _compiler(self, *, definitions=None, limit_profiles=None) -> OrganizationBlueprintCompiler:
        return OrganizationBlueprintCompiler(
            definitions=definitions or self._catalog,
            limit_profiles=limit_profiles or self._catalog,
            admission_policy=self._admission,
        )

    @contextmanager
    def _scoped_ports(self, tenant_id: str, project_id: str) -> Iterator[tuple[Any, Any, Any]]:
        with OrganizationUnitOfWork(session_factory=self._session_factory) as uow:
            repository = FileCatalogDefinitionRepositoryAdapter(
                uow.definitions,
                self._catalog,
                uow.session,
            )
            yield (
                SqlOrganizationDefinitionCatalogAdapter(
                    repository,
                    tenant_id=tenant_id,
                    project_id=project_id,
                ),
                SqlOrganizationLimitProfileAdapter(repository),
                repository,
            )

    def get_blueprint(
        self,
        *,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int | None,
        include_inactive: bool = True,
    ) -> tuple[Any, str] | None:
        with self._scoped_ports(tenant_id, project_id) as (definitions, _limits, repository):
            if include_inactive and version is not None:
                row = repository.get_organization_blueprint(tenant_id, project_id, key, version)
                if row is None:
                    return None
                return (
                    self._validated_definition(
                        definitions=definitions,
                        row=row,
                        key=key,
                        version=version,
                    ),
                    str(row.lifecycle),
                )
            definition = self._resolve_active_definition(
                definitions=definitions,
                repository=repository,
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
                version=version,
            )
            return (definition, "active") if definition is not None else None

    def _active_definitions(self, *, definitions, repository, tenant_id: str, project_id: str) -> list[Any]:
        keys = {value.key for value in self._catalog.list_organization_blueprints()}
        keys.update(
            row.definition_key for row in repository.list_organization_blueprint_revisions(tenant_id, project_id)
        )
        values = []
        for key in sorted(keys):
            definition = self._resolve_active_definition(
                definitions=definitions,
                repository=repository,
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
                version=None,
            )
            if definition is not None:
                values.append(definition)
        return values

    def _resolve_active_definition(
        self,
        *,
        definitions,
        repository,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int | None,
    ):
        candidate_versions = (
            {version}
            if version is not None
            else {value.version for value in self._catalog.list_organization_blueprints() if value.key == key}
        )
        if version is None:
            candidate_versions.update(
                row.version
                for row in repository.list_organization_blueprint_revisions(
                    tenant_id,
                    project_id,
                    key=key,
                )
            )
        for candidate in sorted((value for value in candidate_versions if value is not None), reverse=True):
            row = repository.get_organization_blueprint(
                tenant_id,
                project_id,
                key,
                candidate,
            )
            if row is not None and str(row.lifecycle) == "active":
                return self._validated_definition(
                    definitions=definitions,
                    row=row,
                    key=key,
                    version=candidate,
                )
        return None

    @staticmethod
    def _validated_definition(*, definitions, row, key: str, version: int):
        definition = definitions.get_organization_blueprint(key, version)
        if definition is None:
            raise OrganizationCompileBindingError("organization_definition_payload_missing")
        if canonical_definition_sha256(definition) != str(row.content_hash or ""):
            raise OrganizationCompileBindingError("organization_definition_content_hash_mismatch")
        return definition

    @staticmethod
    def selector(definition_key: str, team_count: int) -> str:
        return f"{definition_key}:standard:{team_count}"

    @staticmethod
    def parse_selector(value: str) -> tuple[str, int | None]:
        raw = str(value or "").strip()
        head, separator, count = raw.rpartition(":standard:")
        if separator:
            if not head or not count.isdigit():
                raise OrganizationCompileBindingError("organization_blueprint_selector_invalid")
            return head, int(count)
        if not raw:
            raise OrganizationCompileBindingError("organization_blueprint_selector_invalid")
        return raw, None

    @staticmethod
    def _version(value: object) -> int | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if not raw.isdigit() or int(raw) < 1:
            raise OrganizationCompileBindingError("organization_blueprint_version_invalid")
        return int(raw)

    @staticmethod
    def _composition(
        payload: Mapping[str, Any],
        *,
        selector_count: int | None,
    ) -> tuple[str, int | None, dict[str, int] | None]:
        parameters = payload.get("parameters")
        parameter_map = dict(parameters) if isinstance(parameters, Mapping) else {}
        raw_counts = parameter_map.get("team_blueprint_counts")
        if raw_counts is None and isinstance(parameter_map.get("custom_composition"), Mapping):
            raw_counts = dict(parameter_map["custom_composition"]).get("team_blueprint_counts")
        custom_keys = payload.get("custom_team_blueprint_keys")
        if raw_counts is None and isinstance(custom_keys, list) and custom_keys:
            raw_counts = dict(Counter(str(value) for value in custom_keys if str(value)))
        if raw_counts is not None:
            if not isinstance(raw_counts, Mapping):
                raise OrganizationCompileBindingError("organization_custom_composition_invalid")
            counts: dict[str, int] = {}
            for key, value in raw_counts.items():
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise OrganizationCompileBindingError("organization_custom_composition_invalid")
                counts[str(key)] = value
            if selector_count is not None:
                raise OrganizationCompileBindingError("organization_composition_mode_conflict")
            return "custom", None, counts
        value = payload.get("team_count", selector_count)
        if isinstance(value, bool) or not isinstance(value, int):
            raise OrganizationCompileBindingError("organization_team_count_invalid")
        if selector_count is not None and value != selector_count:
            raise OrganizationCompileBindingError("organization_team_count_selector_mismatch")
        return "standard", value, None

    def _response(
        self,
        plan: CompiledOrganizationPlan,
        *,
        compile_token: str,
        expires_at: float,
        admin_policy_hash: str,
        admission_exception_ref: str | None,
        title: str,
    ) -> dict[str, Any]:
        definition_ref = VersionedDefinitionRef.parse(plan.definition_ref)
        diagnostics = [
            {
                "severity": value.severity,
                "reason_code": value.reason_code,
                "message": value.human_message,
                **({"node_ids": list(value.details.get("node_ids") or [])} if value.details.get("node_ids") else {}),
            }
            for value in (*plan.warnings, *plan.blockers)
        ]
        required_unfilled = [f"{slot.unit_key}:{slot.slot_key}" for slot in plan.role_slots if slot.required]
        return {
            "blueprint_key": definition_ref.key,
            "blueprint_version": str(definition_ref.version),
            "title": title,
            "organization_id": plan.organization_id,
            "definition_ref": plan.definition_ref,
            "definition_revision": plan.definition_revision,
            "plan_digest": plan.plan_digest,
            "compile_token": compile_token,
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "admin_policy_hash": admin_policy_hash,
            "composition_mode": plan.composition_mode,
            "team_count": plan.requested_team_count,
            "unit_count": len(plan.units),
            "hierarchy_edge_count": int(plan.expected_counts.get("contains", len(plan.units))),
            "relation_edge_count": len(plan.relations),
            "role_slot_count": len(plan.role_slots),
            "planned_writes": [
                "organization_instance",
                f"organization_units:{len(plan.units)}",
                f"team_instances:{plan.requested_team_count}",
                f"role_slots:{len(plan.role_slots)}",
                f"organization_relations:{len(plan.relations)}",
                "topology_snapshot",
                "membership_and_admin_grant",
                "audit_outbox",
            ],
            "capability_gaps": list(plan.capability_gaps),
            "unfilled_required_slots": required_unfilled,
            "budget_assumptions": {
                "default_assignment_capacity": int(plan.expected_counts.get("assignment_capacity_default", 0)),
                "workflow_steps": int(plan.expected_counts.get("workflow_step", 0)),
                "parallel_teams": plan.requested_team_count,
            },
            "diagnostics": diagnostics,
            "limits": self._limits(plan),
            "admission_exception_ref": admission_exception_ref,
        }

    def _limits(self, plan: CompiledOrganizationPlan) -> dict[str, Any]:
        with self._scoped_ports(plan.tenant_id, plan.project_id) as (
            _definitions,
            limits_port,
            _repository,
        ):
            limits = limits_port.resolve_limit_profile(
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                policy_ref=plan.effective_limit_profile_ref,
            )
        return {
            "revision": str(limits.revision),
            "policy_hash": limits.content_hash(),
            "max_teams": limits.max_team_instances_per_organization,
            "max_units": limits.max_units_per_organization,
            "max_role_slots": limits.max_role_slots_per_organization,
            "max_assignments": limits.max_assignments_per_organization,
            "max_relations": limits.max_relations_per_organization,
            "max_patch_operations": limits.max_patch_operations,
            "max_page_size": limits.topology_max_page_size,
            "max_depth": limits.topology_max_depth,
            "max_render_nodes": limits.canvas_render_node_limit,
            "max_render_edges": limits.canvas_render_edge_limit,
        }

    @staticmethod
    def _title(key: str, team_count: int) -> str:
        label = key.replace("_", " ").title()
        return f"{label} · {team_count} Teams"

    @staticmethod
    def _activation_summary(definition, count: int) -> list[str]:
        standard = definition.standard_composition
        baseline = len(standard.baseline_singleton_team_refs) + sum(standard.baseline_group_counts.values())
        additions = max(0, min(count - baseline, len(standard.activation_order)))
        active = list(standard.activation_order[:additions])
        scaled = max(0, count - baseline - additions)
        summary = [f"Baseline: {baseline} Teams"]
        summary.extend(f"Aktiviert: {key}" for key in active)
        if scaled:
            summary.append(f"Scale-out {standard.scale_out_group}: +{scaled}")
        return summary

    def _capability_summary(self, definition, count: int, *, definitions=None) -> list[str]:
        standard = definition.standard_composition
        singleton_keys = list(standard.baseline_singleton_team_refs)
        baseline = len(singleton_keys) + sum(standard.baseline_group_counts.values())
        activated_count = max(
            0,
            min(count - baseline, len(standard.activation_order)),
        )
        singleton_keys.extend(standard.activation_order[:activated_count])
        refs = [
            unit.team_blueprint_ref
            for unit in definition.units
            if unit.unit_key in singleton_keys and unit.team_blueprint_ref
        ]
        refs.extend(
            group.team_blueprint_ref
            for group in definition.unit_groups
            if group.group_id in standard.baseline_group_counts
        )
        kinds = {
            blueprint.team_kind
            for value in refs
            for ref in [VersionedDefinitionRef.parse(value)]
            for blueprint in [(definitions or self._catalog).get_team_blueprint(ref.key, ref.version)]
            if blueprint is not None
        }
        return sorted(kinds)

    @staticmethod
    def _custom_blueprint_options(definition, maximum_team_count: int) -> list[dict[str, Any]]:
        options: dict[str, dict[str, Any]] = {}
        standard = definition.standard_composition
        for unit in definition.units:
            if unit.materialization_kind != "team_instance" or not unit.team_blueprint_ref:
                continue
            ref = VersionedDefinitionRef.parse(unit.team_blueprint_ref)
            options[ref.key] = {
                "key": ref.key,
                "version": str(ref.version),
                "title": ref.key.replace("_", " ").title(),
                "repeatable": False,
                "minimum_when_selected": 1,
                "maximum": 1,
                "standard_baseline": unit.unit_key in standard.baseline_singleton_team_refs,
                "standard_default_count": (1 if unit.unit_key in standard.baseline_singleton_team_refs else 0),
            }
        for group in definition.unit_groups:
            ref = VersionedDefinitionRef.parse(group.team_blueprint_ref)
            options[ref.key] = {
                "key": ref.key,
                "version": str(ref.version),
                "title": ref.key.replace("_", " ").title(),
                "repeatable": True,
                "minimum_when_selected": group.min_count,
                "maximum": min(
                    int(group.max_count or maximum_team_count),
                    int(maximum_team_count),
                ),
                "standard_baseline": group.group_id in standard.baseline_group_counts,
                "standard_default_count": int(standard.baseline_group_counts.get(group.group_id, 0)),
            }
        return [options[key] for key in sorted(options)]


__all__ = [
    "DenyOrganizationAdmissionPolicy",
    "OrganizationCompileApplicationService",
    "OrganizationCompileBindingError",
]
