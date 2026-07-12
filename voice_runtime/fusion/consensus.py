from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, cast

from voice_runtime.backends.base import (
    DisagreementRegion,
    TranscriptionCandidate,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
)

from .alignment import (
    align_candidates,
    candidate_tokens,
    detokenize,
    differences,
    normalize_token,
    tokenize,
)
from .scoring import (
    AGREEMENT_SIGNAL_VERSION,
    CandidateScorer,
    CandidateScoringSignals,
    VersionedSignal,
)


@dataclass(frozen=True)
class FusionOutcome:
    result: TranscriptionResult
    result_hash: str


class DeterministicFusionService:
    """Conservative deterministic fusion with source-complete token lineage."""

    def __init__(self, scorer: CandidateScorer | None = None) -> None:
        self._scorer = scorer or CandidateScorer()

    def fuse(self, candidates: tuple[TranscriptionCandidate, ...]) -> FusionOutcome:
        successful = tuple(
            sorted(
                (
                    item
                    for item in candidates
                    if item.status == "succeeded" and item.text.strip()
                ),
                key=lambda item: item.candidate_id,
            )
        )
        if not successful:
            result = TranscriptionResult(
                text="",
                warnings=("fusion_no_successful_candidate",),
                candidates=candidates,
                fusion_strategy="deterministic_consensus_v2",
                provenance_valid=False,
            )
            return FusionOutcome(result=result, result_hash=_result_hash(result))

        engine_identities = {
            (item.backend, item.model_revision or "") for item in successful
        }
        allow_raw_confidence = len(engine_identities) == 1
        all_calibrated = all(
            self._scorer.confidence_is_comparable(candidate) for candidate in successful
        )
        confidence_mode = (
            "calibrated"
            if all_calibrated
            else "raw_same_engine"
            if allow_raw_confidence
            else "degraded_cross_engine_uncalibrated"
        )
        agreement_signals = _agreement_signals(successful)
        ranked: list[tuple[float, str, TranscriptionCandidate]] = []
        score_trace: dict[str, dict[str, object]] = {}
        for candidate in successful:
            score, trace = self._scorer.score(
                candidate,
                signals=CandidateScoringSignals(
                    agreement=agreement_signals.get(candidate.candidate_id)
                ),
                allow_uncalibrated_confidence=allow_raw_confidence,
                allow_calibrated_signals=all_calibrated,
            )
            score_trace[candidate.candidate_id] = trace
            ranked.append((score, candidate.candidate_id, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = ranked[0][2]

        regions = _disagreement_regions(
            selected=selected,
            candidates=successful,
            score_trace=score_trace,
        )
        assembled_tokens, token_provenance, assembled_words = _assemble_consensus_tokens(
            anchor=selected,
            candidates=successful,
            score_trace=score_trace,
            scorer=self._scorer,
            calibrate_confidence=confidence_mode == "calibrated",
        )
        assembled_text = detokenize(assembled_tokens)
        all_tokens_timed = all(item["time_source"] != "unavailable" for item in token_provenance)
        assembled_segments = _assembled_segments(
            selected=selected,
            text=assembled_text,
            words=assembled_words,
            confidence_mode=confidence_mode,
            fallback_confidence=cast(
                float | None,
                score_trace[selected.candidate_id]["confidence"],
            ),
        )
        comparable_confidences = [
            word.confidence
            for word in assembled_words
            if word.confidence is not None
        ]
        result_confidence = (
            sum(comparable_confidences) / len(comparable_confidences)
            if comparable_confidences
            and confidence_mode in {"calibrated", "raw_same_engine"}
            else cast(float | None, score_trace[selected.candidate_id]["confidence"])
            if confidence_mode in {"calibrated", "raw_same_engine"}
            else None
        )
        warnings = list(selected.warnings)
        if confidence_mode == "degraded_cross_engine_uncalibrated":
            warnings.append("fusion_cross_engine_confidence_degraded")
        if not all_tokens_timed:
            warnings.append("fusion_word_timestamps_unavailable")

        valid_candidate_ids = {item.candidate_id for item in successful}
        provenance_valid = _validate_token_provenance(
            assembled_tokens,
            token_provenance,
            successful,
        )
        if not provenance_valid:
            # Fail closed: a text whose tokens cannot be reconstructed from the
            # immutable candidate set must never leave fusion as usable output.
            assembled_text = ""
            assembled_segments = ()
            result_confidence = None
            warnings.append("fusion_unprovenanced_output_blocked")
        result = TranscriptionResult(
            text=assembled_text,
            language=selected.language,
            duration_ms=selected.duration_ms,
            model=selected.model,
            warnings=tuple(dict.fromkeys(warnings)),
            segments=assembled_segments,
            confidence=result_confidence,
            raw_backend=selected.backend,
            candidates=candidates,
            selected_candidate_id=selected.candidate_id,
            fusion_strategy="deterministic_consensus_v2",
            disagreement_regions=regions,
            decision_trace={
                "fusion_contract_version": "ananta.voice-fusion-decision.v2",
                "confidence_comparison_mode": confidence_mode,
                "candidate_scores": score_trace,
                "token_provenance": token_provenance,
                "lineages": {
                    item.candidate_id: item.lineage_id or item.candidate_id
                    for item in successful
                },
                "alignment_versions": {
                    "time": "time_v1",
                    "text_fallback": "unicode_text_v1",
                },
            },
            provenance={
                "assembly": "candidate_tokens",
                "source_candidate_ids": sorted(
                    {str(item["candidate_id"]) for item in token_provenance}
                ),
                "valid_candidate_ids": sorted(valid_candidate_ids),
                "synthetic": any(item.synthetic for item in successful),
                "execution_location": "voice-runtime",
                "timing_policy": "source_only_no_interpolation",
                "timed_token_count": sum(
                    item["time_source"] != "unavailable" for item in token_provenance
                ),
                "token_count": len(token_provenance),
            },
            provenance_valid=provenance_valid,
        )
        return FusionOutcome(result=result, result_hash=_result_hash(result))


def _disagreement_regions(
    *,
    selected: TranscriptionCandidate,
    candidates: tuple[TranscriptionCandidate, ...],
    score_trace: dict[str, dict[str, object]],
) -> tuple[DisagreementRegion, ...]:
    regions: list[DisagreementRegion] = []
    for candidate in candidates:
        if candidate.candidate_id == selected.candidate_id:
            continue
        for index, difference in enumerate(differences(selected, candidate)):
            regions.append(
                DisagreementRegion(
                    region_id=(
                        f"disagreement-{selected.candidate_id}-"
                        f"{candidate.candidate_id}-{index}"
                    ),
                    start_ms=difference.start_ms,
                    end_ms=difference.end_ms,
                    alternatives=(
                        _disagreement_alternative(
                            selected,
                            text=difference.reference_text,
                            start_index=difference.start_index,
                            end_index=difference.end_index,
                            alignment_method=difference.alignment_method,
                            alignment_operation=difference.operation,
                            score_trace=score_trace,
                        ),
                        _disagreement_alternative(
                            candidate,
                            text=difference.alternative_text,
                            start_index=difference.candidate_start_index,
                            end_index=difference.candidate_end_index,
                            alignment_method=difference.alignment_method,
                            alignment_operation=difference.operation,
                            score_trace=score_trace,
                        ),
                    ),
                    selected_candidate_id=selected.candidate_id,
                )
            )
    return tuple(
        sorted(
            regions,
            key=lambda item: (
                item.start_ms is None,
                item.start_ms if item.start_ms is not None else 0,
                item.end_ms if item.end_ms is not None else 0,
                item.region_id,
            ),
        )
    )


def _disagreement_alternative(
    candidate: TranscriptionCandidate,
    *,
    text: str,
    start_index: int,
    end_index: int,
    alignment_method: str,
    alignment_operation: str,
    score_trace: dict[str, dict[str, object]],
) -> dict[str, object]:
    trace = score_trace[candidate.candidate_id]
    return {
        "candidate_id": candidate.candidate_id,
        "text": text,
        "score": trace["score"],
        "scoring_schema_version": trace["scoring_schema_version"],
        "lineage_id": candidate.lineage_id or candidate.candidate_id,
        "source_token_range": [start_index, end_index],
        "alignment_method": alignment_method,
        "alignment_operation": alignment_operation,
        "source": {
            "backend": candidate.backend,
            "model": candidate.model,
            "model_revision": candidate.model_revision,
            "manifest_digest": candidate.manifest_digest,
            "audio_variant_id": candidate.audio_variant_id,
            "source_audio_digest": candidate.source_audio_digest,
        },
    }


def _result_hash(result: TranscriptionResult) -> str:
    payload = result.as_dict()
    _remove_nondeterministic_timings(payload)
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _remove_nondeterministic_timings(value: object) -> None:
    if isinstance(value, dict):
        for key in ("latency_ms", "real_time_factor", "runtime_ms"):
            value.pop(key, None)
        for child in value.values():
            _remove_nondeterministic_timings(child)
    elif isinstance(value, list):
        for child in value:
            _remove_nondeterministic_timings(child)


def _agreement_signals(
    candidates: tuple[TranscriptionCandidate, ...],
) -> dict[str, VersionedSignal]:
    representatives: dict[str, TranscriptionCandidate] = {}
    for candidate in candidates:
        lineage = candidate.lineage_id or candidate.candidate_id
        current = representatives.get(lineage)
        if current is None or candidate.candidate_id < current.candidate_id:
            representatives[lineage] = candidate

    result: dict[str, VersionedSignal] = {}
    for candidate in candidates:
        own_lineage = candidate.lineage_id or candidate.candidate_id
        peers = tuple(
            representatives[lineage]
            for lineage in sorted(representatives)
            if lineage != own_lineage
        )
        if not peers:
            continue
        normalized = tuple(normalize_token(item) for item in tokenize(candidate.text))
        ratios = [
            SequenceMatcher(
                a=normalized,
                b=tuple(normalize_token(item) for item in tokenize(peer.text)),
                autojunk=False,
            ).ratio()
            for peer in peers
        ]
        value = round(sum(ratios) / len(ratios), 6)
        result[candidate.candidate_id] = VersionedSignal(
            value=value,
            version=AGREEMENT_SIGNAL_VERSION,
            artifact_digest=_stable_digest(
                {
                    "version": AGREEMENT_SIGNAL_VERSION,
                    "candidate_id": candidate.candidate_id,
                    "candidate_tokens": normalized,
                    "peers": [
                        {
                            "candidate_id": peer.candidate_id,
                            "lineage_id": peer.lineage_id or peer.candidate_id,
                            "tokens": [
                                normalize_token(item) for item in tokenize(peer.text)
                            ],
                        }
                        for peer in peers
                    ],
                }
            ),
        )
    return result


def _assemble_consensus_tokens(
    *,
    anchor: TranscriptionCandidate,
    candidates: tuple[TranscriptionCandidate, ...],
    score_trace: dict[str, dict[str, object]],
    scorer: CandidateScorer,
    calibrate_confidence: bool,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...], tuple[TranscriptionWord, ...]]:
    anchor_tokens = tokenize(anchor.text)
    alignments = {
        candidate.candidate_id: align_candidates(anchor, candidate).project_to_reference()
        for candidate in candidates
    }
    source_tokens = {
        candidate.candidate_id: candidate_tokens(candidate) for candidate in candidates
    }
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    output: list[str] = []
    provenance: list[dict[str, object]] = []
    words: list[TranscriptionWord] = []
    for anchor_index, anchor_token in enumerate(anchor_tokens):
        lineage_options: dict[str, tuple[float, str, str | None, int | None]] = {}
        for candidate in candidates:
            aligned = alignments[candidate.candidate_id][anchor_index]
            score = float(cast(Any, score_trace[candidate.candidate_id]["score"]))
            lineage = candidate.lineage_id or candidate.candidate_id
            option = (score, candidate.candidate_id, aligned.token, aligned.candidate_index)
            previous = lineage_options.get(lineage)
            if previous is None or (score, candidate.candidate_id) > (
                previous[0],
                previous[1],
            ):
                lineage_options[lineage] = option

        grouped: dict[str, list[tuple[float, str, str | None, int | None]]] = {}
        for option in lineage_options.values():
            key = normalize_token(option[2]) if option[2] is not None else ""
            grouped.setdefault(key, []).append(option)
        ranked_forms = sorted(
            grouped.items(),
            key=lambda item: (
                -len(item[1]),
                -(sum(option[0] for option in item[1]) / len(item[1])),
                -max(option[0] for option in item[1]),
                item[0],
            ),
        )
        chosen_options = ranked_forms[0][1]
        chosen = sorted(chosen_options, key=lambda item: (-item[0], item[1]))[0]
        _score, candidate_id, token, candidate_index = chosen
        if token is None or candidate_index is None:
            continue
        source = by_id[candidate_id]
        source_token = source_tokens[candidate_id][candidate_index]
        output_index = len(output)
        output.append(token)
        if source_token.has_time:
            assert source_token.start_ms is not None and source_token.end_ms is not None
            words.append(
                TranscriptionWord(
                    start_ms=source_token.start_ms,
                    end_ms=source_token.end_ms,
                    text=token,
                    confidence=_source_confidence(
                        source,
                        candidate_index,
                        scorer=scorer,
                        calibrate=calibrate_confidence,
                    ),
                    candidate_id=candidate_id,
                )
            )
        provenance.append(
            {
                "token_index": output_index,
                "token": token,
                "candidate_id": candidate_id,
                "source_token_index": candidate_index,
                "lineage_id": source.lineage_id or source.candidate_id,
                "operation": "copied"
                if normalize_token(token) == normalize_token(anchor_token)
                else "consensus",
                "source_backend": source.backend,
                "source_model_revision": source.model_revision,
                "source_audio_digest": source.source_audio_digest,
                "audio_variant_id": source.audio_variant_id,
                "start_ms": source_token.start_ms,
                "end_ms": source_token.end_ms,
                "time_source": source_token.time_source,
            }
        )
    return tuple(output), tuple(provenance), tuple(words)


def _assembled_segments(
    *,
    selected: TranscriptionCandidate,
    text: str,
    words: tuple[TranscriptionWord, ...],
    confidence_mode: str,
    fallback_confidence: float | None,
) -> tuple[TranscriptionSegment, ...]:
    selected_start = min(
        (segment.start_ms for segment in selected.segments),
        default=0,
    )
    selected_end = max(
        (segment.end_ms for segment in selected.segments),
        default=selected.duration_ms or 0,
    )
    start_ms = min((word.start_ms for word in words), default=selected_start)
    end_ms = max((word.end_ms for word in words), default=selected_end)
    confidences = [word.confidence for word in words if word.confidence is not None]
    confidence = (
        sum(confidences) / len(confidences)
        if confidences and confidence_mode in {"calibrated", "raw_same_engine"}
        else fallback_confidence
        if confidence_mode in {"calibrated", "raw_same_engine"}
        else None
    )
    return (
        TranscriptionSegment(
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            confidence=confidence,
            backend="fusion",
            words=words,
        ),
    )


def _source_confidence(
    candidate: TranscriptionCandidate,
    candidate_index: int,
    *,
    scorer: CandidateScorer,
    calibrate: bool,
) -> float | None:
    words = candidate.words or tuple(
        word for segment in candidate.segments for word in segment.words
    )
    tokens = tokenize(candidate.text)
    if len(words) == len(tokens) and candidate_index < len(words):
        value = words[candidate_index].confidence
    else:
        value = candidate.confidence
    profile = scorer.calibration_profile(candidate)
    return (
        profile.calibrate(value)
        if calibrate and profile and profile.comparable
        else value
    )


def _validate_token_provenance(
    output: tuple[str, ...],
    provenance: tuple[dict[str, object], ...],
    candidates: tuple[TranscriptionCandidate, ...],
) -> bool:
    if len(output) != len(provenance):
        return False
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for index, (token, item) in enumerate(zip(output, provenance, strict=True)):
        candidate_id = str(item.get("candidate_id") or "")
        candidate = by_id.get(candidate_id)
        source_index = item.get("source_token_index")
        if candidate is None or not isinstance(source_index, int):
            return False
        source = tokenize(candidate.text)
        if source_index < 0 or source_index >= len(source):
            return False
        if item.get("token_index") != index:
            return False
        if normalize_token(token) != normalize_token(source[source_index]):
            return False
        time_source = item.get("time_source")
        start_ms = item.get("start_ms")
        end_ms = item.get("end_ms")
        if time_source == "unavailable" and (start_ms is not None or end_ms is not None):
            return False
        if time_source in {"word", "segment_bounds"} and not (
            isinstance(start_ms, int)
            and isinstance(end_ms, int)
            and 0 <= start_ms <= end_ms
        ):
            return False
    return True


def _stable_digest(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
