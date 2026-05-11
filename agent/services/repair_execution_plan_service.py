"""Hub-side RepairProcedureExecutionPlan generator.

DRR-T006: Produces a typed, machine-readable RepairProcedureExecutionPlan
from matched signature outcome, environment facts, and policy decision.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from worker.core.execution_envelope import RepairStep, RepairProcedure


# ── Typed plan models ─────────────────────────────────────────────────────────

class RepairActionType(str, Enum):
    collect_evidence = "collect_evidence"
    command_probe = "command_probe"
    command_mutation = "command_mutation"
    file_patch_propose = "file_patch_propose"
    file_patch_apply = "file_patch_apply"
    service_restart = "service_restart"
    verification_probe = "verification_probe"
    stop = "stop"
    escalate = "escalate"


class RepairStepExecutionPlan(BaseModel):
    step_id: str
    step_type: str
    title: str = ""
    action: RepairActionType
    preconditions: list[str] = Field(default_factory=list)
    expected_inputs: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: dict[str, Any] = Field(default_factory=dict)
    mutation_candidate: bool = False
    action_safety_class: str = "inspect_only"
    requires_approval: bool = False
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    timeout_seconds: int = 60
    verification_after_step: bool = False
    rollback_hint_refs: list[str] = Field(default_factory=list)

    @field_validator("step_type")
    @classmethod
    def _validate_step_type(cls, v: str) -> str:
        known = {
            "collect_evidence", "evaluate_signature_outcome", "branch",
            "classify_case", "stop", "command_probe", "command_mutation",
            "file_patch_propose", "file_patch_apply", "service_restart",
            "verification_probe", "escalate",
        }
        if v not in known:
            raise ValueError(f"unknown step_type: {v!r}")
        return v

    @field_validator("action_safety_class")
    @classmethod
    def _validate_safety_class(cls, v: str) -> str:
        known = {"inspect_only", "bounded_low_risk", "confirm_required", "high_risk"}
        if v not in known:
            raise ValueError(f"unknown action_safety_class: {v!r}")
        return v


class RepairProcedureExecutionPlan(BaseModel):
    plan_id: str
    goal_id: str = ""
    task_id: str = ""
    procedure_id: str
    problem_class: str = ""
    signature_id: str = ""
    signature_confidence: float = 0.0
    safety_class: str = "safe"
    approval_requirement: str = "none"
    environment_facts_hash: str = ""
    created_by: str = "hub"
    policy_decision_ref: str = ""
    context_bundle_ref: str = ""
    steps: list[RepairStepExecutionPlan] = Field(default_factory=list)
    verification_plan: list[str] = Field(default_factory=list)
    rollback_hints: list[str] = Field(default_factory=list)
    max_runtime_seconds: int = 300
    version: str = "1.0"

    @field_validator("procedure_id")
    @classmethod
    def _non_empty_procedure_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("procedure_id must be non-empty")
        return v.strip()

    @field_validator("steps")
    @classmethod
    def _non_empty_steps(cls, v: list[RepairStepExecutionPlan]) -> list[RepairStepExecutionPlan]:
        if not v:
            raise ValueError("steps must be non-empty")
        return v

    @field_validator("created_by")
    @classmethod
    def _must_be_hub(cls, v: str) -> str:
        if v.strip().lower() != "hub":
            raise ValueError("created_by must be 'hub'")
        return v.strip().lower()

    @field_validator("safety_class")
    @classmethod
    def _validate_safety_class(cls, v: str) -> str:
        known = {"safe", "review_first", "confirm_required", "high_risk"}
        if v not in known:
            raise ValueError(f"unknown safety_class: {v!r}")
        return v

    @field_validator("approval_requirement")
    @classmethod
    def _validate_approval_requirement(cls, v: str) -> str:
        known = {"none", "review_first", "confirm_required", "high_risk"}
        if v not in known:
            raise ValueError(f"unknown approval_requirement: {v!r}")
        return v

    @model_validator(mode="after")
    def _check_mutation_verification(self) -> "RepairProcedureExecutionPlan":
        for step in self.steps:
            if step.mutation_candidate and not step.verification_after_step:
                raise ValueError(
                    f"mutation step {step.step_id!r} must have verification_after_step=True"
                )
        return self

    def to_repair_procedure(self) -> RepairProcedure:
        return RepairProcedure(
            procedure_id=self.procedure_id,
            safety_class=self.safety_class,
            steps=[
                RepairStep(
                    step_id=s.step_id,
                    title=s.title,
                    action_class=s.action.value,
                    mutation_candidate=s.mutation_candidate,
                    verification_required=s.verification_after_step,
                )
                for s in self.steps
            ],
            diagnosis={
                "problem_class": self.problem_class,
                "signature_id": self.signature_id,
                "signature_confidence": self.signature_confidence,
                "plan_id": self.plan_id,
            },
        )


# ── Plan generation ───────────────────────────────────────────────────────────

def _compute_environment_facts_hash(environment_facts: dict[str, Any]) -> str:
    normalized = json.dumps(environment_facts, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _determine_safety_class(problem_class: str) -> str:
    high_risk = {"permission_issue", "runtime_health_failure"}
    review_first = {"package_install_failure", "compose_failure"}
    if problem_class in high_risk:
        return "high_risk"
    if problem_class in review_first:
        return "review_first"
    return "safe"


def _determine_approval_requirement(safety_class: str, mutation_count: int) -> str:
    if safety_class == "high_risk":
        return "high_risk"
    if safety_class == "review_first":
        return "review_first"
    if mutation_count > 0:
        return "confirm_required"
    return "none"


def _build_inspect_step(step_id: str, title: str, evidence_sources: list[str]) -> RepairStepExecutionPlan:
    return RepairStepExecutionPlan(
        step_id=step_id,
        step_type="collect_evidence",
        title=title,
        action=RepairActionType.collect_evidence,
        preconditions=["deterministic_diagnosis_completed"],
        mutation_candidate=False,
        action_safety_class="inspect_only",
        requires_approval=False,
        required_capabilities=["repair.diagnose"],
        allowed_tools=["shell_read"],
        timeout_seconds=30,
        verification_after_step=False,
        expected_inputs={"evidence_sources": evidence_sources},
        expected_outputs={"evidence_collected": True},
    )


def _build_verification_step(step_id: str, title: str) -> RepairStepExecutionPlan:
    return RepairStepExecutionPlan(
        step_id=step_id,
        step_type="verification_probe",
        title=title,
        action=RepairActionType.verification_probe,
        preconditions=["mutation_executed"],
        mutation_candidate=False,
        action_safety_class="inspect_only",
        requires_approval=False,
        required_capabilities=["repair.verify"],
        allowed_tools=["shell_read"],
        timeout_seconds=30,
        verification_after_step=False,
    )


def _build_mutation_step(
    step_id: str,
    title: str,
    action_safety_class: str,
    requires_approval: bool,
) -> RepairStepExecutionPlan:
    return RepairStepExecutionPlan(
        step_id=step_id,
        step_type="command_mutation",
        title=title,
        action=RepairActionType.command_mutation,
        preconditions=["pre_mutation_verification_passed"],
        mutation_candidate=True,
        action_safety_class=action_safety_class,
        requires_approval=requires_approval,
        required_capabilities=["repair.execute.low_risk"],
        allowed_tools=["shell_write"],
        timeout_seconds=60,
        verification_after_step=True,
        rollback_hint_refs=["rollback:restore_previous_state"],
    )


def _build_escalation_step() -> RepairStepExecutionPlan:
    return RepairStepExecutionPlan(
        step_id="escalate",
        step_type="escalate",
        title="Escalate to LLM with bounded context",
        action=RepairActionType.escalate,
        preconditions=["deterministic_paths_exhausted"],
        mutation_candidate=False,
        action_safety_class="inspect_only",
        requires_approval=False,
        required_capabilities=["repair.llm_escalate"],
        allowed_tools=[],
        timeout_seconds=120,
        verification_after_step=False,
    )


def generate_repair_execution_plan(
    *,
    matching_outcome: dict[str, Any],
    environment_facts: dict[str, Any],
    signature_matching: dict[str, Any] | None = None,
    diagnosis_run: dict[str, Any] | None = None,
    selected_catalog_entry: dict[str, Any] | None = None,
    task_id: str = "",
    goal_id: str = "",
    policy_decision_ref: str = "",
    context_bundle_ref: str = "",
) -> RepairProcedureExecutionPlan:
    """Generate a typed RepairProcedureExecutionPlan from diagnostic outputs.

    High-confidence signatures produce full mutation-capable plans.
    Low-confidence/no-match produces escalation or diagnosis-only plans.
    """
    outcome = str(matching_outcome.get("outcome") or "no_match")
    best_score = float(matching_outcome.get("best_score") or 0.0)
    problem_class = str(matching_outcome.get("best_problem_class") or "service_start_failure")
    top_matches = list((signature_matching or {}).get("matches") or [{}])
    top_match = top_matches[0] if top_matches else {}
    signature_id = str(top_match.get("signature_id") or "")
    signature_confidence = float(top_match.get("score") or 0.0)

    safety_class = _determine_safety_class(problem_class)
    env_hash = _compute_environment_facts_hash(environment_facts)
    max_runtime = 120 if safety_class == "high_risk" else 300
    steps: list[RepairStepExecutionPlan] = []

    if outcome == "single_high_confidence" and best_score >= 0.78:
        diagnosis_steps = list((diagnosis_run or {}).get("executed_steps") or [])
        if diagnosis_steps:
            for i, dstep in enumerate(diagnosis_steps):
                steps.append(_build_inspect_step(
                    step_id=str(dstep.get("step_id") or f"diag-{i:02d}"),
                    title=str(dstep.get("title") or f"Diagnostic step {i+1}"),
                    evidence_sources=list(dstep.get("expected_sources") or []),
                ))
        else:
            steps.append(_build_inspect_step(
                step_id="collect-evidence",
                title="Collect evidence for " + problem_class,
                evidence_sources=["error_logs", "service_status"],
            ))

        catalog_procedure = dict((selected_catalog_entry or {}).get("procedure") or {})
        catalog_steps = list(catalog_procedure.get("steps") or [])
        for i, cstep in enumerate(catalog_steps):
            if bool(cstep.get("mutation_candidate")):
                action_safety_class = "bounded_low_risk"
                if safety_class == "review_first":
                    action_safety_class = "confirm_required"
                elif safety_class == "high_risk":
                    action_safety_class = "high_risk"
                requires_approval = action_safety_class in {"confirm_required", "high_risk"}
                steps.append(_build_mutation_step(
                    step_id=str(cstep.get("id") or f"mutate-{i:02d}"),
                    title=str(cstep.get("title") or f"Mutation step {i+1}"),
                    action_safety_class=action_safety_class,
                    requires_approval=requires_approval,
                ))
            elif str(cstep.get("step_type") or "") == "verification_check":
                steps.append(_build_verification_step(
                    step_id=str(cstep.get("id") or f"verify-{i:02d}"),
                    title=str(cstep.get("title") or f"Verification step {i+1}"),
                ))

        steps.append(_build_verification_step(
            step_id="final-verify",
            title="Final verification for " + problem_class,
        ))

    elif outcome == "no_match":
        steps.append(_build_inspect_step(
            step_id="collect-evidence",
            title="Collect additional evidence for unknown failure",
            evidence_sources=["error_logs", "service_status", "runtime_state"],
        ))
        steps.append(_build_escalation_step())

    elif outcome in ("low_confidence", "ambiguous_high_confidence"):
        steps.append(_build_inspect_step(
            step_id="collect-corroboration",
            title="Collect corroborating evidence for ambiguous outcome",
            evidence_sources=["error_logs", "runtime_state", "container_state"],
        ))
        steps.append(_build_inspect_step(
            step_id="evaluate",
            title="Evaluate corroborated evidence",
            evidence_sources=[],
        ))
        steps.append(_build_escalation_step())
    else:
        steps.append(_build_escalation_step())

    mutation_count = sum(1 for s in steps if s.mutation_candidate)
    approval_requirement = _determine_approval_requirement(safety_class, mutation_count)

    plan_id = f"repair-plan-{uuid.uuid4().hex[:12]}"
    procedure_id = str(
        (selected_catalog_entry or {})
        .get("procedure", {})
        .get("id") or f"procedure-{problem_class}-v1"
    )

    plan = RepairProcedureExecutionPlan(
        plan_id=plan_id,
        goal_id=goal_id,
        task_id=task_id,
        procedure_id=procedure_id,
        problem_class=problem_class,
        signature_id=signature_id,
        signature_confidence=signature_confidence,
        safety_class=safety_class,
        approval_requirement=approval_requirement,
        environment_facts_hash=env_hash,
        created_by="hub",
        policy_decision_ref=policy_decision_ref,
        context_bundle_ref=context_bundle_ref,
        steps=steps,
        verification_plan=[s.step_id for s in steps if s.action == RepairActionType.verification_probe],
        rollback_hints=["restore_previous_state_on_failure"],
        max_runtime_seconds=max_runtime,
        version="1.0",
    )

    return plan
