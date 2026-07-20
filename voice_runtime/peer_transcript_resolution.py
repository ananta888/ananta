"""Deterministic peer-evidence resolution and isolated local display choices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ananta_contracts.speech_evidence_resolution import (
    RESOLUTION_CONTRACT_VERSION,
    ResolutionDecision,
    SpeechEvidenceResolution,
    decision_digest,
    resolution_hash,
)
from voice_runtime.peer_transcript_consensus import ConflictEdge, PeerTranscriptConflictGraph


@dataclass(frozen=True)
class SpeechResolutionPolicy:
    version: str = "speech-peer-resolution-policy.v1"
    minimum_score_micros: int = 1_100_000
    minimum_margin_micros: int = 150_000
    source_alignment_weight_micros: int = 300_000


class PeerTranscriptResolutionService:
    def __init__(self, policy: SpeechResolutionPolicy = SpeechResolutionPolicy()) -> None:
        self._policy = policy

    def resolve(self, graph: PeerTranscriptConflictGraph) -> SpeechEvidenceResolution:
        nodes = {node.candidate_id: node for node in graph.nodes if node.superseded_by is None}
        decisions: list[ResolutionDecision] = []
        for edge in sorted(graph.edges, key=lambda item: item.edge_id):
            decisions.append(self._resolve_edge(edge, nodes))
        unresolved = tuple(item.region_id for item in decisions if item.selected_candidate_id is None)
        rows = tuple(decisions)
        return SpeechEvidenceResolution(
            contract_version=RESOLUTION_CONTRACT_VERSION,
            policy_version=self._policy.version,
            graph_digest=graph.graph_digest,
            decisions=rows,
            unresolved_region_ids=unresolved,
            resolution_hash=resolution_hash(
                policy_version=self._policy.version,
                graph_digest=graph.graph_digest,
                decisions=rows,
            ),
        )

    def _resolve_edge(self, edge: ConflictEdge, nodes: Mapping[str, object]) -> ResolutionDecision:
        left = nodes[edge.reference_candidate_id]
        right = nodes[edge.candidate_id]
        if edge.kind in {"incompatible-source", "uncertain", "speaker-overlap", "lexical", "insert", "delete"}:
            return self._decision(edge, None, (), (), f"speech_resolution_{edge.kind}_quarantined")
        left_score = left.authority_micros + left.quality_micros  # type: ignore[attr-defined]
        right_score = right.authority_micros + right.quality_micros  # type: ignore[attr-defined]
        if left.source_family == right.source_family:  # type: ignore[attr-defined]
            left_score += self._policy.source_alignment_weight_micros
            right_score += self._policy.source_alignment_weight_micros
        if max(left_score, right_score) < self._policy.minimum_score_micros:
            return self._decision(edge, None, (), (), "speech_resolution_quality_insufficient")
        if edge.kind in {"exact", "punctuation", "timing"}:
            selected = min(edge.reference_candidate_id, edge.candidate_id)
            candidates = tuple(sorted({edge.reference_candidate_id, edge.candidate_id}))
            # Correlated revisions/models are represented by one lineage vote.
            lineages = tuple(
                sorted(
                    {
                        nodes[candidate_id].lineage_digest  # type: ignore[attr-defined]
                        for candidate_id in candidates
                    }
                )
            )
            return self._decision(edge, selected, candidates, lineages, f"speech_resolution_{edge.kind}")
        if abs(left_score - right_score) < self._policy.minimum_margin_micros:
            return self._decision(edge, None, (), (), "speech_resolution_margin_insufficient")
        selected = edge.reference_candidate_id if left_score > right_score else edge.candidate_id
        lineage = nodes[selected].lineage_digest  # type: ignore[attr-defined]
        return self._decision(edge, selected, (selected,), (lineage,), "speech_resolution_weighted")

    @staticmethod
    def _decision(
        edge: ConflictEdge,
        selected: str | None,
        candidates: tuple[str, ...],
        lineages: tuple[str, ...],
        reason: str,
    ) -> ResolutionDecision:
        raw = {
            "region_id": edge.edge_id,
            "selected_candidate_id": selected,
            "supporting_candidate_ids": list(candidates),
            "supporting_lineage_digests": list(lineages),
            "reason_code": reason,
            "edge_provenance": list(edge.provenance),
        }
        return ResolutionDecision(
            region_id=edge.edge_id,
            selected_candidate_id=selected,
            supporting_candidate_ids=candidates,
            supporting_lineage_digests=lineages,
            reason_code=reason,
            decision_digest=decision_digest(raw),
        )


class LocalTranscriptDisplayOverrideStore:
    """Personal display state with no path into evidence or dataset services."""

    def __init__(self, *, maximum_entries: int = 1024) -> None:
        self._maximum = maximum_entries
        self._values: dict[tuple[str, str], str] = {}

    def set(self, *, pair_id: str, region_id: str, candidate_id: str) -> None:
        key = (pair_id, region_id)
        if key not in self._values and len(self._values) >= self._maximum:
            oldest = next(iter(self._values))
            self._values.pop(oldest)
        self._values[key] = candidate_id

    def get(self, *, pair_id: str, region_id: str) -> str | None:
        return self._values.get((pair_id, region_id))

    def clear_pair(self, pair_id: str) -> None:
        self._values = {key: value for key, value in self._values.items() if key[0] != pair_id}


__all__ = [
    "LocalTranscriptDisplayOverrideStore",
    "PeerTranscriptResolutionService",
    "SpeechResolutionPolicy",
]
