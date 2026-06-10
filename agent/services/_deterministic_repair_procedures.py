"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Repair procedure construction, catalog selection, preview rendering and the LLM-proposal review path.

Public re-exports: the public agent.services.deterministic_repair_path_service
module continues to expose every function via thin delegating wrappers, so
existing imports keep working unchanged.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Pattern

from agent.db_models import RepairOutcomeMemoryDB
from agent.repositories.repair_outcome import get_repair_outcome_memory_repo

from agent.services._deterministic_repair_constants import (
    REPAIR_PROBLEM_CLASS_INVENTORY,
    REPAIR_EXECUTION_SAFETY_POLICY,
    REPAIR_ACTION_SAFETY_CLASSES,
)


log = logging.getLogger(__name__)




def build_repair_procedure_template(*, problem_class: str, platform_target: str) -> dict[str, Any]:
    safety_class = "safe"
    if problem_class in {"permission_issue", "runtime_health_failure"}:
        safety_class = "high_risk"
    elif problem_class in {"package_install_failure", "compose_failure"}:
        safety_class = "review_first"
    return {
        "id": f"repair-procedure-{problem_class}-v1",
        "problem_class": problem_class,
        "platform_target": platform_target,
        "safety_class": safety_class,
        "preconditions": [
            "deterministic_diagnosis_completed",
            "non_destructive_checks_executed",
            "bounded_execution_scope_confirmed",
        ],
        "steps": [
            {
                "id": "repair-step-01",
                "title": "Prepare bounded repair preview",
                "mutation_candidate": False,
                "dry_run_supported": True,
            },
            {
                "id": "repair-step-02",
                "title": "Execute selected bounded repair action",
                "mutation_candidate": True,
                "dry_run_supported": True,
            },
            {
                "id": "repair-step-03",
                "title": "Run post-repair verification checks",
                "mutation_candidate": False,
                "dry_run_supported": False,
            },
        ],
        "postconditions": [
            "repair_outcome_recorded",
            "verification_result_classified",
        ],
        "verification": {
            "required": True,
            "checks": ["service_health", "command_exit_code", "target_specific_health_probe"],
        },
        "rollback_hints": [
            "prefer_reversible_actions_when_available",
            "record_manual_recovery_hint_for_non_reversible_actions",
        ],
    }





def build_initial_repair_procedure_catalog(
    *, signature_catalog: tuple[FailureSignature, ...] | None = None
) -> dict[str, Any]:
    catalog = signature_catalog or build_initial_failure_signature_catalog()
    signature_ids_by_problem_class: dict[str, list[str]] = {}
    for signature in catalog:
        signature_ids_by_problem_class.setdefault(signature.problem_class, []).append(signature.id)

    entries: list[dict[str, Any]] = []
    for problem_class in REPAIR_PROBLEM_CLASS_INVENTORY.keys():
        template = build_repair_procedure_template(problem_class=problem_class, platform_target="cross_platform")
        entries.append(
            {
                "id": f"repair-catalog-{problem_class}-v1",
                "problem_class": problem_class,
                "trigger_signature_ids": signature_ids_by_problem_class.get(problem_class, []),
                "trigger_diagnosis_outcomes": [problem_class, f"{problem_class}_review_required"],
                "bounded_scope_only": True,
                "procedure": template,
            }
        )
    return {
        "schema": "deterministic_repair_catalog_v1",
        "entries": entries,
        "bounded_scope_only": True,
    }





