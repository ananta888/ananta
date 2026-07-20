"""Immutable deterministic speech-evidence resolution contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

RESOLUTION_CONTRACT_VERSION = "ananta.speech-evidence-resolution.v1"


@dataclass(frozen=True)
class ResolutionDecision:
    region_id: str
    selected_candidate_id: str | None
    supporting_candidate_ids: tuple[str, ...]
    supporting_lineage_digests: tuple[str, ...]
    reason_code: str
    decision_digest: str


@dataclass(frozen=True)
class SpeechEvidenceResolution:
    contract_version: str
    policy_version: str
    graph_digest: str
    decisions: tuple[ResolutionDecision, ...]
    unresolved_region_ids: tuple[str, ...]
    resolution_hash: str


def resolution_hash(
    *,
    policy_version: str,
    graph_digest: str,
    decisions: tuple[ResolutionDecision, ...],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "contract_version": RESOLUTION_CONTRACT_VERSION,
                "policy_version": policy_version,
                "graph_digest": graph_digest,
                "decisions": [
                    {
                        "region_id": item.region_id,
                        "selected_candidate_id": item.selected_candidate_id,
                        "supporting_candidate_ids": list(item.supporting_candidate_ids),
                        "supporting_lineage_digests": list(item.supporting_lineage_digests),
                        "reason_code": item.reason_code,
                        "decision_digest": item.decision_digest,
                    }
                    for item in decisions
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def decision_digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


__all__ = [
    "RESOLUTION_CONTRACT_VERSION",
    "ResolutionDecision",
    "SpeechEvidenceResolution",
    "decision_digest",
    "resolution_hash",
]
