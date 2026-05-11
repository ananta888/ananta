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
})

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
