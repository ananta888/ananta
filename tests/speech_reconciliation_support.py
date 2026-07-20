from __future__ import annotations

import hashlib
from typing import Any

from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationCheckpoint,
    SpeechReconciliationJob,
    SpeechReconciliationResult,
)
from ananta_contracts.speech_reconciliation_worker import SpeechReconciliationWorkerOutcome


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def job_payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "job_id": "speech-reconciliation-job-test",
        "attempt_id": "speech-reconciliation-attempt-test",
        "fencing_token_digest": digest("fence"),
        "fencing_epoch": 1,
        "consent_id": "speech-consent-test",
        "consent_version": 1,
        "revocation_epoch": 0,
        "input_manifest_digest": digest("manifest"),
        "input_lineage_digest": digest("lineage"),
        "input_artifact_ref": "artifact://speech-evidence/test/input.enc",
        "policy_digest": digest("policy"),
        "research_policy_ref": None,
        "source_duration_ms": 600_000,
        "max_compute_factor": 10,
        "ledger_sequence": 0,
        "key_epoch": 1,
        "deadline_at_ms": 2_000_000,
        "stage": "staging",
    }
    payload.update(changes)
    return payload


def job_contract(**changes: Any) -> SpeechReconciliationJob:
    return SpeechReconciliationJob.from_mapping(job_payload(**changes))


def checkpoint_payload(job: SpeechReconciliationJob, **changes: Any) -> dict[str, Any]:
    payload = {
        "contract_version": CONTRACT_VERSION,
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
        "checkpoint_digest": digest("checkpoint"),
        "checkpoint_ref": "artifact://speech-reconciliation-checkpoints/test/checkpoint.enc",
        "checkpoint_sequence": 1,
        "stage": "slow_asr",
        "state_digest": digest("state"),
    }
    payload.update(changes)
    return payload


def checkpoint_contract(job: SpeechReconciliationJob, **changes: Any) -> SpeechReconciliationCheckpoint:
    return SpeechReconciliationCheckpoint.from_mapping(checkpoint_payload(job, **changes))


def result_payload(job: SpeechReconciliationJob, **changes: Any) -> dict[str, Any]:
    payload = {
        "contract_version": CONTRACT_VERSION,
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
        "status": "completed",
        "dataset_manifest_digest": digest("resolved-dataset"),
        "dataset_artifact_ref": "artifact://speech-datasets/test/resolved-v1",
        "checkpoint_digest": digest("checkpoint"),
        "evaluation_digest": digest("evaluation"),
        "adapter_digest": None,
        "resolved_count": 3,
        "unresolved_count": 0,
        "rejected_count": 1,
        "quarantined_count": 0,
        "reason_code": "speech_reconciliation_completed",
    }
    payload.update(changes)
    return payload


def result_contract(job: SpeechReconciliationJob, **changes: Any) -> SpeechReconciliationResult:
    return SpeechReconciliationResult.from_mapping(result_payload(job, **changes))


def worker_outcome_payload(job: SpeechReconciliationJob, **changes: Any) -> dict[str, Any]:
    transcript = {
        "schema_version": "2.0",
        "text": "Hallo Welt",
        "language": "de",
        "duration_ms": 1000,
        "model": "model-a",
        "warnings": [],
        "segments": [],
        "pipeline": None,
        "confidence": 0.9,
        "raw_backend": "model-a",
        "rerun_backend": None,
        "stages": [],
        "candidates": [
            {
                "candidate_id": "candidate-a",
                "backend": "model-a",
                "model": "model-a",
                "model_revision": "revision-a",
                "device": "cpu",
                "execution_location": "speech-reconciliation-worker",
                "manifest_digest": digest("model-a"),
                "synthetic": False,
                "audio_variant_id": "original",
                "source_audio_digest": digest("audio"),
                "lineage_id": "lineage-a",
                "text": "Hallo Welt",
                "words": [],
                "segments": [],
                "language": "de",
                "duration_ms": 1000,
                "confidence": 0.9,
                "latency_ms": 10.0,
                "real_time_factor": 0.01,
                "status": "succeeded",
                "error": None,
                "warnings": [],
                "provenance": {"manifest_digest": digest("model-a")},
                "parent_candidate_ids": [],
            }
        ],
        "selected_candidate_id": "candidate-a",
        "fusion_strategy": "deterministic_consensus_v2",
        "disagreement_regions": [],
        "decision_trace": {
            "token_provenance": [
                {"candidate_id": "candidate-a", "source_token_index": 0, "token": "Hallo"},
                {"candidate_id": "candidate-a", "source_token_index": 1, "token": "Welt"},
            ]
        },
        "provenance": {"assembly": "candidate_tokens"},
        "provenance_valid": True,
        "turn_id": None,
        "revision": None,
        "authority": None,
        "source_digest": None,
        "semantic_frame_refs": [],
        "correction_state": None,
        "supersedes_revision": None,
    }
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
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
        "status": "completed",
        "candidate_set_digest": digest("candidate-set"),
        "candidate_count": 1,
        "successful_candidate_count": 1,
        "failed_candidate_count": 0,
        "quality_score_micros": 900_000,
        "previous_quality_score_micros": 800_000,
        "graph_digest": digest("graph"),
        "resolution_hash": digest("resolution"),
        "unresolved_count": 0,
        "unresolved_high_quality_conflict_count": 0,
        "publishable": True,
        "reason_code": "speech_reconciliation_resolved",
        "checkpoint": checkpoint_payload(job, stage="resolution"),
        "transcript": transcript,
        "retryable": False,
    }
    payload.update(changes)
    return payload


def worker_outcome_contract(
    job: SpeechReconciliationJob,
    **changes: Any,
) -> SpeechReconciliationWorkerOutcome:
    return SpeechReconciliationWorkerOutcome.from_mapping(worker_outcome_payload(job, **changes))
