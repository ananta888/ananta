from __future__ import annotations

from typing import Any

from agent.services.admin_repair_taxonomy import (
    ALLOWED_EVIDENCE_SOURCES,
    KRITIS_HOOK_FIELDS,
    PROBLEM_TAXONOMY,
    SUPPORTED_PLATFORM_PROFILES,
)
from agent.services.admin_repair_normalizers import (
    _normalize_evidence_sources,
    _normalize_execution_scope,
    _normalize_platform_target,
    _normalize_targets,
    _to_bool,
)
from agent.services.admin_repair_diagnosis import _build_diagnosis_artifact
from agent.services.admin_repair_plan_builder import (
    _build_repair_plan,
    _build_rollback_and_caution_model,
)
from agent.services.admin_repair_execution import (
    _build_environment_summary,
    _build_execution_session_template,
    _build_hardening_bridge_contract,
    _build_platform_evidence_adapters,
    _build_platform_playbooks,
    _build_verification_phase,
    _resolve_platform_detection,
)
from agent.services.admin_repair_presentation import (
    _build_cli_output_sections,
    _build_future_extension_boundaries,
    _build_golden_paths,
    _build_session_trail,
    _build_smoke_scenarios,
)
from agent.services.deterministic_repair_path_service import build_deterministic_repair_foundation_snapshot


def build_admin_repair_mode_data(mode_data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(mode_data or {})
    issue_symptom = str(normalized.get("issue_symptom") or "").strip() or "unspecified_admin_issue"
    platform_detection = _resolve_platform_detection(normalized)
    execution_scope = _normalize_execution_scope(
        normalized.get("execution_scope"),
        platform_supported=bool(platform_detection.get("supported")),
    )
    dry_run_default = _to_bool(normalized.get("dry_run"), default=True)
    evidence_sources = _normalize_evidence_sources(normalized.get("evidence_sources"))
    affected_targets = _normalize_targets(normalized.get("affected_targets"))
    if not affected_targets:
        affected_targets = ["service_runtime"]

    diagnosis_artifact = _build_diagnosis_artifact(
        issue_symptom=issue_symptom,
        affected_targets=affected_targets,
        evidence_sources=evidence_sources,
    )
    repair_plan = _build_repair_plan(
        diagnosis_artifact=diagnosis_artifact,
        affected_targets=affected_targets,
        evidence_sources=evidence_sources,
        execution_scope=execution_scope,
        dry_run_default=dry_run_default,
    )
    rollback_caution_model = _build_rollback_and_caution_model(repair_plan)
    platform_adapters = _build_platform_evidence_adapters(
        platform_target=platform_detection["platform_target"],
        evidence_sources=evidence_sources,
        issue_symptom=issue_symptom,
    )
    platform_playbooks = _build_platform_playbooks(
        platform_target=platform_detection["platform_target"],
        problem_class=str(diagnosis_artifact.get("problem_class") or "service_health"),
    )
    execution_session = _build_execution_session_template(repair_plan)
    verification_phase = _build_verification_phase(
        repair_plan=repair_plan,
        diagnosis_artifact=diagnosis_artifact,
        execution_scope=execution_scope,
    )
    bridge_contract = _build_hardening_bridge_contract(repair_plan)

    normalized["issue_symptom"] = issue_symptom
    normalized["platform_target"] = platform_detection["platform_target"]
    normalized["execution_scope"] = execution_scope
    normalized["dry_run"] = dry_run_default
    normalized["evidence_sources"] = evidence_sources
    normalized["affected_targets"] = affected_targets
    normalized["platform_detection"] = platform_detection
    normalized["environment_summary"] = _build_environment_summary(platform_detection, normalized)
    normalized["evidence_contract"] = {
        "allowed_sources": list(ALLOWED_EVIDENCE_SOURCES),
        "selected_sources": evidence_sources,
        "bounded_collection": {
            "max_items_per_source": 50,
            "max_chars_per_item": 4000,
            "sensitive_fields_redacted": True,
        },
    }
    normalized["problem_taxonomy"] = {
        "classes": list(PROBLEM_TAXONOMY.keys()),
        "mapped_problem_class": diagnosis_artifact["problem_class"],
    }
    normalized["diagnosis_artifact"] = diagnosis_artifact
    normalized["repair_plan"] = repair_plan
    normalized["rollback_caution_model"] = rollback_caution_model
    normalized["platform_evidence_adapters"] = platform_adapters
    normalized["platform_playbooks"] = platform_playbooks
    normalized["golden_paths"] = _build_golden_paths(
        issue_symptom=issue_symptom,
        evidence_sources=evidence_sources,
        execution_scope=execution_scope,
    )
    normalized["execution_session"] = execution_session
    normalized["verification_phase"] = verification_phase
    normalized["bridge_contract"] = bridge_contract
    normalized["future_extension_boundaries"] = _build_future_extension_boundaries()
    normalized["deterministic_repair_foundation"] = build_deterministic_repair_foundation_snapshot(
        mode_data=normalized,
        issue_symptom=issue_symptom,
        evidence_sources=evidence_sources,
    )
    normalized["session_trail"] = _build_session_trail(
        evidence_contract=normalized["evidence_contract"],
        repair_plan=repair_plan,
        execution_session=execution_session,
        verification_phase=verification_phase,
    )
    normalized["cli_output"] = _build_cli_output_sections(
        diagnosis_artifact=diagnosis_artifact,
        repair_plan=repair_plan,
        verification_phase=verification_phase,
    )
    normalized["smoke_scenarios"] = _build_smoke_scenarios(
        platform_target=platform_detection["platform_target"],
        issue_symptom=issue_symptom,
        evidence_sources=evidence_sources,
        execution_scope=execution_scope,
    )
    normalized["kritis_hook_fields"] = list(KRITIS_HOOK_FIELDS)
    normalized["kritis_enforcement"] = "deferred"
    return normalized


def render_admin_repair_goal(mode_data: dict[str, Any]) -> str:
    issue_symptom = str(mode_data.get("issue_symptom") or "unspecified_admin_issue")
    platform = str((mode_data.get("platform_detection") or {}).get("platform_label") or mode_data.get("platform_target") or "Unknown")
    scope = str(mode_data.get("execution_scope") or "diagnosis_only")
    diagnosis_class = str((mode_data.get("diagnosis_artifact") or {}).get("problem_class") or "service_health")
    evidence_sources = list(mode_data.get("evidence_sources") or [])
    dry_run = bool(mode_data.get("dry_run", True))
    return (
        f"Bearbeite einen Admin-Repair-Fall als Shared Foundation: {issue_symptom}. "
        f"Plattformziel: {platform}. "
        "Fokussiere auf bounded diagnosis, step-confirmed execution, advisory repair classification und verifizierbare Outputs; "
        "dieser Modus ist nicht voll KRITIS-gehaertet. "
        f"Execution Scope: {scope}. Dry-run default: {dry_run}. "
        f"Diagnoseklasse: {diagnosis_class}. "
        f"Evidenzquellen: {', '.join(evidence_sources)}."
    )
