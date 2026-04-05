from __future__ import annotations

from dataclasses import dataclass

from agent.db_models import TeamBlueprintDB, TeamDB
from agent.models import (
    BlueprintBundleDefinition,
    BlueprintBundleMemberAssignment,
    BlueprintBundleRoleDefinition,
    BlueprintBundleTeamDefinition,
    BlueprintBundleTemplate,
    BlueprintArtifactDefinition,
    TeamBlueprintBundle,
)

BUNDLE_SCHEMA_VERSION = "1.0"
ALLOWED_BUNDLE_MODES = {"full", "split"}
ALLOWED_BUNDLE_PARTS = {"blueprint", "templates", "team"}
ALLOWED_CONFLICT_STRATEGIES = {"fail", "skip", "overwrite"}
ALLOWED_BLUEPRINT_ARTIFACT_KINDS = {"task", "policy"}


@dataclass(frozen=True)
class BundleImportPlan:
    schema_version: str
    mode: str
    conflict_strategy: str
    parts: list[str]
    diff: dict
    summary: dict
    errors: list[dict]
    template_specs: list[dict]
    blueprint_spec: dict | None
    team_spec: dict | None


def normalize_bundle_mode(value: str | None) -> str:
    return (value or "full").strip().lower()


def normalize_bundle_parts(parts: list[str] | None, fallback_parts: list[str]) -> list[str]:
    normalized: list[str] = []
    source = parts if parts else fallback_parts
    for part in source:
        candidate = (part or "").strip().lower()
        if not candidate or candidate in normalized:
            continue
        normalized.append(candidate)
    return normalized


def validate_bundle_mode_and_parts(mode: str, parts: list[str]) -> list[dict]:
    errors: list[dict] = []
    if mode not in ALLOWED_BUNDLE_MODES:
        errors.append(
            {
                "type": "validation",
                "message": "unsupported_bundle_mode",
                "details": {"mode": mode, "allowed_modes": sorted(ALLOWED_BUNDLE_MODES)},
            }
        )
    invalid_parts = [part for part in parts if part not in ALLOWED_BUNDLE_PARTS]
    if invalid_parts:
        errors.append(
            {
                "type": "validation",
                "message": "unsupported_bundle_parts",
                "details": {"parts": invalid_parts, "allowed_parts": sorted(ALLOWED_BUNDLE_PARTS)},
            }
        )
    return errors


