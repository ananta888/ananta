"""Write-free dry-run planner for portable Organization Bundle v2 imports."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    OrganizationBundleImportPlan,
    OrganizationBundlePlanItem,
    OrganizationCompileRequest,
    OrganizationDiagnostic,
    OrganizationLimitProfile,
    TeamBlueprintDefinition,
    VersionedDefinitionRef,
    canonical_definition_sha256,
    canonical_json,
    canonical_sha256,
)
from agent.models.team_models import OrganizationBlueprintBundleV2, PortableDefinitionRevision
from agent.services.organization_blueprint_compiler import (
    OrganizationBlueprintCompiler,
    OrganizationCompilationError,
)
from agent.services.organization_blueprint_validation_service import OrganizationBlueprintValidationService
from agent.services.organization_template_security_service import (
    OrganizationTemplateSecurityService,
)

SECTION_METHODS = {
    "policies": "get_policy",
    "limit_profiles": "get_limit_profile",
    "role_templates": "get_role_template",
    "handoff_definitions": "get_handoff",
    "workflow_definitions": "get_workflow",
    "team_blueprints": "get_team_blueprint",
    "organization_blueprints": "get_organization_blueprint",
}


class OrganizationBundlePlanner:
    def __init__(
        self,
        *,
        definitions,
        validator=None,
        clock=time.time,
        preview_ttl_seconds: int = 300,
        admission_policy=None,
        instance_exists: Callable[[str, str, str], bool] | None = None,
        template_security: OrganizationTemplateSecurityService | None = None,
        allowed_template_appendix_refs: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._definitions = definitions
        self._validator = validator or OrganizationBlueprintValidationService()
        self._clock = clock
        self._ttl = max(30, min(int(preview_ttl_seconds), 1800))
        self._admission_policy = admission_policy or _DenyAdmissionPolicy()
        self._instance_exists = instance_exists or (lambda _tenant, _project, _organization: False)
        self._template_security = template_security or OrganizationTemplateSecurityService()
        self._allowed_template_appendix_refs = frozenset(allowed_template_appendix_refs or ())

    def plan(
        self,
        *,
        bundle: OrganizationBlueprintBundleV2,
        conflict_strategy: str,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        expected_target_revision: str,
        effective_limits: OrganizationLimitProfile,
        allowed_source_refs: list[str],
        allowed_run_refs: list[str],
        assignment_rebindings: Mapping[str, str] | None = None,
        instance_admission_exception_refs: Mapping[str, str] | None = None,
    ) -> OrganizationBundleImportPlan:
        if conflict_strategy not in {"fail", "skip", "overwrite"}:
            raise ValueError("organization_bundle_conflict_strategy_invalid")
        serialized = canonical_json(bundle.model_dump(mode="json"))
        bundle_digest = canonical_sha256(bundle.model_dump(mode="json"))
        errors: list[OrganizationDiagnostic] = []
        items: list[OrganizationBundlePlanItem] = []
        instance_plans = []
        instance_organization_ids: dict[str, str] = {}
        instance_names: dict[str, str] = {}
        instance_requested_lifecycles: dict[str, str] = {}
        instance_actions: dict[str, str] = {}
        bound_admission_refs: dict[str, str] = {}
        normalized_rebindings = {
            str(key or "").strip(): str(value or "").strip() for key, value in dict(assignment_rebindings or {}).items()
        }
        admission_refs = {
            str(key or "").strip(): str(value or "").strip()
            for key, value in dict(instance_admission_exception_refs or {}).items()
        }

        def issue(path: str, code: str, message: str, **details: Any) -> None:
            errors.append(
                OrganizationDiagnostic(
                    path=path,
                    reason_code=code,
                    human_message=message,
                    severity="blocker",
                    details=details,
                )
            )

        if len(serialized.encode("utf-8")) > effective_limits.max_bundle_bytes:
            issue("$", "ORGANIZATION_BUNDLE_SIZE_LIMIT_EXCEEDED", "Bundle exceeds the effective byte limit.")
        metadata = dict(bundle.bundle_metadata or {})
        for path in find_forbidden_portability_metadata_paths(metadata):
            issue(
                f"$.bundle_metadata{path}",
                "ORGANIZATION_BUNDLE_SOURCE_SCOPE_METADATA_FORBIDDEN",
                "Portable bundle metadata must not contain source scope or runtime bindings.",
            )
        declared_sources = [str(value) for value in metadata.get("allowed_source_refs") or []]
        declared_runs = [str(value) for value in metadata.get("allowed_run_refs") or []]
        self._validate_grounding_refs(
            declared_sources, allowed_source_refs, "SRC_", "$.bundle_metadata.allowed_source_refs", issue
        )
        self._validate_grounding_refs(
            declared_runs, allowed_run_refs, "RUN_", "$.bundle_metadata.allowed_run_refs", issue
        )

        seen: set[tuple[str, str, int]] = set()
        for section, method_name in SECTION_METHODS.items():
            for index, revision in enumerate(getattr(bundle, section)):
                identity = (section, revision.key, revision.version)
                if identity in seen:
                    issue(
                        f"$.{section}[{index}]",
                        "ORGANIZATION_BUNDLE_DEFINITION_DUPLICATE",
                        "Definition identity occurs more than once.",
                    )
                    continue
                seen.add(identity)
                actual_hash = canonical_definition_sha256(revision.definition)
                if actual_hash != revision.content_hash:
                    issue(
                        f"$.{section}[{index}].content_hash",
                        "ORGANIZATION_BUNDLE_CONTENT_HASH_MISMATCH",
                        "Declared content hash does not match canonical definition JSON.",
                    )
                self._validate_template_security(
                    section=section,
                    index=index,
                    revision=revision,
                    issue=issue,
                )
                existing = getattr(self._definitions, method_name)(
                    tenant_id,
                    project_id,
                    revision.key,
                    revision.version,
                )
                action, changes = _plan_revision_action(existing, revision, conflict_strategy)
                items.append(
                    OrganizationBundlePlanItem(
                        section=section,
                        key=revision.key,
                        version=revision.version,
                        content_hash=revision.content_hash,
                        action=action,
                        changes=changes,
                    )
                )
                if action == "conflict":
                    issue(
                        f"$.{section}[{index}]",
                        "ORGANIZATION_BUNDLE_REVISION_CONFLICT",
                        "The scoped key/version already exists with different content.",
                    )

        overlay = _BundleCatalogOverlay(
            bundle=bundle,
            repository=self._definitions,
            tenant_id=tenant_id,
            project_id=project_id,
        )
        for index, revision in enumerate(bundle.organization_blueprints):
            try:
                definition = OrganizationBlueprintDefinition.model_validate(revision.definition)
            except ValidationError as exc:
                issue(
                    f"$.organization_blueprints[{index}].definition",
                    "ORGANIZATION_BLUEPRINT_CONTRACT_INVALID",
                    "Organization blueprint contract is invalid.",
                    validation_errors=exc.errors(include_url=False),
                )
                continue
            try:
                definition_limits = _BundleLimitProfileOverlay(overlay).resolve_limit_profile(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    policy_ref=definition.limit_policy_ref,
                )
            except (TypeError, ValueError):
                issue(
                    f"$.organization_blueprints[{index}].definition.limit_policy_ref",
                    "ORGANIZATION_LIMIT_PROFILE_NOT_FOUND",
                    "Organization blueprint references no available limit profile.",
                )
                continue
            errors.extend(
                self._validator.validate(
                    definition,
                    catalog=overlay,
                    limits=definition_limits,
                )
            )

        active_overlay = _BundleCatalogOverlay(
            bundle=bundle,
            repository=self._definitions,
            tenant_id=tenant_id,
            project_id=project_id,
            require_active=True,
        )
        compiler = OrganizationBlueprintCompiler(
            definitions=active_overlay,
            limit_profiles=_BundleLimitProfileOverlay(active_overlay),
            admission_policy=self._admission_policy,
        )
        portable_instance_keys = {value.instance_key for value in bundle.organization_instances}
        unknown_admission_refs = sorted(set(admission_refs) - portable_instance_keys)
        if unknown_admission_refs:
            issue(
                "$.instance_admission_exception_refs",
                "ORGANIZATION_BUNDLE_ADMISSION_REBIND_UNKNOWN",
                "An admission exception does not correspond to a portable instance recipe.",
                instance_keys=unknown_admission_refs,
            )
        for index, instance in enumerate(bundle.organization_instances):
            path = f"$.organization_instances[{index}]"
            legacy_values = {
                "organization_id": instance.organization_id,
                "definition_revision": instance.definition_revision,
                "effective_limit_profile_ref": instance.effective_limit_profile_ref,
                "effective_limit_profile_revision": instance.effective_limit_profile_revision,
                "effective_limit_profile_hash": instance.effective_limit_profile_hash,
                "plan_digest": instance.plan_digest,
                "topology_snapshot": instance.topology_snapshot,
            }
            if any(value is not None for value in legacy_values.values()):
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_SOURCE_BOUND_INSTANCE_FORBIDDEN",
                    "Source-bound instance IDs, plans, snapshots, and policy hashes are not portable.",
                    fields=sorted(key for key, value in legacy_values.items() if value is not None),
                )
                continue
            target_organization_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (f"ananta:bundle-instance:{tenant_id}:{project_id}:{bundle_digest}:{instance.instance_key}"),
                )
            )
            instance_organization_ids[instance.instance_key] = target_organization_id
            instance_names[instance.instance_key] = instance.name
            instance_requested_lifecycles[instance.instance_key] = instance.requested_lifecycle
            admission_ref = admission_refs.get(instance.instance_key, "")
            if admission_ref and instance.composition_mode != "custom":
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_STANDARD_ADMISSION_EXCEPTION_FORBIDDEN",
                    "Standard compositions must not consume a custom admission exception.",
                )
                continue
            if admission_ref:
                bound_admission_refs[instance.instance_key] = admission_ref
            try:
                compiled = compiler.compile(
                    OrganizationCompileRequest(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        principal_id=principal_id,
                        organization_id=target_organization_id,
                        definition_ref=instance.definition_ref,
                        composition_mode=instance.composition_mode,
                        team_count=instance.team_count,
                        custom_composition=instance.team_blueprint_counts,
                        admission_exception_ref=admission_ref or None,
                    )
                )
            except (OrganizationCompilationError, ValueError) as exc:
                issue(
                    path,
                    str(getattr(exc, "reason_code", "") or "ORGANIZATION_BUNDLE_INSTANCE_COMPILE_INVALID"),
                    "Portable instance recipe could not be recompiled in the authenticated target scope.",
                )
                continue
            existing = self._instance_exists(tenant_id, project_id, target_organization_id)
            action = "create"
            if existing:
                action = "skip" if conflict_strategy == "skip" else "conflict"
                if action == "conflict":
                    issue(
                        path,
                        "ORGANIZATION_BUNDLE_INSTANCE_CONFLICT",
                        "The target-scoped instance key is already materialized.",
                    )
            items.append(
                OrganizationBundlePlanItem(
                    section="organization_instances",
                    key=instance.instance_key,
                    version=1,
                    content_hash=canonical_sha256(instance.model_dump(mode="json", exclude_none=True)),
                    action=action,
                    changes=["target_recompile", f"teams:{compiled.requested_team_count}"],
                )
            )
            instance_actions[instance.instance_key] = action
            if action == "create":
                instance_plans.append(compiled)

        assignment_refs = {row.principal_ref for row in bundle.assignments}
        unknown_rebindings = sorted(set(normalized_rebindings) - assignment_refs)
        if unknown_rebindings:
            issue(
                "$.assignment_rebindings",
                "ORGANIZATION_BUNDLE_ASSIGNMENT_REBIND_UNKNOWN",
                "A target-local rebind does not correspond to an exported assignment principal.",
                principal_refs=unknown_rebindings,
            )
        seen_assignments: set[tuple[str, str, str, str]] = set()
        seen_target_assignments: set[tuple[str, str, str, str]] = set()
        for index, assignment in enumerate(bundle.assignments):
            path = f"$.assignments[{index}]"
            if assignment.organization_id is not None:
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_SOURCE_BOUND_ASSIGNMENT_FORBIDDEN",
                    "Source organization IDs are forbidden in portable assignment intents.",
                )
                continue
            identity = (
                assignment.instance_key,
                assignment.unit_key,
                assignment.role_slot_key,
                assignment.principal_ref,
            )
            if identity in seen_assignments:
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_ASSIGNMENT_DUPLICATE",
                    "The portable assignment intent occurs more than once.",
                )
                continue
            seen_assignments.add(identity)
            instance_action = instance_actions.get(assignment.instance_key)
            agent_url = normalized_rebindings.get(assignment.principal_ref, "")
            if instance_action != "skip" and not _valid_target_agent_url(agent_url):
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_ASSIGNMENT_REBIND_REQUIRED",
                    "Each pseudonymized principal requires an explicit target-local Agent URL.",
                    principal_ref=assignment.principal_ref,
                )
                continue
            if assignment.instance_key not in instance_organization_ids:
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_ASSIGNMENT_INSTANCE_MISSING",
                    "Assignment intent references no valid portable instance recipe.",
                )
                continue
            target_identity = (
                assignment.instance_key,
                assignment.unit_key,
                assignment.role_slot_key,
                agent_url,
            )
            if target_identity in seen_target_assignments:
                issue(
                    path,
                    "ORGANIZATION_BUNDLE_ASSIGNMENT_TARGET_DUPLICATE",
                    "Multiple pseudonyms resolve to the same target slot and Agent.",
                )
                continue
            seen_target_assignments.add(target_identity)
            items.append(
                OrganizationBundlePlanItem(
                    section="assignments",
                    key=":".join(identity),
                    version=1,
                    content_hash=canonical_sha256(assignment.model_dump(mode="json", exclude_none=True)),
                    action="skip" if instance_action == "skip" else "create",
                    changes=["target_local_rebind"],
                )
            )

        if len(bundle.assignments) > effective_limits.max_assignments_per_organization:
            issue("$.assignments", "ORGANIZATION_ASSIGNMENT_LIMIT_EXCEEDED", "Bundle exceeds the assignment limit.")

        expires_at_epoch = self._clock() + self._ttl
        expires_at = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "schema_version": "2.0",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "principal_id": principal_id,
            "conflict_strategy": conflict_strategy,
            "bundle_digest": bundle_digest,
            "expected_target_revision": expected_target_revision,
            "effective_limit_profile_ref": f"{effective_limits.policy_id}@{effective_limits.revision}",
            "effective_limit_profile_revision": effective_limits.revision,
            "effective_limit_profile_hash": effective_limits.content_hash(),
            "expires_at": expires_at,
            "expires_at_epoch": expires_at_epoch,
            "allowed_source_refs": declared_sources,
            "allowed_run_refs": declared_runs,
            "items": [item.model_dump(mode="json") for item in items],
            "instance_plans": [item.model_dump(mode="json") for item in instance_plans],
            "instance_organization_ids": instance_organization_ids,
            "instance_names": instance_names,
            "instance_requested_lifecycles": instance_requested_lifecycles,
            "instance_admission_exception_refs": bound_admission_refs,
            "assignment_rebindings": normalized_rebindings,
            "errors": [error.model_dump(mode="json") for error in errors],
        }
        return OrganizationBundleImportPlan(**payload, plan_digest=canonical_sha256(payload))

    def _validate_template_security(
        self,
        *,
        section: str,
        index: int,
        revision: PortableDefinitionRevision,
        issue: Callable[..., None],
    ) -> None:
        if section != "role_templates":
            return
        decision = self._template_security.validate_role_definition(
            template_key=revision.key,
            template_version=revision.version,
            definition=revision.definition,
            allowed_appendix_refs=self._allowed_template_appendix_refs,
        )
        if not decision.allowed:
            issue(
                f"$.{section}[{index}].definition.prompt_template",
                "ORGANIZATION_BUNDLE_ROLE_TEMPLATE_UNTRUSTED",
                "Role-template instructions violate target Hub governance policy.",
                reason_codes=list(decision.reason_codes),
                provenance_hash=decision.provenance_hash,
            )

    @staticmethod
    def _validate_grounding_refs(values, allowlist, prefix, path, issue) -> None:
        allowed = set(allowlist)
        for index, value in enumerate(values):
            if not value.startswith(prefix) or value not in allowed:
                issue(
                    f"{path}[{index}]",
                    "GROUNDING_REFERENCE_UNVERIFIED",
                    "Grounding reference was not supplied in the Hub assignment allowlist.",
                )


_FORBIDDEN_PORTABILITY_KEYS = frozenset(
    {
        "tenant_id",
        "project_id",
        "organization_id",
        "source_tenant_id",
        "source_project_id",
        "source_organization_id",
        "database_id",
        "local_database_id",
        "local_id",
        "environment_id",
        "compiled_plan",
    }
)


def find_forbidden_portability_metadata_paths(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in _FORBIDDEN_PORTABILITY_KEYS:
                findings.append(child_path)
            findings.extend(find_forbidden_portability_metadata_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_forbidden_portability_metadata_paths(child, f"{path}[{index}]"))
    return findings


def _plan_revision_action(existing, incoming: PortableDefinitionRevision, strategy: str) -> tuple[str, list[str]]:
    if existing is None:
        return "create", []
    existing_hash = str(getattr(existing, "content_hash", None) or getattr(existing, "profile_hash", ""))
    if existing_hash == incoming.content_hash:
        return "unchanged", []
    if strategy == "skip":
        return "skip", ["content_hash"]
    lifecycle = str(getattr(existing, "lifecycle", "active"))
    if strategy == "overwrite" and lifecycle == "draft":
        return "update", ["content_hash", "definition"]
    return "conflict", ["content_hash", "definition"]


class _BundleCatalogOverlay:
    def __init__(
        self,
        *,
        bundle,
        repository,
        tenant_id,
        project_id,
        require_active: bool = False,
    ) -> None:
        self.bundle = bundle
        self.repository = repository
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.require_active = require_active
        self._incoming = {
            section: {(item.key, item.version): item for item in getattr(bundle, section)}
            for section in SECTION_METHODS
        }

    def _definition(self, section, method, key, version, model=None):
        incoming = self._incoming[section].get((key, version))
        value = (
            incoming.definition
            if incoming is not None and (not self.require_active or incoming.lifecycle == "active")
            else None
        )
        if value is None:
            row = getattr(self.repository, method)(self.tenant_id, self.project_id, key, version)
            if row is not None and self.require_active and getattr(row, "lifecycle", "active") != "active":
                row = None
            value = getattr(row, "definition_json", None) if row else None
            if row is not None and section == "limit_profiles":
                value = {
                    "policy_id": row.policy_key,
                    "revision": row.revision,
                    **dict(row.limits_json or {}),
                }
        if value is None:
            return None
        return model.model_validate(value) if model else value

    def get_organization_blueprint(self, key, version):
        return self._definition(
            "organization_blueprints", "get_organization_blueprint", key, version, OrganizationBlueprintDefinition
        )

    def get_team_blueprint(self, key, version):
        return self._definition("team_blueprints", "get_team_blueprint", key, version, TeamBlueprintDefinition)

    def has_role_template(self, key, version):
        return self._definition("role_templates", "get_role_template", key, version) is not None

    def has_workflow_definition(self, key, version):
        return self._definition("workflow_definitions", "get_workflow", key, version) is not None

    def get_workflow_definition(self, key, version):
        return self._definition("workflow_definitions", "get_workflow", key, version)

    def has_handoff_definition(self, key, version):
        return self._definition("handoff_definitions", "get_handoff", key, version) is not None

    def has_policy(self, portable_ref):
        try:
            ref = VersionedDefinitionRef.parse(portable_ref)
        except ValueError:
            return False
        return (
            self._definition("policies", "get_policy", ref.key, ref.version) is not None
            or self._definition("limit_profiles", "get_limit_profile", ref.key, ref.version) is not None
        )

    def get_limit_profile(self, key, version):
        return self._definition("limit_profiles", "get_limit_profile", key, version)


class _BundleLimitProfileOverlay:
    def __init__(self, definitions: _BundleCatalogOverlay) -> None:
        self._definitions = definitions

    def resolve_limit_profile(self, *, tenant_id: str, project_id: str, policy_ref: str):
        del tenant_id, project_id
        ref = VersionedDefinitionRef.parse(policy_ref)
        value = self._definitions.get_limit_profile(ref.key, ref.version)
        if value is None:
            raise ValueError("organization_limit_profile_not_found")
        if isinstance(value, OrganizationLimitProfile):
            return value
        if isinstance(value, dict):
            return OrganizationLimitProfile.model_validate(value)
        return OrganizationLimitProfile(
            policy_id=str(value.policy_key),
            revision=int(value.revision),
            **dict(value.limits_json or {}),
        )


class _DenyAdmissionPolicy:
    def validate_exception(self, **_kwargs):
        return False, "organization_bundle_custom_admission_exception_required"


def _valid_target_agent_url(value: str) -> bool:
    if not value or len(value) > 512:
        return False
    parsed = urlsplit(value)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


__all__ = [
    "OrganizationBundlePlanner",
    "SECTION_METHODS",
    "find_forbidden_portability_metadata_paths",
]
