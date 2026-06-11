from __future__ import annotations

from typing import Any


def _build_cli_output_sections(
    *,
    diagnosis_artifact: dict[str, Any],
    repair_plan: dict[str, Any],
    verification_phase: dict[str, Any],
) -> dict[str, Any]:
    risk_summary = [
        {
            "action_id": action.get("id"),
            "risk_class": action.get("risk_class"),
            "advisory_class": action.get("advisory_class"),
            "requires_approval": action.get("requires_approval"),
            "rollback_supported": action.get("rollback_supported"),
            "rollback_hint": action.get("rollback_hint"),
            "caution_level": action.get("caution_level"),
        }
        for action in list(repair_plan.get("steps") or [])
    ]
    return {
        "schema": "admin_repair_cli_output_v1",
        "sections": [
            {
                "id": "diagnosis",
                "title": "Diagnosis",
                "content": {
                    "problem_class": diagnosis_artifact.get("problem_class"),
                    "confidence": diagnosis_artifact.get("confidence"),
                    "likely_causes": diagnosis_artifact.get("likely_causes"),
                },
            },
            {
                "id": "plan",
                "title": "Repair Plan",
                "content": {
                    "execution_scope": repair_plan.get("execution_scope"),
                    "step_count": len(list(repair_plan.get("steps") or [])),
                    "dry_run_default": repair_plan.get("dry_run_default"),
                },
            },
            {
                "id": "risk",
                "title": "Risk and Approval",
                "content": risk_summary,
            },
            {
                "id": "verification",
                "title": "Verification",
                "content": {
                    "result_state": verification_phase.get("result_state"),
                    "checks": verification_phase.get("checks"),
                },
            },
        ],
        "advisory_vs_enforced_visible": True,
    }


def _build_session_trail(
    *,
    evidence_contract: dict[str, Any],
    repair_plan: dict[str, Any],
    execution_session: dict[str, Any],
    verification_phase: dict[str, Any],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = [
        {
            "event": "evidence_collected",
            "sources": list(evidence_contract.get("selected_sources") or []),
            "bounded": True,
        },
        {
            "event": "plan_generated",
            "step_count": len(list(repair_plan.get("steps") or [])),
            "advisory_policy": repair_plan.get("advisory_policy"),
        },
    ]
    for step in list(execution_session.get("steps") or []):
        entries.append(
            {
                "event": "execution_step",
                "step_id": step.get("step_id"),
                "confirmation_required": bool(step.get("confirmation_required")),
                "state": step.get("state"),
            }
        )
    entries.append(
        {
            "event": "verification_phase",
            "check_count": len(list(verification_phase.get("checks") or [])),
            "result_state": verification_phase.get("result_state"),
        }
    )
    return {
        "schema": "admin_repair_session_trail_v1",
        "entries": entries,
        "sensitive_handling": {
            "redaction_enabled": True,
            "note": "sensitive_material_handling_explicit",
        },
    }


def _build_smoke_scenarios(
    *,
    platform_target: str,
    issue_symptom: str,
    evidence_sources: list[str],
    execution_scope: str,
) -> list[dict[str, Any]]:
    base = {
        "scenario_schema": "admin_repair_smoke_scenario_v1",
        "symptom": issue_symptom[:160],
        "evidence_sources": list(evidence_sources),
        "execution_scope": execution_scope,
        "expects_confirmation_gates": True,
    }
    windows_scenario = {
        **base,
        "scenario_id": "windows-fixture-smoke",
        "platform_target": "windows11",
        "fixture_profile": "windows_service_restart_loop",
    }
    ubuntu_scenario = {
        **base,
        "scenario_id": "ubuntu-fixture-smoke",
        "platform_target": "ubuntu",
        "fixture_profile": "ubuntu_service_unhealthy",
    }
    if platform_target == "windows11":
        return [windows_scenario, ubuntu_scenario]
    if platform_target == "ubuntu":
        return [ubuntu_scenario, windows_scenario]
    return [windows_scenario, ubuntu_scenario]


def _build_golden_paths(
    *,
    issue_symptom: str,
    evidence_sources: list[str],
    execution_scope: str,
) -> dict[str, Any]:
    def _flow(platform: str, fixture_profile: str) -> dict[str, Any]:
        return {
            "platform_target": platform,
            "reproducible": True,
            "fixture_supported": True,
            "fixture_profile": fixture_profile,
            "steps": [
                {
                    "id": "collect-evidence",
                    "label": "Collect bounded evidence",
                    "confirmation_gate": False,
                    "output": "diagnosis_artifact",
                },
                {
                    "id": "preview-plan",
                    "label": "Render dry-run repair preview",
                    "confirmation_gate": False,
                    "output": "repair_plan_preview",
                },
                {
                    "id": "confirm-execution",
                    "label": "Confirm bounded mutation-capable step",
                    "confirmation_gate": True,
                    "output": "execution_decision",
                },
                {
                    "id": "verify-result",
                    "label": "Run verification checks and classify result",
                    "confirmation_gate": False,
                    "output": "verification_phase",
                },
            ],
            "flow_summary": {
                "issue_symptom": issue_symptom[:160],
                "evidence_sources": list(evidence_sources),
                "execution_scope": execution_scope,
            },
        }

    return {
        "schema": "admin_repair_golden_paths_v1",
        "windows": _flow("windows11", "windows_service_restart_loop"),
        "ubuntu": _flow("ubuntu", "ubuntu_service_unhealthy"),
    }


def _build_future_extension_boundaries() -> dict[str, Any]:
    return {
        "schema": "admin_repair_extension_boundaries_v1",
        "mvp_scope": [
            "bounded_diagnosis",
            "bounded_repair_plan",
            "step_confirmed_execution",
            "post_repair_verification",
        ],
        "out_of_scope_domains": [
            "network_specific_repair_architecture",
            "container_orchestrator_specific_repair_architecture",
            "application_domain_specific_repair_architecture",
        ],
        "extension_policy": {
            "requires_shared_repair_action_schema": True,
            "forbid_parallel_repair_models": True,
            "keep_shared_foundation_maintainable": True,
        },
    }
