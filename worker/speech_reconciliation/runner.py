"""Single-task composition for the isolated reconciliation worker."""

from __future__ import annotations

import hashlib
from typing import Callable, Mapping

from ananta_contracts.speech_reconciliation import canonical_json
from voice_runtime.peer_transcript_consensus import PeerTranscriptCandidate
from worker.speech_reconciliation.asr_ensemble import SpeechAsrEnsemble
from worker.speech_reconciliation.audio_staging import SpeechAudioStager
from worker.speech_reconciliation.checkpointing import SpeechReconciliationCheckpointStore
from worker.speech_reconciliation.contracts import (
    SpeechReconciliationWorkerOutcome,
    SpeechReconciliationWorkerTask,
    assert_worker_outcome_matches_job,
)
from worker.speech_reconciliation.resolver import SpeechReconciliationResolver


class SpeechReconciliationRunner:
    """Execute one admitted attempt; it never creates/delegates another task."""

    def __init__(
        self,
        *,
        stager: SpeechAudioStager,
        ensemble: SpeechAsrEnsemble,
        resolver: SpeechReconciliationResolver,
        checkpoints: SpeechReconciliationCheckpointStore,
    ) -> None:
        self._stager = stager
        self._ensemble = ensemble
        self._resolver = resolver
        self._checkpoints = checkpoints

    def run(
        self,
        task: SpeechReconciliationWorkerTask,
        ciphertext: bytes,
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> Mapping[str, object]:
        with self._stager.stage(task, ciphertext, cancellation_check=cancellation_check) as staged:
            ensemble = self._ensemble.run(task, staged, cancellation_check=cancellation_check)
            successful = tuple(item for item in ensemble.candidates if item.status == "succeeded" and item.text)
            if not successful:
                state = canonical_json(
                    {
                        "schema": "ananta.speech-reconciliation-worker-state.v1",
                        "candidate_set_digest": ensemble.candidate_set_digest,
                        "candidates": [item.as_dict() for item in ensemble.candidates],
                        "graph_digest": None,
                        "resolution_hash": None,
                        "fusion_result_hash": None,
                        "publishable": False,
                    }
                )
                checkpoint = self._checkpoints.save(
                    task,
                    checkpoint_sequence=1,
                    stage="slow_asr",
                    state=state,
                )
                return _outcome(
                    task,
                    status="failed",
                    candidate_set_digest=ensemble.candidate_set_digest,
                    candidate_count=len(ensemble.candidates),
                    successful_candidate_count=0,
                    failed_candidate_count=len(ensemble.candidates),
                    quality_score_micros=_candidate_quality_micros(ensemble.candidates),
                    previous_quality_score_micros=_previous_candidate_quality_micros(ensemble.candidates),
                    graph_digest=None,
                    resolution_hash=None,
                    unresolved_count=0,
                    unresolved_region_ids=[],
                    unresolved_high_quality_conflict_count=0,
                    publishable=False,
                    reason_code=ensemble.reason_code or "speech_reconciliation_asr_failed",
                    checkpoint=checkpoint.to_dict(),
                    transcript=None,
                    retryable=False,
                )
            resolution = self._resolver.resolve(
                tuple(
                    PeerTranscriptCandidate(
                        transcript=item,
                        source_id=item.candidate_id,
                        source_family=staged.source_audio_digest,
                        contributor_digest=str(item.manifest_digest),
                        revision=1,
                        lineage_digest=hashlib.sha256(str(item.lineage_id or item.candidate_id).encode()).hexdigest(),
                        # Local worker attestation over already-admitted immutable
                        # candidate provenance; this is not a peer signature.
                        signature_digest=hashlib.sha256(
                            canonical_json(
                                {
                                    "binding_digest": task.binding_digest,
                                    "candidate_id": item.candidate_id,
                                    "candidate_provenance": dict(item.provenance),
                                }
                            )
                        ).hexdigest(),
                        authority_micros=500_000,
                        quality_micros=max(0, min(1_000_000, round((item.confidence or 0) * 1_000_000))),
                    )
                    for item in successful
                )
            )
            state = canonical_json(
                {
                    "schema": "ananta.speech-reconciliation-worker-state.v1",
                    "candidate_set_digest": ensemble.candidate_set_digest,
                    "candidates": [item.as_dict() for item in ensemble.candidates],
                    "graph_digest": resolution.graph.graph_digest,
                    "resolution_hash": resolution.peer_resolution.resolution_hash,
                    "fusion_result_hash": resolution.fusion_result_hash,
                    "publishable": resolution.publishable,
                }
            )
            checkpoint = self._checkpoints.save(
                task,
                checkpoint_sequence=1,
                stage="resolution",
                state=state,
            )
            status = ensemble.status if resolution.publishable else "partial"
            return _outcome(
                task,
                status=status,
                candidate_set_digest=ensemble.candidate_set_digest,
                candidate_count=len(ensemble.candidates),
                successful_candidate_count=len(successful),
                failed_candidate_count=len(ensemble.candidates) - len(successful),
                quality_score_micros=_candidate_quality_micros(ensemble.candidates),
                previous_quality_score_micros=_previous_candidate_quality_micros(ensemble.candidates),
                graph_digest=resolution.graph.graph_digest,
                resolution_hash=resolution.peer_resolution.resolution_hash,
                unresolved_count=len(resolution.peer_resolution.unresolved_region_ids),
                unresolved_region_ids=sorted(resolution.peer_resolution.unresolved_region_ids),
                unresolved_high_quality_conflict_count=(resolution.unresolved_high_quality_conflict_count),
                publishable=resolution.publishable,
                reason_code=resolution.reason_code,
                checkpoint=checkpoint.to_dict(),
                transcript=resolution.transcript.as_dict() if resolution.transcript is not None else None,
                retryable=False,
            )


def _outcome(
    task: SpeechReconciliationWorkerTask,
    **values: object,
) -> dict[str, object]:
    job = task.job
    outcome = SpeechReconciliationWorkerOutcome.from_mapping(
        {
            "contract_version": task.contract_version,
            "job_id": job.job_id,
            "attempt_id": job.attempt_id,
            "fencing_token_digest": job.fencing_token_digest,
            "fencing_epoch": job.fencing_epoch,
            "consent_id": job.consent_id,
            "consent_version": job.consent_version,
            "revocation_epoch": job.revocation_epoch,
            "input_manifest_digest": job.input_manifest_digest,
            "policy_digest": job.policy_digest,
            "ledger_sequence": job.ledger_sequence,
            "key_epoch": job.key_epoch,
            **values,
        }
    )
    assert_worker_outcome_matches_job(job, outcome)
    return outcome.to_dict()


def _candidate_quality_micros(candidates) -> int:
    values = tuple(candidates)
    if not values:
        return 0
    successful = tuple(item for item in values if item.status == "succeeded" and item.text)
    if not successful:
        return 0
    confidence = sum(max(0.0, min(1.0, float(item.confidence or 0.0))) for item in successful)
    coverage = len(successful) / len(values)
    return max(0, min(1_000_000, round(coverage * confidence / len(successful) * 1_000_000)))


def _previous_candidate_quality_micros(candidates) -> int | None:
    values = tuple(candidates)
    if len(values) < 2:
        return None
    return _candidate_quality_micros(values[: max(1, len(values) // 2)])


__all__ = ["SpeechReconciliationRunner"]