def export_blueprint_bundle(repos, blueprint: TeamBlueprintDB, *, team: TeamDB | None = None, include_members: bool = False, mode: str = "full", parts: list[str] | None = None) -> dict:
    normalized_mode = normalize_bundle_mode(mode)
    base_parts = ["blueprint", "templates"]
    if team is not None:
        base_parts.append("team")
    normalized_parts = normalize_bundle_parts(parts, base_parts if normalized_mode == "split" else base_parts)

    blueprint_roles = repos.blueprint_role_repo.get_by_blueprint(blueprint.id)
    blueprint_artifacts = repos.blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    role_by_id = {role.id: role for role in repos.role_repo.get_all()}
    team_members = repos.team_member_repo.get_by_team(team.id) if team is not None and include_members else []
    team_type = repos.team_type_repo.get_by_id(team.team_type_id) if team is not None and team.team_type_id else None

    referenced_template_ids: set[str] = set()
    blueprint_section = None
    if "templates" in normalized_parts:
        referenced_template_ids.update(role.template_id for role in blueprint_roles if role.template_id)
    if "blueprint" in normalized_parts:
        blueprint_section = BlueprintBundleDefinition(
            name=blueprint.name,
            description=blueprint.description,
            base_team_type_name=blueprint.base_team_type_name,
            roles=[
                BlueprintBundleRoleDefinition(
                    name=role.name,
                    description=role.description,
                    template_name=repos.template_repo.get_by_id(role.template_id).name if role.template_id and repos.template_repo.get_by_id(role.template_id) else None,
                    sort_order=role.sort_order,
                    is_required=role.is_required,
                    config=role.config or {},
                )
                for role in blueprint_roles
            ],
            artifacts=[
                BlueprintArtifactDefinition(
                    kind=artifact.kind,
                    title=artifact.title,
                    description=artifact.description,
                    sort_order=artifact.sort_order,
                    payload=artifact.payload or {},
                )
                for artifact in blueprint_artifacts
            ],
        )
        referenced_template_ids.update(role.template_id for role in blueprint_roles if role.template_id)

    team_section = None
    if team is not None and "team" in normalized_parts:
        role_templates: dict[str, str] = {}
        for role_id, template_id in (team.role_templates or {}).items():
            role = role_by_id.get(role_id)
            template = repos.template_repo.get_by_id(template_id) if template_id else None
            if role and template:
                role_templates[role.name] = template.name
                referenced_template_ids.add(template.id)

        members: list[BlueprintBundleMemberAssignment] = []
        if include_members:
            blueprint_role_by_id = {role.id: role for role in blueprint_roles}
            for member in team_members:
                role = role_by_id.get(member.role_id)
                blueprint_role = blueprint_role_by_id.get(member.blueprint_role_id) if member.blueprint_role_id else None
                template = repos.template_repo.get_by_id(member.custom_template_id) if member.custom_template_id else None
                if template:
                    referenced_template_ids.add(template.id)
                members.append(
                    BlueprintBundleMemberAssignment(
                        agent_url=member.agent_url,
                        role_name=role.name if role else None,
                        blueprint_role_name=blueprint_role.name if blueprint_role else None,
                        custom_template_name=template.name if template else None,
                    )
                )

        team_section = BlueprintBundleTeamDefinition(
            name=team.name,
            description=team.description,
            team_type_name=team_type.name if team_type else None,
            blueprint_name=blueprint.name,
            is_active=team.is_active,
            role_templates=role_templates,
            members=members,
        )

    template_section = []
    if "templates" in normalized_parts:
        template_section = [
            BlueprintBundleTemplate(
                name=template.name,
                description=template.description,
                prompt_template=template.prompt_template,
            )
            for template in sorted(
                [repos.template_repo.get_by_id(template_id) for template_id in referenced_template_ids if template_id],
                key=lambda item: item.name if item else "",
            )
            if template is not None
        ]

    return TeamBlueprintBundle(
        schema_version=BUNDLE_SCHEMA_VERSION,
        mode=normalized_mode,
        parts=normalized_parts if normalized_mode == "split" else base_parts,
        blueprint=blueprint_section,
        templates=template_section,
        team=team_section,
        bundle_metadata={
            "blueprint_id": blueprint.id,
            "team_id": team.id if team is not None else None,
            "include_members": include_members,
        },
    ).model_dump()


