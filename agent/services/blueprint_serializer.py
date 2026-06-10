"""Serialization helpers for team blueprints, catalog items and work profiles.

Extracted from agent/routes/teams.py (SPLIT-012).
"""

from typing import Any

from agent.db_models import BlueprintArtifactDB, BlueprintRoleDB, TeamBlueprintDB
from agent.services.repository_registry import get_repository_registry
from agent.services.team_definition_version_service import enrich_blueprint_payload


def _repos():
    return get_repository_registry()


def _serialize_blueprint(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB] | None = None,
    artifacts: list[BlueprintArtifactDB] | None = None,
) -> dict:
    blueprint_dict = blueprint.model_dump()
    blueprint_roles = roles if roles is not None else _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
    blueprint_artifacts = artifacts if artifacts is not None else _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    blueprint_dict["roles"] = [role.model_dump() for role in blueprint_roles]
    blueprint_dict["artifacts"] = [artifact.model_dump() for artifact in blueprint_artifacts]
    # WFG-033: include the persisted workflow block. The
    # DB rows are the source of truth after a deploy; the
    # in-memory catalog is what the materialiser uses at
    # runtime. Both views are kept in sync by
    # team_blueprint_reconciliation_service.
    blueprint_dict["workflow"] = _serialize_blueprint_workflow(blueprint.id)
    return enrich_blueprint_payload(blueprint_dict, blueprint, blueprint_roles, blueprint_artifacts)


def _serialize_blueprint_workflow(blueprint_id: str) -> dict | None:
    """Return the workflow block for a blueprint as a dict.

    The function reads from
    ``blueprint_workflow_step_repo.get_by_blueprint`` (the
    authoritative, persisted view after a deploy). When
    the blueprint has no workflow steps, returns ``None``
    so the API consumer can distinguish "no workflow" from
    "empty workflow".
    """
    repo = getattr(_repos(), "blueprint_workflow_step_repo", None)
    if repo is None:
        return None
    rows = list(repo.get_by_blueprint(blueprint_id) or [])
    if not rows:
        return None
    return {
        "mode": "gated",
        "default_failure_policy": "manual",
        "steps": [
            {
                "id": r.step_id,
                "role": r.role_name,
                "task_kind": r.task_kind,
                "title": r.title,
                "description": r.description,
                "produces": list(r.produces or []),
                "consumes": list(r.consumes or []),
                "depends_on": list(r.depends_on or []),
                "gate": bool(r.gate),
                "checks": dict(r.checks or {}),
                "failure_policy": r.failure_policy,
                "required_capabilities": list(r.required_capabilities or []),
                "sort_order": int(r.sort_order),
            }
            for r in rows
        ],
    }


def _suggest_goal_modes_for_blueprint(blueprint: TeamBlueprintDB) -> list[str]:
    name = str(blueprint.name or "").strip().lower()
    team_type = str(blueprint.base_team_type_name or "").strip().lower()
    if name == "tdd" or team_type == "tdd":
        return ["code_fix", "admin_repair", "project_evolution", "code_review"]
    if "research-evolution" in name or "research-evolution" in team_type:
        return ["project_evolution", "repo_analysis", "doc_summary", "code_review"]
    if "repair" in name:
        return ["admin_repair", "code_fix", "docker_compose_repair", "runtime_repair", "sys_diag"]
    if "research" in name:
        return ["repo_analysis", "doc_summary", "doc_gen"]
    if "security" in name:
        return ["code_review", "repo_analysis", "sys_diag"]
    if "release" in name:
        return ["sys_diag", "doc_gen", "code_review"]
    if "scrum-opencode" in name or "opencode" in name:
        return ["code_fix", "code_review", "doc_gen", "docker_compose_repair"]
    if team_type == "kanban":
        return ["code_fix", "repo_analysis", "doc_gen"]
    return ["code_fix", "repo_analysis", "doc_gen", "sys_diag"]


def _suggest_playbooks_for_blueprint(blueprint: TeamBlueprintDB) -> list[str]:
    name = str(blueprint.name or "").strip().lower()
    if name == "tdd":
        return ["bugfix", "refactoring", "architecture_review"]
    if "research-evolution" in name:
        return ["architecture_review", "refactoring", "bugfix"]
    if "repair" in name:
        return ["incident", "bugfix"]
    if "security" in name:
        return ["architecture_review", "refactoring"]
    if "release" in name:
        return ["incident", "architecture_review"]
    if "research" in name:
        return ["architecture_review"]
    return ["bugfix", "refactoring", "incident", "architecture_review"]


