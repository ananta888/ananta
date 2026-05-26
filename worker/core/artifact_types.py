"""Typed artifact enforcement for native worker outputs. AWF-T038."""
from __future__ import annotations

from typing import Any

TYPED_ARTIFACT_KINDS = frozenset({
    "command_plan_artifact",
    "command_result_artifact",
    "test_result_artifact",
    "verification_artifact",
    "patch_artifact",
    "patch_plan_artifact",
    "review_artifact",
    "security_review_artifact",
    "triage_artifact",
    "summary_artifact",
    "memory_proposal_artifact",
    "skill_proposal_artifact",
    "skill_result",
    "delegation_artifact",
    # DRR-T031: Deterministic repair artifact kinds
    "repair_plan_artifact",
    "repair_preview_artifact",
    "repair_step_result_artifact",
    "repair_verification_artifact",
    "repair_outcome_artifact",
    "rollback_proposal_artifact",
    "repair_execution_result",
})

# DRR-T031: Required metadata fields per repair artifact kind
REPAIR_ARTIFACT_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "repair_plan_artifact": frozenset({"plan_id", "procedure_id", "safety_class"}),
    "repair_preview_artifact": frozenset({"plan_id", "procedure_id", "safety_class"}),
    "repair_step_result_artifact": frozenset({"step_id", "plan_id", "status"}),
    "repair_verification_artifact": frozenset({"plan_id", "verification_status"}),
    "repair_outcome_artifact": frozenset({"plan_id", "outcome_label", "procedure_id"}),
    "rollback_proposal_artifact": frozenset({"plan_id", "rollback_recommended"}),
    "repair_execution_result": frozenset({"plan_id", "procedure_id"}),
}


def validate_repair_artifact_metadata(artifact: dict) -> list[str]:
    """Return validation errors for repair artifact metadata. DRR-T031."""
    kind = str(artifact.get("kind") or "")
    required = REPAIR_ARTIFACT_REQUIRED_FIELDS.get(kind)
    if required is None:
        return []
    metadata = dict(artifact.get("metadata") or {})
    missing = [f for f in required if f not in metadata]
    return [f"repair_artifact_missing_field:{f}" for f in missing]

# Modes that are "non-trivial" — success requires at least one typed artifact
_MUTATION_OR_CODE_MODES = frozenset({
    "shell_execute", "command_execute", "patch_apply",
    "patch_propose", "code_read", "plan_only",
})

_SUCCESS_STATUSES = frozenset({"success", "partial_success"})


def is_typed_artifact(artifact: dict[str, Any]) -> bool:
    """Return True if artifact has a recognized typed kind. AWF-T038."""
    return str(artifact.get("kind") or "").strip() in TYPED_ARTIFACT_KINDS


def enforce_artifact_first(
    *,
    artifacts: list[dict[str, Any]],
    mode: str,
    status: str,
) -> list[str]:
    """Enforce artifact-first rule for non-trivial successful results. AWF-T038.

    Returns list of violation messages; empty = compliant.
    """
    if status not in _SUCCESS_STATUSES:
        return []
    if mode not in _MUTATION_OR_CODE_MODES:
        return []
    typed = [a for a in artifacts if is_typed_artifact(a)]
    if not typed:
        return [
            f"artifact_first_violation:mode={mode!r} success requires at least one typed artifact"
        ]
    return []


def map_reference_kind_to_output_artifact_type(kind: str) -> str | None:
    normalized = str(kind or "").strip().lower()
    if not normalized:
        return None
    if "patch" in normalized:
        return "patch"
    if "test_result" in normalized:
        return "test_result"
    if "plan" in normalized:
        return "plan"
    if normalized in {"workspace_file", "file"}:
        return "file"
    if "summary" in normalized or "review" in normalized:
        return "report"
    return None
