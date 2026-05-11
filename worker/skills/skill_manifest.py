"""SkillManifest: versioned, capability-bound skill descriptor. AWF-T027."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


VALID_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})

# Tools that elevate risk class — skill risk_class must not be "low" if any of these appear
_HIGH_RISK_TOOLS = frozenset({
    "run_shell", "shell_execute", "file_write", "memory_write", "provider_cloud_call",
})


@dataclass
class SkillManifest:
    """Immutable skill descriptor. AWF-T027.

    Skills are instruction-based workflows bound to an explicit capability set.
    They are not arbitrary executable code.
    """
    id: str
    version: str
    name: str
    description: str
    required_capabilities: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    risk_class: str = "medium"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    context_requirements: list[str] = field(default_factory=list)
    approval_requirements: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    owner: str = ""
    source: str = ""

    @property
    def content_hash(self) -> str:
        """Stable hash over security-relevant fields. Changes if policy changes. AWF-T027."""
        payload = json.dumps({
            "id": self.id,
            "version": self.version,
            "required_capabilities": sorted(self.required_capabilities),
            "allowed_tools": sorted(self.allowed_tools),
            "denied_tools": sorted(self.denied_tools),
            "risk_class": self.risk_class,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "required_capabilities": list(self.required_capabilities),
            "allowed_tools": list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
            "risk_class": self.risk_class,
            "context_requirements": list(self.context_requirements),
            "approval_requirements": list(self.approval_requirements),
            "tests": list(self.tests),
            "owner": self.owner,
            "source": self.source,
        }


def validate_skill_manifest(
    manifest: SkillManifest,
    *,
    known_capabilities: set[str] | None = None,
) -> list[str]:
    """Validate a SkillManifest. Returns list of error codes; empty = valid. AWF-T027."""
    errors: list[str] = []

    if not str(manifest.id).strip():
        errors.append("skill_manifest_invalid:id_required")
    if not str(manifest.version).strip():
        errors.append("skill_manifest_invalid:version_required")
    if not str(manifest.name).strip():
        errors.append("skill_manifest_invalid:name_required")
    if manifest.risk_class not in VALID_RISK_CLASSES:
        errors.append(f"skill_manifest_invalid:unknown_risk_class:{manifest.risk_class!r}")

    # Unknown required capability → deny load
    if known_capabilities is not None:
        for cap in manifest.required_capabilities:
            if cap not in known_capabilities:
                errors.append(f"skill_manifest_invalid:unknown_capability:{cap!r}")

    # Risk class must not be "low" if high-risk tools are in allowed_tools
    high_risk_used = frozenset(manifest.allowed_tools) & _HIGH_RISK_TOOLS
    if high_risk_used and manifest.risk_class == "low":
        errors.append(
            f"skill_manifest_invalid:risk_class_too_low_for_tools:{sorted(high_risk_used)}"
        )

    return errors
