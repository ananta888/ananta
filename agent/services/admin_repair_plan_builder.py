from __future__ import annotations

from typing import Any

from agent.services.admin_repair_taxonomy import (
    ACTION_BEHAVIOR,
    ACTION_RISK_CLASS,
    PROBLEM_TAXONOMY,
)


def _advisory_class_for_action(*, risk_class: str, mutation_candidate: bool) -> str:
    if risk_class == "high" and mutation_candidate:
        return "blocked"
    if mutation_candidate or risk_class == "medium":
        return "confirm-required"
    return "allow"


def _repair_action_for_problem(problem_class: str) -> str:
    return {
        "service_health": "restart_service",
        "package_runtime": "package_state_repair",
        "path_resolution": "path_repair",
        "permissions": "permission_fix",
        "port_binding": "port_conflict_resolution",
        "container_runtime": "container_runtime_repair",
    }.get(problem_class, "bounded_runtime_reset")


def _build_repair_action(
    *,
    action_id: str,
    title: str,
    repair_action_class: str,
    affected_targets: list[str],
    evidence_sources: list[str],
    execution_scope: str,
    mutation_candidate: bool,
    dry_run_supported: bool,
    expected_verification: str,
) -> dict[str, Any]:
    risk_class = ACTION_RISK_CLASS.get(repair_action_class, "medium")
    behavior = ACTION_BEHAVIOR.get(
        repair_action_class,
        {"rollback_supported": False, "rollback_hint": "manual_recovery_required"},
    )
    advisory_class = _advisory_class_for_action(risk_class=risk_class, mutation_candidate=mutation_candidate)
    caution_level = "high" if (risk_class == "high" or advisory_class == "blocked") else ("medium" if advisory_class == "confirm-required" else "low")
    return {
        "id": action_id,
        "title": title,
        "risk_class": risk_class,
        "requires_approval": advisory_class in {"confirm-required", "blocked"},
        "dry_run_supported": dry_run_supported,
        "verification_required": True,
        "mutation_candidate": mutation_candidate,
        "evidence_sources": list(evidence_sources),
        "execution_scope": execution_scope,
        "audit_hint": f"admin_repair:{repair_action_class}",
        "repair_action_class": repair_action_class,
        "affected_targets": list(affected_targets),
        "expected_verification": expected_verification,
        "advisory_class": advisory_class,
        "rollback_supported": bool(behavior["rollback_supported"]),
        "rollback_hint": str(behavior["rollback_hint"]),
        "caution_level": caution_level,
    }


def _build_repair_plan(
    *,
    diagnosis_artifact: dict[str, Any],
    affected_targets: list[str],
    evidence_sources: list[str],
    execution_scope: str,
    dry_run_default: bool,
) -> dict[str, Any]:
    problem_class = str(diagnosis_artifact.get("problem_class") or "service_health")
    verification_hint = str(PROBLEM_TAXONOMY.get(problem_class, {}).get("verification_hint") or "verification_checks_pass")
    actions: list[dict[str, Any]] = [
        _build_repair_action(
            action_id="repair-action-01",
            title="Collect bounded inspection snapshot",
            repair_action_class="inspect_state",
            affected_targets=affected_targets,
            evidence_sources=evidence_sources,
            execution_scope=execution_scope,
            mutation_candidate=False,
            dry_run_supported=True,
            expected_verification="inspection_snapshot_available",
        ),
        _build_repair_action(
            action_id="repair-action-02",
            title="Generate dry-run preview for selected repair path",
            repair_action_class="preview_mutation_plan",
            affected_targets=affected_targets,
            evidence_sources=evidence_sources,
            execution_scope=execution_scope,
            mutation_candidate=False,
            dry_run_supported=True,
            expected_verification="preview_contains_explicit_steps_and_targets",
        ),
    ]

    if execution_scope == "bounded_repair":
        action_class = _repair_action_for_problem(problem_class)
        actions.append(
            _build_repair_action(
                action_id="repair-action-03",
                title="Execute bounded repair step after explicit confirmation",
                repair_action_class=action_class,
                affected_targets=affected_targets,
                evidence_sources=evidence_sources,
                execution_scope=execution_scope,
                mutation_candidate=True,
                dry_run_supported=True,
                expected_verification=verification_hint,
            )
        )
        actions.append(
            _build_repair_action(
                action_id="repair-action-04",
                title="Run post-repair verification checks",
                repair_action_class="verification_check",
                affected_targets=affected_targets,
                evidence_sources=evidence_sources,
                execution_scope=execution_scope,
                mutation_candidate=False,
                dry_run_supported=False,
                expected_verification=verification_hint,
            )
        )
    else:
        actions.append(
            _build_repair_action(
                action_id="repair-action-03",
                title="Emit diagnosis-only follow-up recommendation",
                repair_action_class="diagnosis_only_followup",
                affected_targets=affected_targets,
                evidence_sources=evidence_sources,
                execution_scope=execution_scope,
                mutation_candidate=False,
                dry_run_supported=True,
                expected_verification="operator_review_acknowledges_next_steps",
            )
        )

    return {
        "schema": "admin_repair_plan_v1",
        "dry_run_default": dry_run_default,
        "execution_scope": execution_scope,
        "steps": actions,
        "result_states": ["resolved", "improved", "unchanged", "regressed"],
        "advisory_policy": "classification_is_hook_ready_and_not_full_kritis_enforcement",
    }


def _build_rollback_and_caution_model(repair_plan: dict[str, Any]) -> dict[str, Any]:
    reversible_actions: list[str] = []
    non_reversible_actions: list[str] = []
    caution_messages: list[dict[str, Any]] = []
    for action in list(repair_plan.get("steps") or []):
        action_id = str(action.get("id") or "")
        action_title = str(action.get("title") or "")
        rollback_supported = bool(action.get("rollback_supported"))
        if rollback_supported:
            reversible_actions.append(action_id)
        else:
            non_reversible_actions.append(action_id)
        is_high_risk = bool(action.get("mutation_candidate")) and str(action.get("caution_level") or "") == "high"
        if is_high_risk or not rollback_supported:
            caution_messages.append(
                {
                    "action_id": action_id,
                    "title": action_title,
                    "risk_class": action.get("risk_class"),
                    "caution_level": action.get("caution_level"),
                    "rollback_supported": rollback_supported,
                    "rollback_hint": action.get("rollback_hint"),
                    "message": "High-risk mutation action requires explicit caution; never present as safe when rollback is missing.",
                }
            )
    return {
        "schema": "admin_repair_rollback_caution_v1",
        "reversible_action_ids": reversible_actions,
        "non_reversible_action_ids": non_reversible_actions,
        "caution_messages": caution_messages,
        "safe_presentation_guard": {
            "enabled": True,
            "rule": "no_safe_label_when_high_risk_or_rollback_missing",
        },
    }
