"""SkillProposalArtifact: proposal-only self-improvement path. AWF-T030.

Workers can propose new skills for Hub review, but cannot self-install them.
Requires skill_propose capability. Proposals do not mutate the active SkillRegistry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.core.execution_envelope import ExecutionEnvelope

_SKILL_PROPOSE_CAPABILITY = "skill_propose"


@dataclass
class SkillProposalArtifact:
    """Proposed SkillManifest awaiting Hub approval. AWF-T030.

    Does NOT enable or write to the active SkillRegistry.
    Approval/installation is an explicit, separate Hub workflow.
    """
    proposed_manifest: dict[str, Any]
    rationale: str
    evidence_refs: list[str] = field(default_factory=list)
    expected_tests: list[str] = field(default_factory=list)
    risk_analysis: str = ""
    required_capabilities: list[str] = field(default_factory=list)
    proposed_by: str = ""
    approval_required: bool = True
    approved: bool = False


def emit_skill_proposal(
    *,
    envelope: ExecutionEnvelope,
    proposed_manifest: dict[str, Any],
    rationale: str,
    evidence_refs: list[str] | None = None,
    expected_tests: list[str] | None = None,
    risk_analysis: str = "",
) -> SkillProposalArtifact:
    """Emit a SkillProposalArtifact — requires skill_propose capability. AWF-T030.

    Raises PermissionError if the capability is not granted.
    The returned artifact is a plain data object; it does NOT modify any registry.
    """
    granted = frozenset(envelope.capability_grant.capabilities)
    if _SKILL_PROPOSE_CAPABILITY not in granted:
        raise PermissionError(f"capability_required:{_SKILL_PROPOSE_CAPABILITY}")

    manifest = dict(proposed_manifest or {})
    return SkillProposalArtifact(
        proposed_manifest=manifest,
        rationale=str(rationale or "").strip(),
        evidence_refs=list(evidence_refs or []),
        expected_tests=list(expected_tests or []),
        risk_analysis=str(risk_analysis or "").strip(),
        required_capabilities=list(manifest.get("required_capabilities") or []),
        proposed_by=str(envelope.task_id or ""),
        approval_required=True,
        approved=False,
    )