def _build_blueprint_work_profile(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    capability_defaults: set[str] = set()
    execution_modes: set[str] = set()
    preferred_backends: set[str] = set()
    fallback_backends: set[str] = set()
    risk_profiles: set[str] = set()

    for role in roles:
        role_config = dict(role.config or {})
        for capability in role_config.get("capability_defaults") or []:
            normalized = str(capability).strip()
            if normalized:
                capability_defaults.add(normalized)
        execution_mode = str(role_config.get("execution_mode") or "").strip()
        if execution_mode:
            execution_modes.add(execution_mode)
        preferred_backend = str(role_config.get("preferred_backend") or "").strip()
        if preferred_backend:
            preferred_backends.add(preferred_backend)
        for backend in role_config.get("fallback_backends") or []:
            normalized_backend = str(backend).strip()
            if normalized_backend:
                fallback_backends.add(normalized_backend)
        risk_profile = str(role_config.get("risk_profile") or "").strip().lower()
        if risk_profile:
            risk_profiles.add(risk_profile)

    policy_artifacts = [
        {
            "title": artifact.title,
            "sort_order": artifact.sort_order,
            "payload": dict(artifact.payload or {}),
        }
        for artifact in artifacts
        if str(artifact.kind or "").strip().lower() == "policy"
    ]
    starter_artifacts = [
        {
            "kind": artifact.kind,
            "title": artifact.title,
            "description": artifact.description,
            "sort_order": artifact.sort_order,
            "payload": dict(artifact.payload or {}),
        }
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
    ]
    return {
        "blueprint_id": blueprint.id,
        "blueprint_name": blueprint.name,
        "base_team_type_name": blueprint.base_team_type_name,
        "goal_modes": _suggest_goal_modes_for_blueprint(blueprint),
        "playbooks": _suggest_playbooks_for_blueprint(blueprint),
        "recommended_action_pack_capabilities": sorted(capability_defaults),
        "execution_modes": sorted(execution_modes),
        "preferred_backends": sorted(preferred_backends),
        "fallback_backends": sorted(fallback_backends),
        "risk_profiles": sorted(risk_profiles),
        "policy_profiles": policy_artifacts,
        "starter_artifacts": starter_artifacts,
    }


STANDARD_BLUEPRINT_ORDER = [
    "Scrum",
    "Kanban",
    "Research",
    "Code-Repair",
    "TDD",
    "Security-Review",
    "Release-Prep",
    "Scrum-OpenCode",
    "Research-Evolution",
]

BLUEPRINT_PRODUCT_HINTS = {
    "scrum": {
        "intended_use": "Iterative delivery with explicit sprint roles and backlog ownership.",
        "when_to_use": "Use for feature-driven work with clear increments and sprint cadence.",
    },
    "kanban": {
        "intended_use": "Continuous flow delivery with WIP-aware operational prioritization.",
        "when_to_use": "Use for mixed incoming work where throughput and flow metrics matter.",
    },
    "research": {
        "intended_use": "Evidence-driven research and synthesis with source validation.",
        "when_to_use": "Use when outcome quality depends on source coverage and defensible synthesis.",
    },
    "code-repair": {
        "intended_use": "Incident triage, targeted patching, and regression containment.",
        "when_to_use": "Use for bugfix and repair work with minimal blast radius requirements.",
    },
    "tdd": {
        "intended_use": "Behavior-first implementation with explicit red/green/refactor evidence.",
        "when_to_use": "Use when test-first change control and verification traceability are required.",
    },
    "security-review": {
        "intended_use": "Security and compliance review with risk-focused controls validation.",
        "when_to_use": "Use before risky releases or for trust-boundary and policy-sensitive changes.",
    },
    "release-prep": {
        "intended_use": "Release-readiness orchestration with verification and rollback gates.",
        "when_to_use": "Use for pre-release coordination, go/no-go framing, and rollout safety checks.",
    },
    "scrum-opencode": {
        "intended_use": "Scrum delivery with explicit OpenCode/SGPT/terminal execution cascade.",
        "when_to_use": "Use for OpenCode-first teams needing deterministic tool/handoff expectations.",
    },
    "research-evolution": {
        "intended_use": "DeerFlow research followed by Evolver proposal with mandatory review gate.",
        "when_to_use": "Use for proposal-heavy evolution where research traceability is mandatory.",
    },
}


def _catalog_hint_for_blueprint(blueprint_name: str) -> dict[str, str]:
    normalized_name = str(blueprint_name or "").strip().lower()
    return BLUEPRINT_PRODUCT_HINTS.get(
        normalized_name,
        {
            "intended_use": "Reusable team definition with roles, start artifacts, and governance defaults.",
            "when_to_use": "Use when you want a repeatable startup path instead of assembling teams manually.",
        },
    )


def _catalog_expected_outputs(artifacts: list[BlueprintArtifactDB]) -> list[str]:
    starter_tasks = [
        str(artifact.title).strip()
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        if str(artifact.kind or "").strip().lower() == "task" and str(artifact.title or "").strip()
    ]
    if starter_tasks:
        return starter_tasks[:3]

    policy_titles = [
        str(artifact.title).strip()
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        if str(artifact.kind or "").strip().lower() == "policy" and str(artifact.title or "").strip()
    ]
    return policy_titles[:2] if policy_titles else ["Starter tasks and role-ready execution context"]


def _catalog_safety_review_stance(artifacts: list[BlueprintArtifactDB]) -> str:
    policy_payloads = [
        dict(artifact.payload or {})
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        if str(artifact.kind or "").strip().lower() == "policy"
    ]
    if not policy_payloads:
        return "balanced security, standard verification"

    policy = policy_payloads[0]
    security_level = str(policy.get("security_level") or "balanced").strip().lower()
    verification_required = bool(policy.get("verification_required", False))
    review_required = bool(policy.get("review_required", False))

    attributes = [f"{security_level} security"]
    if verification_required:
        attributes.append("verification required")
    if review_required:
        attributes.append("human review gate")
    return ", ".join(attributes)


def _governance_profile_from_work_profile(work_profile: dict[str, Any]) -> dict[str, str]:
    risk_profiles = {str(item).strip().lower() for item in (work_profile.get("risk_profiles") or []) if str(item).strip()}
    policy_profiles = work_profile.get("policy_profiles") or []
    strict_policy = any(str((item.get("payload") or {}).get("security_level") or "").strip().lower() == "strict" for item in policy_profiles)
    review_gate = any(bool((item.get("payload") or {}).get("review_required", False)) for item in policy_profiles)
    verification_required = any(bool((item.get("payload") or {}).get("verification_required", False)) for item in policy_profiles)

    if strict_policy or "strict" in risk_profiles:
        label = "Strict review profile"
        hint = "High-assurance flow with explicit review and controlled rollout expectations."
    elif verification_required or "high" in risk_profiles:
        label = "Balanced with verification"
        hint = "Execution remains delivery-oriented but expects explicit verification before closure."
    else:
        label = "Balanced default profile"
        hint = "Standard governance profile for regular delivery and iterative refinement."

    if review_gate and "review" not in hint.lower():
        hint = f"{hint} Includes a human review gate."
    return {"label": label, "hint": hint}


def _work_profile_summary(work_profile: dict[str, Any]) -> dict[str, Any]:
    governance = _governance_profile_from_work_profile(work_profile)
    return {
        "recommended_goal_modes": list(work_profile.get("goal_modes") or [])[:4],
        "playbook_hints": list(work_profile.get("playbooks") or [])[:4],
        "capability_hints": list(work_profile.get("recommended_action_pack_capabilities") or [])[:5],
        "governance_profile": governance,
    }


def _serialize_blueprint_catalog_item(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    hints = _catalog_hint_for_blueprint(blueprint.name)
    work_profile = _build_blueprint_work_profile(blueprint, roles, artifacts)
    short_description = str(blueprint.description or "").strip() or hints["intended_use"]
    return {
        "id": blueprint.id,
        "name": blueprint.name,
        "short_description": short_description,
        "intended_use": hints["intended_use"],
        "when_to_use": hints["when_to_use"],
        "expected_outputs": _catalog_expected_outputs(artifacts),
        "safety_review_stance": _catalog_safety_review_stance(artifacts),
        "work_profile_summary": _work_profile_summary(work_profile),
        "goal_modes": work_profile["goal_modes"],
        "playbooks": work_profile["playbooks"],
        "is_standard_blueprint": bool(blueprint.is_seed),
        "entry_recommended": bool(blueprint.is_seed),
    }


def _blueprint_catalog_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    name = str(item.get("name") or "")
    try:
        standard_rank = STANDARD_BLUEPRINT_ORDER.index(name)
    except ValueError:
        standard_rank = len(STANDARD_BLUEPRINT_ORDER) + 1
    is_standard = bool(item.get("is_standard_blueprint"))
    return (0 if is_standard else 1, standard_rank, name.lower())


def _user_lifecycle_state_from_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    drift_status = str(metadata.get("drift_status") or "").strip().lower()
    origin_kind = str(metadata.get("origin_kind") or "").strip().lower()
    customizations = dict(metadata.get("live_customizations") or {})
    role_templates_customized = bool(customizations.get("role_templates"))

    if drift_status == "in_sync":
        return {
            "code": "standard",
            "label": "Standard",
            "hint": "Dieses Team folgt dem aktuellen Blueprint-Stand.",
        }
    if drift_status == "drifted":
        return {
            "code": "outdated",
            "label": "Aktualisierbar",
            "hint": "Blueprint wurde aktualisiert; Team laeuft noch auf einem aelteren Stand.",
        }
    if origin_kind == "not_blueprint_based":
        return {
            "code": "customized",
            "label": "Individuell",
            "hint": "Dieses Team wurde direkt als laufende Instanz aufgebaut.",
        }
    if role_templates_customized:
        return {
            "code": "customized",
            "label": "Angepasst",
            "hint": "Das Team nutzt eigene Rollen-Template-Anpassungen.",
        }
    if origin_kind in {"seed_blueprint_instance", "custom_blueprint_instance"}:
        return {
            "code": "customized",
            "label": "Angepasst",
            "hint": "Blueprint-basierte Instanz mit eigener Laufzeitentwicklung.",
        }
    return {
        "code": "customized",
        "label": "Angepasst",
        "hint": "Laufende Team-Instanz mit individueller Auspraegung.",
    }
