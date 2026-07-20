"""Peer transcript conflict graph built on the canonical fusion alignment."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from voice_runtime.backends.base import TranscriptionCandidate
from voice_runtime.fusion.alignment import AlignmentSpan, align_candidates, normalize_token

ConflictKind = Literal[
    "exact",
    "punctuation",
    "lexical",
    "timing",
    "insert",
    "delete",
    "uncertain",
    "speaker-overlap",
    "incompatible-source",
]
_PUNCTUATION_RE = re.compile(r"[^\w]+", re.UNICODE)
MINIMUM_CONFLICT_CONFIDENCE_MICROS = 350_000


@dataclass(frozen=True)
class PeerTranscriptCandidate:
    transcript: TranscriptionCandidate
    source_id: str
    source_family: str
    contributor_digest: str
    revision: int
    lineage_digest: str
    signature_digest: str
    authority_micros: int
    quality_micros: int
    speaker_overlap: bool = False


@dataclass(frozen=True)
class ConflictNode:
    candidate_id: str
    source_id: str
    source_family: str
    contributor_digest: str
    revision: int
    lineage_digest: str
    signature_digest: str
    authority_micros: int
    quality_micros: int
    text: str
    superseded_by: str | None


@dataclass(frozen=True)
class ConflictEdge:
    edge_id: str
    reference_candidate_id: str
    candidate_id: str
    kind: ConflictKind
    reference_start_index: int
    reference_end_index: int
    candidate_start_index: int
    candidate_end_index: int
    start_ms: int | None
    end_ms: int | None
    reference_text: str
    candidate_text: str
    provenance: tuple[str, ...]


@dataclass(frozen=True)
class PeerTranscriptConflictGraph:
    version: str
    nodes: tuple[ConflictNode, ...]
    edges: tuple[ConflictEdge, ...]
    active_candidate_ids: tuple[str, ...]
    graph_digest: str


class PeerTranscriptConflictGraphBuilder:
    """Consumes fusion alignment; it deliberately owns no alignment algorithm."""

    def build(self, candidates: tuple[PeerTranscriptCandidate, ...]) -> PeerTranscriptConflictGraph:
        self._validate(candidates)
        ordered = tuple(sorted(candidates, key=lambda item: item.transcript.candidate_id))
        superseded = self._superseded(ordered)
        nodes = tuple(
            ConflictNode(
                candidate_id=item.transcript.candidate_id,
                source_id=item.source_id,
                source_family=item.source_family,
                contributor_digest=item.contributor_digest,
                revision=item.revision,
                lineage_digest=item.lineage_digest,
                signature_digest=item.signature_digest,
                authority_micros=item.authority_micros,
                quality_micros=item.quality_micros,
                text=item.transcript.text,
                superseded_by=superseded.get(item.transcript.candidate_id),
            )
            for item in ordered
        )
        active = tuple(item for item in ordered if item.transcript.candidate_id not in superseded)
        edges: list[ConflictEdge] = []
        if active:
            reference = active[0]
            for candidate in active[1:]:
                alignment = align_candidates(reference.transcript, candidate.transcript)
                for ordinal, span in enumerate(alignment.spans):
                    kind = self._classify(reference, candidate, span)
                    edge_id = hashlib.sha256(
                        (
                            "ananta.peer-transcript-edge.v1\0"
                            f"{reference.transcript.candidate_id}\0{candidate.transcript.candidate_id}\0"
                            f"{ordinal}\0{kind}\0{span.reference_start_index}\0{span.candidate_start_index}"
                        ).encode()
                    ).hexdigest()
                    edges.append(
                        ConflictEdge(
                            edge_id=edge_id,
                            reference_candidate_id=reference.transcript.candidate_id,
                            candidate_id=candidate.transcript.candidate_id,
                            kind=kind,
                            reference_start_index=span.reference_start_index,
                            reference_end_index=span.reference_end_index,
                            candidate_start_index=span.candidate_start_index,
                            candidate_end_index=span.candidate_end_index,
                            start_ms=span.start_ms,
                            end_ms=span.end_ms,
                            reference_text=span.reference_text,
                            candidate_text=span.candidate_text,
                            provenance=(
                                reference.transcript.candidate_id,
                                candidate.transcript.candidate_id,
                                reference.source_id,
                                candidate.source_id,
                                reference.contributor_digest,
                                candidate.contributor_digest,
                                reference.lineage_digest,
                                candidate.lineage_digest,
                            ),
                        )
                    )
        payload = {
            "version": "ananta.peer-transcript-conflict-graph.v1",
            "nodes": [node.__dict__ for node in nodes],
            "edges": [edge.__dict__ for edge in edges],
            "active_candidate_ids": [item.transcript.candidate_id for item in active],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        return PeerTranscriptConflictGraph(
            version=payload["version"],
            nodes=nodes,
            edges=tuple(edges),
            active_candidate_ids=tuple(payload["active_candidate_ids"]),
            graph_digest=digest,
        )

    @staticmethod
    def _superseded(candidates: tuple[PeerTranscriptCandidate, ...]) -> dict[str, str]:
        grouped: dict[tuple[str, str], list[PeerTranscriptCandidate]] = {}
        for item in candidates:
            grouped.setdefault((item.source_id, item.lineage_digest), []).append(item)
        superseded: dict[str, str] = {}
        for rows in grouped.values():
            rows.sort(key=lambda item: (item.revision, item.transcript.candidate_id))
            newest = rows[-1]
            for older in rows[:-1]:
                if older.revision >= newest.revision:
                    raise ValueError("speech_evidence_revision_not_monotonic")
                superseded[older.transcript.candidate_id] = newest.transcript.candidate_id
        return superseded

    @staticmethod
    def _classify(
        reference: PeerTranscriptCandidate,
        candidate: PeerTranscriptCandidate,
        span: AlignmentSpan,
    ) -> ConflictKind:
        if reference.source_family != candidate.source_family:
            return "incompatible-source"
        if reference.speaker_overlap or candidate.speaker_overlap:
            return "speaker-overlap"
        confidence = min(reference.transcript.confidence or 0.0, candidate.transcript.confidence or 0.0)
        if round(confidence * 1_000_000) < MINIMUM_CONFLICT_CONFIDENCE_MICROS:
            return "uncertain"
        if span.operation in {"insert", "delete"}:
            changed_text = span.candidate_text if span.operation == "insert" else span.reference_text
            if changed_text and not _PUNCTUATION_RE.sub("", normalize_token(changed_text)):
                return "punctuation"
        if span.operation == "insert":
            return "insert"
        if span.operation == "delete":
            return "delete"
        if span.operation == "equal":
            reference_tokens = (
                reference.transcript.words[span.reference_start_index : span.reference_end_index]
                if reference.transcript.words
                else ()
            )
            candidate_tokens = (
                candidate.transcript.words[span.candidate_start_index : span.candidate_end_index]
                if candidate.transcript.words
                else ()
            )
            if (
                reference_tokens
                and candidate_tokens
                and any(
                    abs(left.start_ms - right.start_ms) > 250
                    for left, right in zip(reference_tokens, candidate_tokens, strict=False)
                )
            ):
                return "timing"
            return "exact"
        left = _PUNCTUATION_RE.sub("", normalize_token(span.reference_text))
        right = _PUNCTUATION_RE.sub("", normalize_token(span.candidate_text))
        return "punctuation" if left == right else "lexical"

    @staticmethod
    def _validate(candidates: tuple[PeerTranscriptCandidate, ...]) -> None:
        if not 1 <= len(candidates) <= 64:
            raise ValueError("speech_evidence_candidate_count_invalid")
        ids = [item.transcript.candidate_id for item in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("speech_evidence_candidate_duplicate")
        for item in candidates:
            if item.transcript.status != "succeeded" or not item.transcript.text:
                raise ValueError("speech_evidence_candidate_unusable")
            if not 1 <= item.revision <= 2**31 - 1:
                raise ValueError("speech_evidence_revision_invalid")
            if not 0 <= item.authority_micros <= 1_000_000 or not 0 <= item.quality_micros <= 1_000_000:
                raise ValueError("speech_evidence_weight_invalid")
            for digest in (item.contributor_digest, item.lineage_digest, item.signature_digest):
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    raise ValueError("speech_evidence_provenance_invalid")


__all__ = [
    "ConflictEdge",
    "ConflictKind",
    "ConflictNode",
    "MINIMUM_CONFLICT_CONFIDENCE_MICROS",
    "PeerTranscriptCandidate",
    "PeerTranscriptConflictGraph",
    "PeerTranscriptConflictGraphBuilder",
]