def select_repair_procedure_from_catalog(
    *,
    repair_catalog: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    entries = list(repair_catalog.get("entries") or [])
    if not entries:
        return {}
    target_problem_class = str(matching_outcome.get("best_problem_class") or "service_start_failure")
    for entry in entries:
        if str(entry.get("problem_class") or "") == target_problem_class:
            return entry
    return entries[0]





def build_repair_procedure_preview(
    *,
    selected_catalog_entry: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    steps = list(procedure.get("steps") or [])
    return {
        "schema": "deterministic_repair_preview_v1",
        "procedure_id": procedure.get("id"),
        "problem_class": selected_catalog_entry.get("problem_class"),
        "step_count": len(steps),
        "dry_run_supported": any(bool(step.get("dry_run_supported")) for step in steps),
        "mutation_step_ids": [step.get("id") for step in steps if bool(step.get("mutation_candidate"))],
        "matching_outcome": matching_outcome.get("outcome"),
        "limitations": [
            "preview_only_does_not_apply_state_changes",
            "verification_results_in_preview_are_predictive_not_observed",
        ],
    }





def execute_repair_procedure(
    *,
    selected_catalog_entry: dict[str, Any],
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
    dry_run: bool,
    approval_policy: dict[str, Any] | None = None,
    safety_policy: dict[str, Any] | None = None,
    session_id: str = "repair-session-default",
    target_scope: str = "service_runtime",
    stop_on_contradictory_evidence: bool = True,
    stop_on_worsening_signals: bool = True,
) -> dict[str, Any]:
    policy = dict(REPAIR_EXECUTION_SAFETY_POLICY)
    if safety_policy:
        policy.update(dict(safety_policy))
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    procedure_id = str(procedure.get("id") or "unknown_procedure")
    steps = list(procedure.get("steps") or [])
    guardrail_evaluation = evaluate_unsafe_action_guardrails(
        proposed_actions=[str(step.get("title") or "") for step in steps],
    )
    procedure_safety_class = str(procedure.get("safety_class") or "safe")
    procedure_requires_approval = procedure_safety_class in set(policy.get("requires_approval_for_safety_classes") or [])
    scope_key = _approval_scope_key(
        procedure_id=procedure_id,
        target_scope=str(target_scope),
        session_id=str(session_id),
    )
    approved_scopes = {str(item) for item in list((approval_policy or {}).get("approved_scopes") or []) if str(item)}
    approval_context = {
        "required": procedure_requires_approval,
        "approved_mutations": bool((approval_policy or {}).get("approved_mutations", False)),
        "scope_key": scope_key,
        "scope_dimensions": {
            "procedure_id": procedure_id,
            "target_scope": str(target_scope),
            "session_id": str(session_id),
        },
        "approved_scopes": sorted(approved_scopes),
    }
    contradictory = _detect_contradictory_evidence(normalized_evidence)
    worsening = _detect_worsening_signals(normalized_evidence)
    step_records: list[dict[str, Any]] = []
    abort_conditions: list[dict[str, Any]] = []
    stop_reason = None

    if guardrail_evaluation["blocked_actions"] and guardrail_evaluation["fail_closed"]:
        abort_conditions.append(
            {
                "code": "unsafe_action_guardrail_block",
                "severity": "critical",
                "step_id": None,
                "message": "Execution blocked by unsafe action guardrails.",
                "blocked_actions": list(guardrail_evaluation["blocked_actions"]),
            }
        )
        return {
            "schema": "deterministic_repair_execution_v1",
            "procedure_id": procedure_id,
            "problem_class": selected_catalog_entry.get("problem_class"),
            "procedure_safety_class": procedure_safety_class,
            "execution_mode": "dry_run" if dry_run else "apply",
            "status": "blocked",
            "safety_policy": policy,
            "approval": approval_context,
            "steps": step_records,
            "abort_conditions": abort_conditions,
            "stop_reason": "unsafe_action_guardrail_block",
            "clean_stop": True,
            "bounded_execution_enforced": True,
            "worsening_signals_detected": worsening,
            "contradictory_evidence_detected": contradictory,
            "unsafe_action_guardrails": guardrail_evaluation,
        }

    for step in steps:
        step_id = str(step.get("id") or "")
        mutation_candidate = bool(step.get("mutation_candidate"))
        action_safety_class = classify_repair_action_safety(
            step=step,
            procedure_safety_class=procedure_safety_class,
        )
        if action_safety_class not in set(REPAIR_ACTION_SAFETY_CLASSES["classes"]):
            abort_conditions.append(
                {
                    "code": "unknown_action_safety_class",
                    "severity": "critical",
                    "step_id": step_id,
                    "message": "Unknown action safety class rejected by bounded execution policy.",
                }
            )
            stop_reason = "unknown_action_safety_class"
            break
        action_requires_approval = action_safety_class in set(policy.get("requires_approval_for_action_safety_classes") or [])
        scope_approved = approval_context["approved_mutations"] or scope_key in approved_scopes
        verification = run_step_verification(
            step=step,
            normalized_evidence=normalized_evidence,
            environment_facts=environment_facts,
        )
        record = {
            "step_id": step_id,
            "title": step.get("title"),
            "mutation_candidate": mutation_candidate,
            "action_safety_class": action_safety_class,
            "requires_approval": action_requires_approval,
            "dry_run_supported": bool(step.get("dry_run_supported")),
            "verification": verification,
            "state": "previewed" if dry_run else "executed",
            "verifiable": True,
            "audit_hint": f"repair_execution:{procedure_id}:{step_id}",
        }
        if mutation_candidate and action_requires_approval and not scope_approved:
            record["state"] = "blocked"
            stop_reason = "approval_required"
            abort_conditions.append(
                {
                    "code": "approval_required_for_mutation",
                    "severity": "high",
                    "step_id": step_id,
                    "message": "Mutation step blocked because scoped approval is required and missing.",
                }
            )
            step_records.append(record)
            break
        if mutation_candidate and stop_on_worsening_signals and verification["checks"]["worsening_signals"]:
            record["state"] = "aborted"
            stop_reason = "worsening_signals"
            abort_conditions.append(
                {
                    "code": "abort_on_worsening_signals",
                    "severity": "critical",
                    "step_id": step_id,
                    "message": "Execution aborted due to worsening signals before mutation.",
                }
            )
            step_records.append(record)
            break
        if mutation_candidate and stop_on_contradictory_evidence and verification["checks"]["contradictory_evidence"]:
            record["state"] = "aborted"
            stop_reason = "contradictory_evidence"
            abort_conditions.append(
                {
                    "code": "abort_on_contradictory_evidence",
                    "severity": "high",
                    "step_id": step_id,
                    "message": "Execution aborted due to contradictory evidence that makes repair unsafe.",
                }
            )
            step_records.append(record)
            break
        step_records.append(record)

    if dry_run:
        status = "preview_only"
    elif abort_conditions:
        status = "aborted"
    else:
        status = "completed"
    return {
        "schema": "deterministic_repair_execution_v1",
        "procedure_id": procedure_id,
        "problem_class": selected_catalog_entry.get("problem_class"),
        "procedure_safety_class": procedure_safety_class,
        "execution_mode": "dry_run" if dry_run else "apply",
        "status": status,
        "safety_policy": policy,
        "approval": approval_context,
        "steps": step_records,
        "abort_conditions": abort_conditions,
        "stop_reason": stop_reason,
        "clean_stop": bool(abort_conditions),
        "bounded_execution_enforced": True,
        "worsening_signals_detected": worsening,
        "contradictory_evidence_detected": contradictory,
        "unsafe_action_guardrails": guardrail_evaluation,
    }





def convert_llm_proposal_to_reviewed_procedure(
    *,
    llm_proposal: dict[str, Any],
    environment_facts: dict[str, Any],
    llm_generate_text: Any = None,
) -> dict[str, Any]:
    try:
        if llm_generate_text is not None:
            try:
                from agent.services.system_prompt_catalog import get_system_prompt

from agent.services import _deterministic_repair_signatures as _drr_signatures
build_initial_failure_signature_catalog = _drr_signatures.build_initial_failure_signature_catalog
from agent.services import _deterministic_repair_utils as _drr_utils
_approval_scope_key = _drr_utils._approval_scope_key
_default_llm_proposal_conversion = _drr_utils._default_llm_proposal_conversion
_detect_contradictory_evidence = _drr_utils._detect_contradictory_evidence
_detect_worsening_signals = _drr_utils._detect_worsening_signals
_structure_llm_proposal = _drr_utils._structure_llm_proposal
from agent.services import _deterministic_repair_verification as _drr_verification
run_step_verification = _drr_verification.run_step_verification
from agent.services import _deterministic_repair_safety as _drr_safety
classify_repair_action_safety = _drr_safety.classify_repair_action_safety
from agent.services import _deterministic_repair_misc as _drr_misc
evaluate_unsafe_action_guardrails = _drr_misc.evaluate_unsafe_action_guardrails
                _tpl = get_system_prompt("system.repair_procedure_converter", "")
            except Exception:
                _tpl = ""
            if _tpl:
                prompt = _tpl.format(
                    proposal_json=json.dumps(llm_proposal),
                    platform_target=str(environment_facts.get("platform_target") or ""),
                )
            else:
                prompt = (
                    "You are a deterministic repair procedure converter. "
                    "Convert the following unstructured repair proposal into a structured step-by-step repair procedure. "
                    "Each step must have: id, title (max 180 chars), mutation_candidate (bool), "
                    "requires_review (bool), requires_approval (bool), execution_allowed (bool). "
                    "Return valid JSON with a 'steps' array. Max 5 steps.\n\n"
                    f"Proposal: {json.dumps(llm_proposal)}\n"
                    f"Platform: {environment_facts.get('platform_target')}\n"
                )
            try:
                llm_response = llm_generate_text(prompt=prompt)
                if isinstance(llm_response, dict):
                    llm_data = llm_response
                elif isinstance(llm_response, str):
                    llm_data = json.loads(llm_response)
                else:
                    llm_data = None
                if isinstance(llm_data, dict) and isinstance(llm_data.get("steps"), list):
                    return _structure_llm_proposal(llm_data, llm_proposal, environment_facts)
            except Exception as exc:
                log.warning("LLM proposal conversion failed, falling back to default: %s", exc)
    except Exception as exc:
        log.warning("convert_llm_proposal_to_reviewed_procedure setup failed: %s", exc)

    return _default_llm_proposal_conversion(llm_proposal, environment_facts)






def execute_repair_procedure(
    *,
    selected_catalog_entry: dict[str, Any],
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
    dry_run: bool,
    approval_policy: dict[str, Any] | None = None,
    safety_policy: dict[str, Any] | None = None,
    session_id: str = "repair-session-default",
    target_scope: str = "service_runtime",
    stop_on_contradictory_evidence: bool = True,
    stop_on_worsening_signals: bool = True,
):
    return _rpr.execute_repair_procedure(selected_catalog_entry=selected_catalog_entry, normalized_evidence=normalized_evidence, environment_facts=environment_facts, dry_run=dry_run, approval_policy=approval_policy, safety_policy=safety_policy, session_id=session_id, target_scope=target_scope, stop_on_contradictory_evidence=stop_on_contradictory_evidence, stop_on_worsening_signals=stop_on_worsening_signals)