def build_bundle_import_plan(repos, bundle: TeamBlueprintBundle, conflict_strategy: str) -> BundleImportPlan:
    normalized_mode = normalize_bundle_mode(bundle.mode)
    normalized_strategy = (conflict_strategy or "fail").strip().lower()
    available_parts = []
    if bundle.blueprint is not None:
        available_parts.append("blueprint")
    if bundle.templates:
        available_parts.append("templates")
    if bundle.team is not None:
        available_parts.append("team")
    parts = normalize_bundle_parts(bundle.parts, available_parts)
    diff = {"templates": [], "blueprints": [], "teams": []}
    errors = validate_bundle_mode_and_parts(normalized_mode, parts)
    template_specs: list[dict] = []
    blueprint_spec = None
    team_spec = None

    if bundle.schema_version != BUNDLE_SCHEMA_VERSION:
        errors.append(
            {
                "type": "validation",
                "message": "unsupported_bundle_schema_version",
                "details": {"schema_version": bundle.schema_version, "supported": BUNDLE_SCHEMA_VERSION},
            }
        )
    if normalized_strategy not in ALLOWED_CONFLICT_STRATEGIES:
        errors.append(
            {
                "type": "validation",
                "message": "unsupported_conflict_strategy",
                "details": {"conflict_strategy": normalized_strategy, "allowed": sorted(ALLOWED_CONFLICT_STRATEGIES)},
            }
        )
    if normalized_mode == "full" and bundle.blueprint is None:
        errors.append(
            {
                "type": "validation",
                "message": "blueprint_required_for_full_bundle",
                "details": {"parts": parts},
            }
        )
    for part in parts:
        if part == "blueprint" and bundle.blueprint is None:
            errors.append({"type": "validation", "message": "bundle_part_missing", "details": {"part": "blueprint"}})
        if part == "templates" and not bundle.templates:
            errors.append({"type": "validation", "message": "bundle_part_missing", "details": {"part": "templates"}})
        if part == "team" and bundle.team is None:
            errors.append({"type": "validation", "message": "bundle_part_missing", "details": {"part": "team"}})

    bundled_templates_by_name: dict[str, BlueprintBundleTemplate] = {}
    for template in bundle.templates:
        normalized_name = (template.name or "").strip()
        if not normalized_name:
            errors.append({"type": "validation", "message": "bundle_template_name_required", "details": {}})
            continue
        key = normalized_name.lower()
        if key in bundled_templates_by_name:
            errors.append(
                {
                    "type": "validation",
                    "message": "duplicate_bundle_template_name",
                    "details": {"name": normalized_name},
                }
            )
            continue
        bundled_templates_by_name[key] = template

    if "templates" in parts:
        for template in bundle.templates:
            existing = repos.template_repo.get_by_name(template.name.strip())
            changes = []
            if existing is not None:
                if existing.description != template.description:
                    changes.append("description")
                if existing.prompt_template != template.prompt_template:
                    changes.append("prompt_template")
            if existing is None:
                action = "create"
            elif normalized_strategy == "fail":
                action = "conflict"
            elif normalized_strategy == "skip":
                action = "skip"
            elif changes:
                action = "update"
            else:
                action = "unchanged"
            spec = {"name": template.name.strip(), "existing": existing, "bundle": template, "action": action, "changes": changes}
            template_specs.append(spec)
            diff["templates"].append({"name": spec["name"], "action": action, "changes": changes})
            if action == "conflict":
                errors.append(
                    {
                        "type": "conflict",
                        "message": "template_name_exists",
                        "details": {"name": template.name.strip()},
                    }
                )

    if "blueprint" in parts and bundle.blueprint is not None:
        blueprint_errors = _validate_blueprint_bundle_definition(bundle.blueprint)
        errors.extend(blueprint_errors)
        for role in bundle.blueprint.roles:
            if not role.template_name:
                continue
            template_exists = role.template_name.strip().lower() in bundled_templates_by_name or repos.template_repo.get_by_name(role.template_name.strip()) is not None
            if not template_exists:
                errors.append(
                    {
                        "type": "validation",
                        "message": "template_not_found",
                        "details": {"template_name": role.template_name.strip(), "role_name": role.name.strip()},
                    }
                )
        existing_blueprint = repos.team_blueprint_repo.get_by_name(bundle.blueprint.name.strip())
        changes = []
        if existing_blueprint is not None:
            existing_payload = _existing_blueprint_payload(repos, existing_blueprint)
            incoming_payload = _incoming_blueprint_payload(bundle.blueprint)
            for field in ("description", "base_team_type_name", "roles", "artifacts"):
                if existing_payload[field] != incoming_payload[field]:
                    changes.append(field)
        if existing_blueprint is None:
            action = "create"
        elif normalized_strategy == "fail":
            action = "conflict"
        elif normalized_strategy == "skip":
            action = "skip"
        elif changes:
            action = "update"
        else:
            action = "unchanged"
        blueprint_spec = {
            "name": bundle.blueprint.name.strip(),
            "existing": existing_blueprint,
            "bundle": bundle.blueprint,
            "action": action,
            "changes": changes,
        }
        diff["blueprints"].append({"name": blueprint_spec["name"], "action": action, "changes": changes})
        if action == "conflict":
            errors.append(
                {
                    "type": "conflict",
                    "message": "blueprint_name_exists",
                    "details": {"name": bundle.blueprint.name.strip()},
                }
            )

    if "team" in parts and bundle.team is not None:
        team_errors = _validate_team_bundle_definition(repos, bundle, bundled_templates_by_name)
        errors.extend(team_errors)
        existing_team = repos.team_repo.get_by_name(bundle.team.name.strip())
        blueprint_name = (bundle.team.blueprint_name or (bundle.blueprint.name if bundle.blueprint else "")).strip() or None
        include_members = _bundle_includes_members(bundle)
        changes = []
        if existing_team is not None:
            existing_payload = _existing_team_payload(repos, existing_team)
            incoming_payload = _incoming_team_payload(bundle.team, blueprint_name=blueprint_name, include_members=include_members)
            fields = ["description", "team_type_name", "blueprint_name", "role_templates", "is_active"]
            if include_members:
                fields.append("members")
            for field in fields:
                if existing_payload[field] != incoming_payload[field]:
                    changes.append(field)
        if existing_team is None:
            action = "create"
        elif normalized_strategy == "fail":
            action = "conflict"
        elif normalized_strategy == "skip":
            action = "skip"
        elif changes:
            action = "update"
        else:
            action = "unchanged"
        team_spec = {
            "name": bundle.team.name.strip(),
            "existing": existing_team,
            "bundle": bundle.team,
            "action": action,
            "changes": changes,
            "blueprint_name": blueprint_name,
            "include_members": include_members,
        }
        diff["teams"].append({"name": team_spec["name"], "action": action, "changes": changes})
        if action == "conflict":
            errors.append(
                {
                    "type": "conflict",
                    "message": "team_name_exists",
                    "details": {"name": bundle.team.name.strip()},
                }
            )

    summary = _build_summary(diff)
    return BundleImportPlan(
        schema_version=bundle.schema_version,
        mode=normalized_mode,
        conflict_strategy=normalized_strategy,
        parts=parts,
        diff=diff,
        summary=summary,
        errors=errors,
        template_specs=template_specs,
        blueprint_spec=blueprint_spec,
        team_spec=team_spec,
    )


