from __future__ import annotations

from tests.test_peer_transcript_consensus import _candidate
from voice_runtime.peer_transcript_consensus import PeerTranscriptConflictGraphBuilder
from voice_runtime.peer_transcript_resolution import (
    LocalTranscriptDisplayOverrideStore,
    PeerTranscriptResolutionService,
)


def test_identical_graph_and_policy_produce_same_resolution_hash() -> None:
    candidates = (
        _candidate("candidate-a", "Guten Tag."),
        _candidate("candidate-b", "Guten Tag!"),
    )
    graph = PeerTranscriptConflictGraphBuilder().build(candidates)
    service = PeerTranscriptResolutionService()
    assert service.resolve(graph) == service.resolve(graph)
    assert service.resolve(graph).resolution_hash == service.resolve(graph).resolution_hash


def test_ambiguous_lexical_and_incompatible_source_regions_remain_unresolved() -> None:
    lexical = PeerTranscriptConflictGraphBuilder().build(
        (_candidate("candidate-a", "Guten Tag"), _candidate("candidate-b", "Schlechten Tag"))
    )
    incompatible = PeerTranscriptConflictGraphBuilder().build(
        (
            _candidate("candidate-a", "Guten Tag"),
            _candidate("candidate-b", "Guten Tag", source_family="foreign-audio"),
        )
    )
    service = PeerTranscriptResolutionService()
    assert service.resolve(lexical).unresolved_region_ids
    assert service.resolve(incompatible).unresolved_region_ids
    lexical_resolution = service.resolve(lexical)
    assert any(
        item.selected_candidate_id is None and "lexical" in item.reason_code
        for item in lexical_resolution.decisions
    )


def test_local_display_override_cannot_change_evidence_resolution_or_dataset_state() -> None:
    graph = PeerTranscriptConflictGraphBuilder().build(
        (_candidate("candidate-a", "Guten Tag."), _candidate("candidate-b", "Guten Tag!"))
    )
    resolution = PeerTranscriptResolutionService().resolve(graph)
    store = LocalTranscriptDisplayOverrideStore()
    region = resolution.decisions[0].region_id
    store.set(pair_id="pair-test", region_id=region, candidate_id="candidate-b")
    assert store.get(pair_id="pair-test", region_id=region) == "candidate-b"
    assert PeerTranscriptResolutionService().resolve(graph).resolution_hash == resolution.resolution_hash
