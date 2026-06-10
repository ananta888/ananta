"""Output contracts for domain discovery (codecompass_domain_analysis.v1).

See docs/codecompass-domain-discovery.md section 2 for field semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DOMAIN_ANALYSIS_SCHEMA = "codecompass_domain_analysis.v1"
DOMAIN_COUPLING_SCHEMA = "codecompass_domain_coupling.v1"

BOUNDARY_WARNING_TYPES = {
    "mutual_coupling",
    "layer_spans_domains",
    "heterogeneous_root",
    "descriptor_mismatch",
}


@dataclass
class DomainCandidate:
    domain_id: str
    display_name: str
    confidence: float
    root_paths: list[str] = field(default_factory=list)
    package_prefixes: list[str] = field(default_factory=list)
    technical_layers: list[str] = field(default_factory=list)
    core_records: list[str] = field(default_factory=list)
    record_count: int = 0
    member_record_ids: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    boundary_warnings: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_members: bool = False) -> dict[str, Any]:
        payload = {
            "domain_id": self.domain_id,
            "display_name": self.display_name,
            "confidence": round(float(self.confidence), 4),
            "root_paths": sorted(self.root_paths),
            "package_prefixes": sorted(self.package_prefixes),
            "technical_layers": sorted(self.technical_layers),
            "core_records": list(self.core_records),
            "record_count": int(self.record_count),
            "metrics": self.metrics,
            "boundary_warnings": self.boundary_warnings,
            "evidence": self.evidence,
        }
        if include_members:
            payload["member_record_ids"] = sorted(self.member_record_ids)
        return payload


def build_analysis_payload(
    *,
    project_root: str,
    generated_at: str,
    inputs: dict[str, int],
    domains: list[DomainCandidate],
    unassigned_records: list[str],
    warnings: list[str],
    include_members: bool = False,
) -> dict[str, Any]:
    return {
        "schema": DOMAIN_ANALYSIS_SCHEMA,
        "project_root": project_root,
        "generated_at": generated_at,
        "inputs": dict(sorted(inputs.items())),
        "domains": [
            candidate.to_dict(include_members=include_members)
            for candidate in sorted(domains, key=lambda c: c.domain_id)
        ],
        "unassigned_records": sorted(unassigned_records),
        "warnings": list(warnings),
    }