def _build_summary(diff: dict) -> dict:
    counts = {"create": 0, "update": 0, "skip": 0, "unchanged": 0, "conflict": 0}
    for items in diff.values():
        for item in items:
            counts[item["action"]] = counts.get(item["action"], 0) + 1
    counts["total"] = sum(counts.values())
    return counts


def _validate_blueprint_bundle_definition(blueprint: BlueprintBundleDefinition) -> list[dict]:
    errors: list[dict] = []
    if not (blueprint.name or "").strip():
        errors.append({"type": "validation", "message": "blueprint_name_required", "details": {}})
    seen_role_names: set[str] = set()
    seen_role_orders: set[int] = set()
    for role in blueprint.roles:
        normalized_name = (role.name or "").strip()
        if not normalized_name:
            errors.append({"type": "validation", "message": "blueprint_role_name_required", "details": {}})
            continue
        lowered = normalized_name.lower()
        if lowered in seen_role_names:
            errors.append(
                {
                    "type": "validation",
                    "message": "duplicate_blueprint_role_name",
                    "details": {"role_name": normalized_name},
                }
            )
        seen_role_names.add(lowered)
        if role.sort_order in seen_role_orders:
            errors.append(
                {
                    "type": "validation",
                    "message": "duplicate_blueprint_role_sort_order",
                    "details": {"sort_order": role.sort_order},
                }
            )
        seen_role_orders.add(role.sort_order)
        role_config = dict(role.config or {})
        capability_defaults = role_config.get("capability_defaults")
        risk_profile = role_config.get("risk_profile")
        verification_defaults = role_config.get("verification_defaults")
        if capability_defaults is not None and not isinstance(capability_defaults, list):
            errors.append(
                {
                    "type": "validation",
                    "message": "blueprint_role_capability_defaults_invalid",
                    "details": {"role_name": normalized_name},
                }
            )
        if risk_profile is not None and str(risk_profile).strip().lower() not in {"low", "balanced", "high", "strict"}:
            errors.append(
                {
                    "type": "validation",
                    "message": "blueprint_role_risk_profile_invalid",
                    "details": {"role_name": normalized_name},
                }
            )
        if verification_defaults is not None and not isinstance(verification_defaults, dict):
            errors.append(
                {
                    "type": "validation",
                    "message": "blueprint_role_verification_defaults_invalid",
                    "details": {"role_name": normalized_name},
                }
            )

    seen_artifact_titles: set[str] = set()
    seen_artifact_orders: set[int] = set()
    for artifact in blueprint.artifacts:
        normalized_kind = (artifact.kind or "").strip().lower()
        normalized_title = (artifact.title or "").strip()
        if normalized_kind not in ALLOWED_BLUEPRINT_ARTIFACT_KINDS:
            errors.append(
                {
                    "type": "validation",
                    "message": "blueprint_artifact_kind_invalid",
                    "details": {"kind": artifact.kind, "allowed_kinds": sorted(ALLOWED_BLUEPRINT_ARTIFACT_KINDS)},
                }
            )
        if not normalized_title:
            errors.append({"type": "validation", "message": "blueprint_artifact_title_required", "details": {}})
            continue
        lowered = normalized_title.lower()
        if lowered in seen_artifact_titles:
            errors.append(
                {
                    "type": "validation",
                    "message": "duplicate_blueprint_artifact_title",
                    "details": {"title": normalized_title},
                }
            )
        seen_artifact_titles.add(lowered)
        if artifact.sort_order in seen_artifact_orders:
            errors.append(
                {
                    "type": "validation",
                    "message": "duplicate_blueprint_artifact_sort_order",
                    "details": {"sort_order": artifact.sort_order},
                }
            )
        seen_artifact_orders.add(artifact.sort_order)
    return errors


