from __future__ import annotations

import pytest

from tests.speech_evidence_sync_support import digest
from voice_runtime.backends.base import TranscriptionCandidate, TranscriptionWord
from voice_runtime.peer_transcript_consensus import (
    PeerTranscriptCandidate,
    PeerTranscriptConflictGraphBuilder,
)


def _candidate(
    candidate_id: str,
    text: str,
    *,
    source_id: str | None = None,
    source_family: str = "audio-source-a",
    lineage: str | None = None,
    revision: int = 1,
    confidence: float = 0.9,
    words: tuple[TranscriptionWord, ...] = (),
    overlap: bool = False,
) -> PeerTranscriptCandidate:
    return PeerTranscriptCandidate(
        transcript=TranscriptionCandidate(
            candidate_id=candidate_id,
            backend="mock-asr",
            text=text,
            confidence=confidence,
            words=words,
            status="succeeded",
            lineage_id=lineage or candidate_id,
        ),
        source_id=source_id or candidate_id,
        source_family=source_family,
        contributor_digest=digest(f"contributor-{candidate_id}"),
        revision=revision,
        lineage_digest=digest(lineage or candidate_id),
        signature_digest=digest(f"signature-{candidate_id}"),
        authority_micros=800_000,
        quality_micros=800_000,
        speaker_overlap=overlap,
    )


@pytest.mark.parametrize(
    ("left", "right", "changes", "kind"),
    [
        ("Hallo Welt", "Hallo Welt", {}, "exact"),
        ("Hallo, Welt", "Hallo Welt", {}, "punctuation"),
        ("Hallo Welt", "Hallo Erde", {}, "lexical"),
        ("Hallo", "Hallo dort", {}, "insert"),
        ("Hallo dort", "Hallo", {}, "delete"),
        ("Hallo Welt", "Hallo Erde", {"confidence": 0.2}, "uncertain"),
        ("Hallo Welt", "Hallo Erde", {"overlap": True}, "speaker-overlap"),
        ("Hallo Welt", "Hallo Welt", {"source_family": "audio-source-b"}, "incompatible-source"),
    ],
)
def test_graph_classifies_conflicts_through_canonical_alignment(left, right, changes, kind) -> None:
    graph = PeerTranscriptConflictGraphBuilder().build(
        (_candidate("candidate-a", left), _candidate("candidate-b", right, **changes))
    )
    assert kind in {edge.kind for edge in graph.edges}
    assert all(len(edge.provenance) == 8 for edge in graph.edges)


def test_graph_classifies_equal_text_with_divergent_source_times_as_timing() -> None:
    left = _candidate(
        "candidate-a",
        "Hallo",
        words=(TranscriptionWord(0, 400, "Hallo", 0.9),),
    )
    right = _candidate(
        "candidate-b",
        "Hallo",
        words=(TranscriptionWord(900, 1200, "Hallo", 0.9),),
    )
    graph = PeerTranscriptConflictGraphBuilder().build((left, right))
    assert [edge.kind for edge in graph.edges] == ["timing"]


def test_higher_revision_supersedes_monotonically_without_deleting_predecessor() -> None:
    old = _candidate("candidate-old", "Alt", source_id="source-a", lineage="lineage-a", revision=1)
    new = _candidate("candidate-new", "Neu", source_id="source-a", lineage="lineage-a", revision=2)
    other = _candidate("candidate-other", "Neu", source_id="source-b")
    graph = PeerTranscriptConflictGraphBuilder().build((old, new, other))
    assert len(graph.nodes) == 3
    old_node = next(node for node in graph.nodes if node.candidate_id == "candidate-old")
    assert old_node.superseded_by == "candidate-new"
    assert "candidate-old" not in graph.active_candidate_ids
    assert all(edge.reference_candidate_id != "candidate-old" for edge in graph.edges)


def test_graph_is_deterministic_and_never_creates_unprovenanced_candidate() -> None:
    rows = (_candidate("candidate-b", "Hallo Welt"), _candidate("candidate-a", "Hallo Welt"))
    first = PeerTranscriptConflictGraphBuilder().build(rows)
    second = PeerTranscriptConflictGraphBuilder().build(tuple(reversed(rows)))
    assert first.graph_digest == second.graph_digest
    known = {node.candidate_id for node in first.nodes}
    assert all(set(edge.provenance[:2]) <= known for edge in first.edges)
