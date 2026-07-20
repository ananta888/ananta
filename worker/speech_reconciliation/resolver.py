"""Deterministic reconciliation over the canonical Fusion and Peer ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.speech_evidence_resolution import SpeechEvidenceResolution
from voice_runtime.backends.base import TranscriptionCandidate, TranscriptionResult
from voice_runtime.fusion import DeterministicFusionService, FusionOutcome
from voice_runtime.fusion.alignment import candidate_tokens, normalize_token, tokenize
from voice_runtime.peer_transcript_consensus import (
    MINIMUM_CONFLICT_CONFIDENCE_MICROS,
    PeerTranscriptCandidate,
    PeerTranscriptConflictGraph,
    PeerTranscriptConflictGraphBuilder,
)
from voice_runtime.peer_transcript_resolution import PeerTranscriptResolutionService


class SpeechReconciliationResolutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ConflictGraphPort(Protocol):
    def build(self, candidates: tuple[PeerTranscriptCandidate, ...]) -> PeerTranscriptConflictGraph: ...


class PeerResolutionPort(Protocol):
    def resolve(self, graph: PeerTranscriptConflictGraph) -> SpeechEvidenceResolution: ...


class FusionPort(Protocol):
    def fuse(self, candidates: tuple[TranscriptionCandidate, ...]) -> FusionOutcome: ...


@dataclass(frozen=True)
class SpeechReconciliationResolution:
    graph: PeerTranscriptConflictGraph
    peer_resolution: SpeechEvidenceResolution
    transcript: TranscriptionResult | None
    fusion_result_hash: str | None
    unresolved_high_quality_conflict_count: int
    publishable: bool
    reason_code: str


class SpeechReconciliationResolver:
    """Compose canonical ports without owning alignment or voting algorithms."""

    def __init__(
        self,
        *,
        graph_builder: ConflictGraphPort | None = None,
        peer_resolution: PeerResolutionPort | None = None,
        fusion: FusionPort | None = None,
    ) -> None:
        self._graphs = graph_builder or PeerTranscriptConflictGraphBuilder()
        self._peer_resolution = peer_resolution or PeerTranscriptResolutionService()
        self._fusion = fusion or DeterministicFusionService()

    def resolve(
        self,
        candidates: tuple[PeerTranscriptCandidate, ...],
    ) -> SpeechReconciliationResolution:
        graph = self._graphs.build(candidates)
        resolution = self._peer_resolution.resolve(graph)
        self._validate_peer_resolution(graph, resolution)
        high_quality_conflicts = self._count_unresolved_high_quality_conflicts(
            graph,
            resolution,
        )
        if resolution.unresolved_region_ids:
            return SpeechReconciliationResolution(
                graph=graph,
                peer_resolution=resolution,
                transcript=None,
                fusion_result_hash=None,
                unresolved_high_quality_conflict_count=high_quality_conflicts,
                publishable=False,
                reason_code="speech_reconciliation_conflicts_unresolved",
            )
        active = frozenset(graph.active_candidate_ids)
        transcripts = tuple(
            sorted(
                (item.transcript for item in candidates if item.transcript.candidate_id in active),
                key=lambda item: item.candidate_id,
            )
        )
        if not transcripts:
            raise SpeechReconciliationResolutionError("speech_reconciliation_no_active_candidate")
        outcome = self._fusion.fuse(transcripts)
        self._validate_fusion(outcome, transcripts)
        return SpeechReconciliationResolution(
            graph=graph,
            peer_resolution=resolution,
            transcript=outcome.result,
            fusion_result_hash=outcome.result_hash,
            unresolved_high_quality_conflict_count=high_quality_conflicts,
            publishable=True,
            reason_code="speech_reconciliation_resolved",
        )

    @staticmethod
    def _validate_peer_resolution(
        graph: PeerTranscriptConflictGraph,
        resolution: SpeechEvidenceResolution,
    ) -> None:
        if resolution.graph_digest != graph.graph_digest:
            raise SpeechReconciliationResolutionError("speech_reconciliation_resolution_graph_mismatch")
        known_candidates = {item.candidate_id for item in graph.nodes}
        known_lineages = {item.lineage_digest for item in graph.nodes}
        known_regions = {item.edge_id for item in graph.edges}
        if set(resolution.unresolved_region_ids) - known_regions:
            raise SpeechReconciliationResolutionError("speech_reconciliation_resolution_region_unknown")
        for decision in resolution.decisions:
            identifiers = set(decision.supporting_candidate_ids)
            if decision.selected_candidate_id is not None:
                identifiers.add(decision.selected_candidate_id)
            if decision.region_id not in known_regions or identifiers - known_candidates:
                raise SpeechReconciliationResolutionError("speech_reconciliation_resolution_candidate_unknown")
            if set(decision.supporting_lineage_digests) - known_lineages:
                raise SpeechReconciliationResolutionError("speech_reconciliation_resolution_lineage_unknown")

    @staticmethod
    def _validate_fusion(
        outcome: FusionOutcome,
        candidates: tuple[TranscriptionCandidate, ...],
    ) -> None:
        result = outcome.result
        if not result.provenance_valid or not result.text.strip():
            raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_unprovenanced")
        by_id = {item.candidate_id: item for item in candidates}
        trace = result.decision_trace.get("token_provenance")
        if not isinstance(trace, tuple | list):
            raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_provenance_missing")
        output_tokens = tokenize(result.text)
        if len(trace) != len(output_tokens):
            raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_provenance_mismatch")
        for output_index, item in enumerate(trace):
            if not isinstance(item, dict):
                raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_provenance_invalid")
            candidate = by_id.get(str(item.get("candidate_id") or ""))
            source_index = item.get("source_token_index")
            if candidate is None or isinstance(source_index, bool) or not isinstance(source_index, int):
                raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_source_unknown")
            sources = candidate_tokens(candidate)
            if not 0 <= source_index < len(sources):
                raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_source_unknown")
            if (
                normalize_token(sources[source_index].text) != normalize_token(output_tokens[output_index])
                or str(item.get("token") or "") != output_tokens[output_index]
            ):
                raise SpeechReconciliationResolutionError("speech_reconciliation_fusion_token_invented")

    @staticmethod
    def _count_unresolved_high_quality_conflicts(
        graph: PeerTranscriptConflictGraph,
        resolution: SpeechEvidenceResolution,
    ) -> int:
        """Count only unresolved semantic disagreements with strong inputs.

        Generic unresolved regions include incompatible sources, speaker
        overlap and low-confidence spans.  Those conditions must never ask the
        Hub for another compute wave.  A high-quality conflict is therefore a
        semantic edit (lexical/insert/delete) whose two active candidates both
        meet the conflict graph's explicit confidence floor.
        """

        unresolved = frozenset(resolution.unresolved_region_ids)
        nodes = {node.candidate_id: node for node in graph.nodes}
        return sum(
            1
            for edge in graph.edges
            if edge.edge_id in unresolved
            and edge.kind in {"lexical", "insert", "delete"}
            and nodes[edge.reference_candidate_id].quality_micros >= MINIMUM_CONFLICT_CONFIDENCE_MICROS
            and nodes[edge.candidate_id].quality_micros >= MINIMUM_CONFLICT_CONFIDENCE_MICROS
        )


__all__ = [
    "ConflictGraphPort",
    "FusionPort",
    "PeerResolutionPort",
    "SpeechReconciliationResolution",
    "SpeechReconciliationResolutionError",
    "SpeechReconciliationResolver",
]