def _validate_team_bundle_definition(repos, bundle: TeamBlueprintBundle, bundled_templates_by_name: dict[str, BlueprintBundleTemplate]) -> list[dict]:
    errors: list[dict] = []
    team = bundle.team
    if team is None:
        return errors
    if not (team.name or "").strip():
        errors.append({"type": "validation", "message": "team_name_required", "details": {}})
    blueprint_name = (team.blueprint_name or (bundle.blueprint.name if bundle.blueprint else "")).strip()
    if not blueprint_name:
        errors.append({"type": "validation", "message": "blueprint_name_required_for_team_bundle", "details": {}})
    elif bundle.blueprint is None and repos.team_blueprint_repo.get_by_name(blueprint_name) is None:
        errors.append(
            {
                "type": "validation",
                "message": "blueprint_not_found",
                "details": {"blueprint_name": blueprint_name},
            }
        )
    team_type_name = (team.team_type_name or (bundle.blueprint.base_team_type_name if bundle.blueprint else "") or "").strip()
    if team_type_name and repos.team_type_repo.get_by_name(team_type_name) is None:
        errors.append(
            {
                "type": "validation",
                "message": "team_type_not_found",
                "details": {"team_type_name": team_type_name},
            }
        )
    for role_name, template_name in (team.role_templates or {}).items():
        if repos.role_repo.get_by_name((role_name or "").strip()) is None:
            errors.append(
                {
                    "type": "validation",
                    "message": "role_not_found",
                    "details": {"role_name": role_name},
                }
            )
        if not template_name:
            errors.append(
                {
                    "type": "validation",
                    "message": "template_name_required",
                    "details": {"role_name": role_name},
                }
            )
            continue
        normalized_template_name = template_name.strip().lower()
        if normalized_template_name not in bundled_templates_by_name and repos.template_repo.get_by_name(template_name.strip()) is None:
            errors.append(
                {
                    "type": "validation",
                    "message": "template_not_found",
                    "details": {"template_name": template_name.strip(), "role_name": role_name},
                }
            )
    if _bundle_includes_members(bundle):
        for member in team.members:
            if not (member.role_name or "").strip():
                errors.append(
                    {
                        "type": "validation",
                        "message": "role_name_required",
                        "details": {"agent_url": member.agent_url},
                    }
                )
            elif repos.role_repo.get_by_name(member.role_name.strip()) is None:
                errors.append(
                    {
                        "type": "validation",
                        "message": "role_not_found",
                        "details": {"role_name": member.role_name.strip()},
                    }
                )
            if member.custom_template_name:
                normalized_template_name = member.custom_template_name.strip().lower()
                if normalized_template_name not in bundled_templates_by_name and repos.template_repo.get_by_name(member.custom_template_name.strip()) is None:
                    errors.append(
                        {
                            "type": "validation",
                            "message": "template_not_found",
                            "details": {"template_name": member.custom_template_name.strip(), "agent_url": member.agent_url},
                        }
                    )
        if bundle.blueprint is not None:
            blueprint_role_names = {role.name.strip().lower() for role in bundle.blueprint.roles}
            for member in team.members:
                if member.blueprint_role_name and member.blueprint_role_name.strip().lower() not in blueprint_role_names:
                    errors.append(
                        {
                            "type": "validation",
                            "message": "blueprint_role_not_found",
                            "details": {"blueprint_role_name": member.blueprint_role_name.strip()},
                        }
                    )
    return errors


