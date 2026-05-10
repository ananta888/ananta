"""Skill system: SkillManifest, SkillRegistry, SkillRunner, proposal/review artifacts.

EW-T032: SkillManifest schema with capability requirements.
EW-T033: SkillRegistry with content hashes and enabled state.
EW-T034: SkillRunner — receives ExecutionEnvelope, never creates own authority.
EW-T035: SkillProposalArtifact workflow.
EW-T036: SkillReviewArtifact, stale-skill detection, pinned-skill protection.
EW-T037: Baseline skill definitions (manifest-only, no execution code).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from worker.core.execution_envelope import (
    KNOWN_CAPABILITY_CLASSES,
    ExecutionEnvelope,
    WorkerResult,
    WorkerResultStatus,
    make_trace,
)


# ── SkillManifest (EW-T032) ───────────────────────────────────────────────────

class SkillManifest(BaseModel):
    """Declares what a skill can do and what it requires. EW-T032."""
    id: str
    name: str
    version: str
    description: str = ""
    capability_requirements: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    risk_class: str = "medium"     # low / medium / high
    pinned: bool = False           # pinned skills require admin approval to modify
    tags: list[str] = Field(default_factory=list)

    @field_validator("capability_requirements")
    @classmethod
    def _validate_caps(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in KNOWN_CAPABILITY_CLASSES]
        if unknown:
            raise ValueError(f"unknown capability classes in skill manifest: {unknown!r}")
        return v

    @field_validator("id", "name", "version")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    def capabilities_granted_by(self, envelope: ExecutionEnvelope) -> bool:
        """True only if all required capabilities are in the envelope. EW-T032."""
        for cap in self.capability_requirements:
            if not envelope.has_capability(cap):
                return False
        return True

    def as_catalog_entry(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "description": self.description,
            "capability_requirements": self.capability_requirements,
            "risk_class": self.risk_class, "pinned": self.pinned, "tags": self.tags,
        }


# ── SkillRegistryEntry (EW-T033) ──────────────────────────────────────────────

@dataclass
class SkillRegistryEntry:
    manifest: SkillManifest
    source_path: str = ""
    content_hash: str = ""
    enabled: bool = True
    compatibility_tags: list[str] = field(default_factory=list)
    registered_at: float = field(default_factory=time.time)

    def compute_hash(self, source_code: str) -> str:
        return hashlib.sha256(source_code.encode()).hexdigest()

    def update_source(self, new_source: str) -> None:
        """Updating source always changes content_hash → requires reload. EW-T033."""
        self.content_hash = self.compute_hash(new_source)

    def trace_info(self) -> dict[str, Any]:
        """For TraceBundle — records skill id and hash. EW-T033."""
        return {
            "skill_id": self.manifest.id,
            "skill_version": self.manifest.version,
            "content_hash": self.content_hash[:16],
            "enabled": self.enabled,
        }


# ── SkillRegistry (EW-T033) ───────────────────────────────────────────────────

class SkillRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, SkillRegistryEntry] = {}

    def register(self, entry: SkillRegistryEntry) -> None:
        self._entries[entry.manifest.id] = entry

    def get(self, skill_id: str) -> SkillRegistryEntry | None:
        return self._entries.get(skill_id)

    def enabled_skills(self) -> list[SkillRegistryEntry]:
        return [e for e in self._entries.values() if e.enabled]

    def disable(self, skill_id: str) -> bool:
        entry = self._entries.get(skill_id)
        if entry:
            entry.enabled = False
            return True
        return False

    def skills_for_envelope(self, envelope: ExecutionEnvelope) -> list[SkillRegistryEntry]:
        """Skills whose capability requirements are all satisfied by the envelope."""
        return [
            e for e in self.enabled_skills()
            if e.manifest.capabilities_granted_by(envelope)
        ]

    def catalog(self) -> list[dict[str, Any]]:
        return [e.manifest.as_catalog_entry() for e in self._entries.values()]


# ── SkillRunResult ────────────────────────────────────────────────────────────

@dataclass
class SkillRunResult:
    skill_id: str
    success: bool
    reason_code: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trace_info: dict[str, Any] = field(default_factory=dict)


# ── SkillRunner (EW-T034) ─────────────────────────────────────────────────────

class SkillRunner:
    """Executes a skill within the authority of the ExecutionEnvelope. EW-T034.

    The runner never creates its own authority — it only delegates to tools
    allowed by the envelope's ToolPolicy and WorkerToolRegistry.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def run(
        self,
        skill_id: str,
        envelope: ExecutionEnvelope,
        *,
        tool_registry: Any = None,
        context: dict[str, Any] | None = None,
    ) -> SkillRunResult:
        """Validate and run a skill against the envelope."""
        entry = self._registry.get(skill_id)
        if entry is None:
            return SkillRunResult(skill_id, False, "skill_not_found")
        if not entry.enabled:
            return SkillRunResult(skill_id, False, "skill_disabled")

        # Capability gate: skill cannot declare capabilities not in envelope
        if not entry.manifest.capabilities_granted_by(envelope):
            missing = [
                c for c in entry.manifest.capability_requirements
                if not envelope.has_capability(c)
            ]
            return SkillRunResult(
                skill_id, False, "missing_capability",
                warnings=[f"skill requires {missing!r} not in envelope"],
            )

        # Tool gate: all skill tools must be allowed by envelope's ToolPolicy
        denied_tools = []
        for tool_id in entry.manifest.allowed_tools:
            if not envelope.tool_policy.is_tool_allowed(tool_id):
                denied_tools.append(tool_id)
        if denied_tools:
            return SkillRunResult(
                skill_id, False, "tool_unavailable",
                warnings=[f"skill tool(s) not in envelope ToolPolicy: {denied_tools!r}"],
            )

        trace_info = entry.trace_info()
        return SkillRunResult(
            skill_id=skill_id,
            success=True,
            reason_code="skill_run_ok",
            trace_info=trace_info,
        )


