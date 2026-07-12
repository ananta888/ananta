from __future__ import annotations

from dataclasses import dataclass

from voice_runtime.backends.base import TranscriptionCandidate


class CandidateLineageError(RuntimeError):
    pass


@dataclass(frozen=True)
class LineageValidation:
    lineage_roots: dict[str, str]
    child_count: int


class CandidateLineageValidator:
    _RELATIONS = frozenset({"audio_variant", "context_derived"})

    def validate(self, candidates: tuple[TranscriptionCandidate, ...]) -> LineageValidation:
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        if len(by_id) != len(candidates):
            raise CandidateLineageError("candidate lineage contains duplicate candidate IDs")
        for candidate in candidates:
            if candidate.candidate_id in candidate.parent_candidate_ids:
                raise CandidateLineageError("candidate lineage contains a self-cycle")
            missing = set(candidate.parent_candidate_ids) - set(by_id)
            if missing:
                raise CandidateLineageError("candidate lineage references a missing parent")
        self._reject_cycles(by_id)

        roots: dict[str, str] = {}
        child_count = 0
        for candidate in candidates:
            lineage_id = candidate.lineage_id or candidate.candidate_id
            if not candidate.parent_candidate_ids:
                if lineage_id != candidate.candidate_id:
                    raise CandidateLineageError("candidate lineage root does not reference itself")
                roots[lineage_id] = candidate.candidate_id
                continue
            child_count += 1
            relation = str(candidate.provenance.get("lineage_relation") or "")
            if relation not in self._RELATIONS:
                raise CandidateLineageError("candidate lineage child has no recognized relation")
            parents = tuple(by_id[parent_id] for parent_id in candidate.parent_candidate_ids)
            if any((parent.lineage_id or parent.candidate_id) != lineage_id for parent in parents):
                raise CandidateLineageError("candidate lineage child and parent roots are inconsistent")
            if any(parent.source_audio_digest != candidate.source_audio_digest for parent in parents):
                raise CandidateLineageError("candidate lineage source audio provenance is inconsistent")
            if relation == "audio_variant":
                self._validate_audio_variant(candidate, parents)

        for candidate in candidates:
            lineage_id = candidate.lineage_id or candidate.candidate_id
            if lineage_id not in roots:
                raise CandidateLineageError("candidate lineage has no reachable root")
        return LineageValidation(lineage_roots=roots, child_count=child_count)

    @staticmethod
    def _reject_cycles(by_id: dict[str, TranscriptionCandidate]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(candidate_id: str) -> None:
            if candidate_id in visiting:
                raise CandidateLineageError("candidate lineage contains a cycle")
            if candidate_id in visited:
                return
            visiting.add(candidate_id)
            for parent_id in by_id[candidate_id].parent_candidate_ids:
                visit(parent_id)
            visiting.remove(candidate_id)
            visited.add(candidate_id)

        for candidate_id in by_id:
            visit(candidate_id)

    @staticmethod
    def _validate_audio_variant(
        candidate: TranscriptionCandidate,
        parents: tuple[TranscriptionCandidate, ...],
    ) -> None:
        if not candidate.provenance.get("audio_variant_profile"):
            raise CandidateLineageError("audio variant lineage lacks variant provenance")
        for parent in parents:
            if candidate.audio_variant_id == parent.audio_variant_id:
                raise CandidateLineageError("audio variant lineage reuses its parent variant ID")
            if candidate.backend != parent.backend:
                raise CandidateLineageError("audio variant lineage changes backend identity")
            if candidate.status != "succeeded" or parent.status != "succeeded":
                continue
            identity = (
                candidate.model,
                candidate.model_revision,
                candidate.manifest_digest,
                candidate.execution_location,
                candidate.synthetic,
            )
            parent_identity = (
                parent.model,
                parent.model_revision,
                parent.manifest_digest,
                parent.execution_location,
                parent.synthetic,
            )
            if identity != parent_identity:
                raise CandidateLineageError("audio variant lineage changes model provenance")