def _existing_blueprint_payload(repos, blueprint: TeamBlueprintDB) -> dict:
    roles = repos.blueprint_role_repo.get_by_blueprint(blueprint.id)
    artifacts = repos.blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    return {
        "description": blueprint.description,
        "base_team_type_name": blueprint.base_team_type_name,
        "roles": [
            {
                "name": role.name,
                "description": role.description,
                "template_name": repos.template_repo.get_by_id(role.template_id).name if role.template_id and repos.template_repo.get_by_id(role.template_id) else None,
                "sort_order": role.sort_order,
                "is_required": role.is_required,
                "config": role.config or {},
            }
            for role in roles
        ],
        "artifacts": [
            {
                "kind": artifact.kind,
                "title": artifact.title,
                "description": artifact.description,
                "sort_order": artifact.sort_order,
                "payload": artifact.payload or {},
            }
            for artifact in artifacts
        ],
    }


def _incoming_blueprint_payload(blueprint: BlueprintBundleDefinition) -> dict:
    return {
        "description": blueprint.description,
        "base_team_type_name": blueprint.base_team_type_name,
        "roles": [
            {
                "name": role.name,
                "description": role.description,
                "template_name": role.template_name,
                "sort_order": role.sort_order,
                "is_required": role.is_required,
                "config": role.config or {},
            }
            for role in blueprint.roles
        ],
        "artifacts": [
            {
                "kind": artifact.kind,
                "title": artifact.title,
                "description": artifact.description,
                "sort_order": artifact.sort_order,
                "payload": artifact.payload or {},
            }
            for artifact in blueprint.artifacts
        ],
    }


def _existing_team_payload(repos, team: TeamDB) -> dict:
    team_type = repos.team_type_repo.get_by_id(team.team_type_id) if team.team_type_id else None
    blueprint = repos.team_blueprint_repo.get_by_id(team.blueprint_id) if team.blueprint_id else None
    members = repos.team_member_repo.get_by_team(team.id)
    role_templates = {}
    for role_id, template_id in (team.role_templates or {}).items():
        role = repos.role_repo.get_by_id(role_id)
        template = repos.template_repo.get_by_id(template_id) if template_id else None
        if role and template:
            role_templates[role.name] = template.name
    normalized_members = []
    for member in members:
        role = repos.role_repo.get_by_id(member.role_id)
        blueprint_role = repos.blueprint_role_repo.get_by_id(member.blueprint_role_id) if member.blueprint_role_id else None
        template = repos.template_repo.get_by_id(member.custom_template_id) if member.custom_template_id else None
        normalized_members.append(
            {
                "agent_url": member.agent_url,
                "role_name": role.name if role else None,
                "blueprint_role_name": blueprint_role.name if blueprint_role else None,
                "custom_template_name": template.name if template else None,
            }
        )
    normalized_members.sort(key=lambda item: (item["agent_url"], item.get("role_name") or ""))
    return {
        "description": team.description,
        "team_type_name": team_type.name if team_type else None,
        "blueprint_name": blueprint.name if blueprint else None,
        "role_templates": role_templates,
        "members": normalized_members,
        "is_active": team.is_active,
    }


def _incoming_team_payload(team: BlueprintBundleTeamDefinition, *, blueprint_name: str | None, include_members: bool) -> dict:
    normalized_members = [
        {
            "agent_url": member.agent_url,
            "role_name": member.role_name,
            "blueprint_role_name": member.blueprint_role_name,
            "custom_template_name": member.custom_template_name,
        }
        for member in team.members
    ]
    normalized_members.sort(key=lambda item: (item["agent_url"], item.get("role_name") or ""))
    return {
        "description": team.description,
        "team_type_name": team.team_type_name,
        "blueprint_name": blueprint_name,
        "role_templates": dict(team.role_templates or {}),
        "members": normalized_members if include_members else [],
        "is_active": team.is_active,
    }


def _bundle_includes_members(bundle: TeamBlueprintBundle) -> bool:
    return bool((bundle.bundle_metadata or {}).get("include_members")) or bool(bundle.team and bundle.team.members)
