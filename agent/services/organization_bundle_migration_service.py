"""Explicit compatibility adapter from Team Blueprint Bundle v1 to v2.

Only the portable Team Blueprint slice is produced.  The adapter intentionally
does not infer an Organization Blueprint, Organization Instance, hierarchy or
organization relation from a single-team legacy document.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.models.organization_models import (
    AssignmentPolicyDefinition,
    RoleSlotDefinition,
    SeparationOfDutiesDefinition,
    TeamArtifactContract,
    TeamBlueprintDefinition,
    TeamCapacityDefaults,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.models.team_models import (
    OrganizationBlueprintBundleV2,
    PortableDefinitionRevision,
    TeamBlueprintBundle,
)


class OrganizationBundleMigrationError(ValueError):
    pass


class OrganizationBundleMigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_schema_version: str
    target_schema_version: str
    source_digest: str
    target_digest: str
    warnings: list[str]
    bundle: OrganizationBlueprintBundleV2


class OrganizationBundleMigrationService:
    """Deterministically project a legacy bundle into portable v2 sections."""

    def migrate_v1_team_slice(
        self,
        source: TeamBlueprintBundle | dict[str, Any],
    ) -> OrganizationBundleMigrationResult:
        bundle = source if isinstance(source, TeamBlueprintBundle) else TeamBlueprintBundle.model_validate(source)
        if bundle.schema_version != "1.0":
            raise OrganizationBundleMigrationError("organization_bundle_v1_schema_required")
        if bundle.blueprint is None:
            raise OrganizationBundleMigrationError("organization_bundle_v1_blueprint_required")

        team_key = _key(bundle.blueprint.name)
        workflow_key = f"{team_key}_legacy_compat_workflow"
        role_by_template: dict[str, str] = {}
        portable_roles: list[PortableDefinitionRevision] = []
        warnings = [
            "legacy_bundle_team_slice_only",
            "organization_topology_and_relations_not_inferred",
        ]

        for template in sorted(bundle.templates, key=lambda value: value.name.casefold()):
            role_key = _key(template.name)
            if role_key in role_by_template.values():
                raise OrganizationBundleMigrationError("organization_bundle_v1_template_key_collision")
            role_by_template[template.name] = role_key
            definition = _role_template_definition(
                key=role_key,
                name=template.name,
                description=template.description,
                prompt_template=template.prompt_template,
                outputs=_artifact_kinds(bundle),
            )
            portable_roles.append(_portable(role_key, 1, definition))

        role_slots: list[RoleSlotDefinition] = []
        seen_slots: set[str] = set()
        for role in sorted(bundle.blueprint.roles, key=lambda value: (value.sort_order, value.name.casefold())):
            slot_key = _key(role.name)
            if slot_key in seen_slots:
                raise OrganizationBundleMigrationError("organization_bundle_v1_role_key_collision")
            seen_slots.add(slot_key)
            template_key = role_by_template.get(str(role.template_name or ""))
            if template_key is None:
                template_key = f"{team_key}_{slot_key}_legacy_role"
                definition = _role_template_definition(
                    key=template_key,
                    name=role.name,
                    description=role.description,
                    prompt_template=(
                        "Execute only Hub-delegated work for the migrated legacy role and return artifacts to the Hub."
                    ),
                    outputs=_artifact_kinds(bundle),
                )
                portable_roles.append(_portable(template_key, 1, definition))
                warnings.append(f"legacy_role_template_materialized:{slot_key}")
            config = dict(role.config or {})
            required = bool(role.is_required)
            minimum = _non_negative_int(config.get("min_count"), 1 if required else 0)
            default = _non_negative_int(config.get("default_count"), max(1 if required else 0, minimum))
            maximum = _optional_positive_int(config.get("max_count"), max(1, default))
            if not minimum <= default <= maximum:
                raise OrganizationBundleMigrationError("organization_bundle_v1_role_cardinality_invalid")
            capabilities = sorted({str(value) for value in config.get("required_capabilities") or [] if str(value)})
            role_slots.append(
                RoleSlotDefinition(
                    slot_id=slot_key,
                    role_template_ref=f"{template_key}@1",
                    required=required,
                    min_count=minimum,
                    default_count=default,
                    max_count=maximum,
                    assignment_policy=AssignmentPolicyDefinition(
                        principal_kinds=["agent", "human"],
                        required_capabilities=capabilities,
                        forbidden_capabilities=["worker_orchestration"],
                        write_access_required=bool(config.get("write_access_required", False)),
                    ),
                    separation_of_duties=SeparationOfDutiesDefinition(
                        enforcement="none",
                        independent_from_slot_ids=[],
                    ),
                    overlays=[],
                )
            )

        if not role_slots:
            raise OrganizationBundleMigrationError("organization_bundle_v1_roles_required")
        artifacts = [
            TeamArtifactContract(kind=value, required=True, portable=True) for value in _artifact_kinds(bundle)
        ]
        if not artifacts:
            artifacts = [TeamArtifactContract(kind="legacy_team_result", required=True, portable=True)]
            warnings.append("legacy_artifact_contract_materialized")

        default_agents = sum(value.default_count for value in role_slots)
        team_definition = TeamBlueprintDefinition(
            key=team_key,
            version=1,
            description=(bundle.blueprint.description or f"Migrated legacy Team Blueprint {bundle.blueprint.name}"),
            team_kind=_team_kind(bundle),
            role_slots=role_slots,
            artifacts=artifacts,
            workflow_ref=f"{workflow_key}@1",
            policies=["legacy_bundle_grounding@1", "legacy_bundle_task_proposals@1"],
            capacity_defaults=TeamCapacityDefaults(
                min_agents=sum(value.min_count for value in role_slots) or 1,
                default_agents=max(1, default_agents),
                max_agents=max(1, sum(value.max_count or value.default_count for value in role_slots)),
            ),
        )

        first_role_ref = role_slots[0].role_template_ref
        workflow_definition = {
            "key": workflow_key,
            "version": 1,
            "mode": "gated",
            "default_failure_policy": "manual",
            "steps": [
                {
                    "step_id": "legacy_hub_delegated_execution",
                    "title": "Execute one Hub-delegated legacy team task",
                    "task_kind": "documentation",
                    "owner_role_ref": first_role_ref,
                    "target_team_selector": {
                        "team_blueprint_ref": f"{team_key}@1",
                        "cardinality": 1,
                        "routing": "single",
                    },
                    "depends_on": [],
                    "inputs": ["hub_delegated_task"],
                    "outputs": [value.kind for value in artifacts],
                    "gate": {
                        "required": False,
                        "acceptance_checks": [],
                        "approval_role_ref": None,
                        "independent_principal_required": False,
                    },
                    "failure_policy": "manual",
                }
            ],
        }
        policies = [
            _compatibility_policy("legacy_bundle_grounding", "grounding"),
            _compatibility_policy("legacy_bundle_task_proposals", "task_proposal"),
        ]
        migrated = OrganizationBlueprintBundleV2(
            bundle_metadata={
                "migration": "team_blueprint_bundle_v1_to_organization_bundle_v2_team_slice",
                "source_schema_version": "1.0",
                "source_mode": bundle.mode,
                "source_parts": sorted(set(bundle.parts)),
                "omitted_sections": [
                    "organization_blueprints",
                    "handoff_definitions",
                    "limit_profiles",
                    "organization_instances",
                    "assignments",
                ],
            },
            role_templates=sorted(portable_roles, key=lambda value: (value.key, value.version)),
            team_blueprints=[_portable(team_key, 1, team_definition.model_dump(mode="json"))],
            workflow_definitions=[_portable(workflow_key, 1, workflow_definition)],
            policies=policies,
        )
        if bundle.team and bundle.team.members:
            warnings.append("legacy_member_assignments_omitted")
        source_payload = bundle.model_dump(mode="json")
        target_payload = migrated.model_dump(mode="json")
        return OrganizationBundleMigrationResult(
            source_schema_version="1.0",
            target_schema_version="2.0",
            source_digest=canonical_sha256(source_payload),
            target_digest=canonical_sha256(target_payload),
            warnings=sorted(set(warnings)),
            bundle=migrated,
        )


def _portable(key: str, version: int, definition: dict[str, Any]) -> PortableDefinitionRevision:
    return PortableDefinitionRevision(
        key=key,
        version=version,
        lifecycle="draft",
        content_hash=canonical_definition_sha256(definition),
        definition=definition,
    )


def _role_template_definition(*, key, name, description, prompt_template, outputs):
    normalized_outputs = outputs or ["legacy_team_result"]
    lowered = name.casefold()
    accountability = (
        "product_owner" if "product owner" in lowered else "scrum_master" if "scrum master" in lowered else "developer"
    )
    return {
        "key": key,
        "version": 1,
        "extends": [],
        "scrum_accountability": accountability,
        "specialization": "legacy_bundle_compatibility",
        "mission": description or f"Execute the migrated legacy role {name}.",
        "scope": "Hub-delegated tasks inside the migrated legacy team boundary.",
        "responsibilities": ["execute_hub_delegated_task"],
        "inputs": ["hub_delegated_task"],
        "outputs": normalized_outputs,
        "decision_rights": ["return_result_to_hub"],
        "handoffs": ["hub_result_handoff"],
        "capability_policy": {
            "required": [],
            "optional": [],
            "forbidden": ["worker_orchestration"],
            "write_access_required": False,
        },
        "context_policy": {
            "allowed_scopes": ["task", "team"],
            "max_context_refs": 100,
            "source_allowlist_required": True,
        },
        "grounding_policy_ref": "legacy_bundle_grounding@1",
        "task_proposal_policy_ref": "legacy_bundle_task_proposals@1",
        "verification": {
            "required": False,
            "evidence_outputs": normalized_outputs,
            "gates": [],
            "independent_reviewer_required": False,
        },
        "escalation": {"target": "hub", "conditions": ["scope_or_authority_unclear"]},
        "prompt_template": prompt_template,
    }


def _compatibility_policy(key: str, policy_type: str) -> PortableDefinitionRevision:
    policy_body = {
        "compatibility_mode": "team_blueprint_bundle_v1",
        "hub_owned_orchestration": True,
        "workers_may_orchestrate": False,
    }
    definition = {
        "key": key,
        "version": 1,
        "policy_type": policy_type,
        "contract_ref": "ananta://organization-bundle-v2/legacy-team-compatibility",
        "content_digest": f"sha256:{canonical_sha256(policy_body)}",
    }
    return _portable(key, 1, definition)


def _artifact_kinds(bundle: TeamBlueprintBundle) -> list[str]:
    return sorted({_key(value.kind) for value in bundle.blueprint.artifacts if value.kind})


def _team_kind(bundle: TeamBlueprintBundle) -> str:
    value = str((bundle.bundle_metadata or {}).get("team_kind") or "").strip()
    if value in {"delivery", "research", "poc", "shared_service", "governance", "coordination"}:
        return value
    combined = f"{bundle.blueprint.name} {bundle.blueprint.base_team_type_name or ''}".casefold()
    if "research" in combined:
        return "research"
    if "proof" in combined or "poc" in combined:
        return "poc"
    if "govern" in combined or "security" in combined or "quality" in combined:
        return "governance"
    if "platform" in combined or "service" in combined:
        return "shared_service"
    if "portfolio" in combined or "coord" in combined:
        return "coordination"
    return "delivery"


def _key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    if not normalized:
        raise OrganizationBundleMigrationError("organization_bundle_v1_portable_key_invalid")
    return normalized[:191]


def _non_negative_int(value: Any, default: int) -> int:
    candidate = default if value is None else value
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise OrganizationBundleMigrationError("organization_bundle_v1_cardinality_invalid")
    return candidate


def _optional_positive_int(value: Any, default: int) -> int:
    candidate = default if value is None else value
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
        raise OrganizationBundleMigrationError("organization_bundle_v1_cardinality_invalid")
    return candidate


__all__ = [
    "OrganizationBundleMigrationError",
    "OrganizationBundleMigrationResult",
    "OrganizationBundleMigrationService",
]