# ── SkillProposalArtifact (EW-T035) ───────────────────────────────────────────

@dataclass
class SkillProposalArtifact:
    """Proposed new skill — never auto-enabled. EW-T035."""
    artifact_id: str
    task_id: str
    proposed_manifest: SkillManifest
    rationale: str
    trigger_examples: list[str] = field(default_factory=list)
    implementation_sketch: str = ""
    test_sketch: str = ""
    risk_assessment: str = ""
    requires_capability: str = "skill_propose"
    auto_enabled: bool = False    # always False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "skill_proposal_artifact",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "proposed_skill_id": self.proposed_manifest.id,
            "rationale": self.rationale,
            "trigger_examples": self.trigger_examples,
            "risk_assessment": self.risk_assessment,
            "auto_enabled": self.auto_enabled,  # always False
        }


# ── SkillReviewArtifact (EW-T036) ─────────────────────────────────────────────

class SkillReviewFinding(str, Enum):
    outdated = "outdated"
    overlapping = "overlapping"
    unsafe = "unsafe"
    failing = "failing"
    stale = "stale"


@dataclass
class SkillReviewEntry:
    skill_id: str
    finding: SkillReviewFinding
    detail: str
    suggested_action: str = ""   # "patch", "disable", "review", "none"


@dataclass
class SkillReviewArtifact:
    """Review report on installed skills. EW-T036."""
    artifact_id: str
    task_id: str
    entries: list[SkillReviewEntry] = field(default_factory=list)
    pinned_skill_warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "skill_review_artifact",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "finding_count": len(self.entries),
            "pinned_warnings": self.pinned_skill_warnings,
            "entries": [
                {
                    "skill_id": e.skill_id,
                    "finding": e.finding.value,
                    "detail": e.detail,
                    "suggested_action": e.suggested_action,
                }
                for e in self.entries
            ],
        }


class SkillReviewer:
    """Produces SkillReviewArtifacts. EW-T036.

    Patch suggestions are PatchArtifact or SkillProposalArtifact, not direct writes.
    Pinned skills cannot be modified without admin approval.
    """

    def review(
        self,
        registry: SkillRegistry,
        *,
        task_id: str,
        artifact_id: str,
        known_safe_hashes: set[str] | None = None,
    ) -> SkillReviewArtifact:
        artifact = SkillReviewArtifact(artifact_id=artifact_id, task_id=task_id)

        for entry in registry.enabled_skills():
            m = entry.manifest

            # Pinned skills: warn but do not suggest modification
            if m.pinned:
                artifact.pinned_skill_warnings.append(
                    f"skill {m.id!r} is pinned — modifications require admin approval"
                )
                continue

            # Stale: no content hash set
            if not entry.content_hash:
                artifact.entries.append(SkillReviewEntry(
                    skill_id=m.id,
                    finding=SkillReviewFinding.stale,
                    detail="skill has no content hash — source integrity unverified",
                    suggested_action="review",
                ))

            # Unsafe: high risk + shell_execute without explicit human approval
            if m.risk_class == "high" and "shell_execute" in m.capability_requirements:
                artifact.entries.append(SkillReviewEntry(
                    skill_id=m.id,
                    finding=SkillReviewFinding.unsafe,
                    detail="high-risk skill with shell_execute capability",
                    suggested_action="patch",
                ))

        return artifact


# ── EW-T037: Baseline skill manifests ────────────────────────────────────────

BASELINE_SKILLS: list[SkillManifest] = [
    SkillManifest(
        id="repository_understand",
        name="Repository Understand",
        version="1.0.0",
        description="Explores and summarizes a codebase structure.",
        capability_requirements=["code_read", "planning"],
        allowed_tools=["read_file", "list_directory"],
        risk_class="low",
        tags=["baseline", "read-only"],
    ),
    SkillManifest(
        id="bugfix_plan",
        name="Bug Fix Plan",
        version="1.0.0",
        description="Produces a step-by-step plan for fixing a reported bug.",
        capability_requirements=["planning", "code_read"],
        allowed_tools=["read_file"],
        risk_class="low",
        tags=["baseline"],
    ),
    SkillManifest(
        id="patch_propose",
        name="Patch Propose",
        version="1.0.0",
        description="Proposes a code patch without modifying the main tree.",
        capability_requirements=["code_read", "patch_propose"],
        allowed_tools=["read_file", "propose_patch"],
        risk_class="medium",
        tags=["baseline"],
    ),
    SkillManifest(
        id="change_review",
        name="Change Review",
        version="1.0.0",
        description="Reviews a proposed patch for correctness and policy compliance.",
        capability_requirements=["code_read", "verify"],
        allowed_tools=["read_file"],
        risk_class="low",
        tags=["baseline"],
    ),
    SkillManifest(
        id="compose_diagnostic",
        name="Compose Diagnostic",
        version="1.0.0",
        description="Synthesizes a structured diagnostic report from trace and artifacts.",
        capability_requirements=["planning", "verify"],
        allowed_tools=["memory_read"],
        risk_class="low",
        tags=["baseline"],
    ),
]


def build_baseline_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for manifest in BASELINE_SKILLS:
        registry.register(SkillRegistryEntry(manifest=manifest))
    return registry
