"""Artifact-first execution enforcement.

EW-T051: Plans → PlanArtifact, patches → PatchArtifact, commands → CommandPlanArtifact.
WorkerResult.summary references artifact ids; free-text-only responses not accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Artifact kind vocabulary ───────────────────────────────────────────────────

KNOWN_ARTIFACT_KINDS = frozenset({
    "plan_artifact",
    "patch_artifact",
    "command_plan_artifact",
    "command_result_artifact",
    "test_result_artifact",
    "verification_artifact",
    "skill_proposal_artifact",
    "skill_review_artifact",
    "delegation_artifact",
    "job_run_artifact",
    "diagnostic_artifact",
    "patch_candidate",
})

# Capabilities that MUST produce at least one artifact
CAPABILITY_ARTIFACT_MAP: dict[str, list[str]] = {
    "planning":       ["plan_artifact"],
    "patch_propose":  ["patch_artifact", "patch_candidate"],
    "patch_apply":    ["patch_artifact"],
    "shell_plan":     ["command_plan_artifact"],
    "shell_execute":  ["command_result_artifact"],
    "test_run":       ["test_result_artifact"],
    "verify":         ["verification_artifact"],
}


# ── EnforcementResult ─────────────────────────────────────────────────────────

@dataclass
class ArtifactEnforcementResult:
    compliant: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── ArtifactEnforcer ─────────────────────────────────────────────────────────

class ArtifactEnforcer:
    """Verifies that WorkerResult artifacts satisfy the capability contract. EW-T051."""

    def check(
        self,
        capabilities_used: list[str],
        artifacts: list[dict[str, Any]],
        summary: str = "",
    ) -> ArtifactEnforcementResult:
        result = ArtifactEnforcementResult(compliant=True)
        artifact_kinds = {a.get("kind", "") for a in artifacts}
        artifact_ids = [a.get("artifact_id", a.get("id", "")) for a in artifacts]

        # 1. Each required capability must produce at least one matching artifact kind
        for cap in capabilities_used:
            required_kinds = CAPABILITY_ARTIFACT_MAP.get(cap)
            if required_kinds and not any(k in artifact_kinds for k in required_kinds):
                result.compliant = False
                result.violations.append(
                    f"capability {cap!r} requires one of {required_kinds!r} but none produced"
                )

        # 2. All artifact kinds must be from known vocabulary
        for a in artifacts:
            kind = a.get("kind", "")
            if kind and kind not in KNOWN_ARTIFACT_KINDS:
                result.violations.append(f"unknown artifact kind {kind!r}")
                result.compliant = False

        # 3. Summary should reference artifact IDs when artifacts exist
        if artifacts and summary and artifact_ids:
            referenced = any(aid in summary for aid in artifact_ids if aid)
            if not referenced:
                result.warnings.append(
                    "WorkerResult.summary does not reference any artifact_id"
                )

        # 4. Free-text-only result (no artifacts) when artifacts were expected
        if not artifacts and any(cap in CAPABILITY_ARTIFACT_MAP for cap in capabilities_used):
            result.compliant = False
            result.violations.append(
                "free-text-only WorkerResult not accepted: "
                "capabilities used require structured artifacts"
            )

        return result

    def build_summary_with_refs(
        self,
        description: str,
        artifacts: list[dict[str, Any]],
    ) -> str:
        """Build a summary string that references artifact IDs. EW-T051."""
        if not artifacts:
            return description
        ids = [a.get("artifact_id", a.get("id", "")) for a in artifacts if a.get("artifact_id") or a.get("id")]
        if not ids:
            return description
        refs = ", ".join(ids)
        return f"{description} [artifacts: {refs}]"
