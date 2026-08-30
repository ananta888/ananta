from __future__ import annotations

from agent.services.research_training_evaluation_attestation import ResearchTrainingEvaluationAttestation
from agent.services.research_training_evaluation_service import ResearchTrainingEvaluationService
from agent.services.research_training_release_gate import ResearchTrainingReleaseGate
from tests.research_training.helpers import DIGEST_A


def metrics(*, loss: float, accuracy: float, throughput: float = 100) -> dict[str, float]:
    return {
        "loss": loss,
        "accuracy": accuracy,
        "latency_ms": 5,
        "throughput_tokens_s": throughput,
        "peak_memory_bytes": 1024,
    }


def test_evaluation_and_release_are_automatic_and_source_grounded() -> None:
    evaluations = ResearchTrainingEvaluationService(
        ResearchTrainingEvaluationAttestation(b"e" * 32),
        allowed_source_refs=["SRC_TEST"],
        allowed_run_refs=["RUN_TEST"],
    )
    result = evaluations.compare(
        run_id="research-run-1", dataset_manifest_digest=DIGEST_A,
        base=metrics(loss=0.5, accuracy=0.7), sft=metrics(loss=0.4, accuracy=0.8),
        inference=metrics(loss=0.4, accuracy=0.8), source_refs=["SRC_TEST"], run_refs=["RUN_TEST"],
    )
    run = {
        "run_id": "research-run-1", "state": "completed", "automatic_release_eligible": True,
        "spec": {"dataset_manifest_digest": DIGEST_A},
    }
    decision = ResearchTrainingReleaseGate(evaluations).decide(run=run, evaluation=result)
    assert decision == {
        "schema": "ananta.research-training-release-decision.v1",
        "run_id": "research-run-1",
        "eligible": True,
        "reason_codes": [],
        "automatic": True,
        "production_eligible": False,
        "human_intervention_required": False,
    }


def test_missing_grounded_evidence_fails_closed_without_human_approval() -> None:
    evaluations = ResearchTrainingEvaluationService(ResearchTrainingEvaluationAttestation(b"e" * 32))
    result = evaluations.compare(
        run_id="research-run-1", dataset_manifest_digest=DIGEST_A,
        base=metrics(loss=0.5, accuracy=0.7), sft=metrics(loss=0.4, accuracy=0.8),
        inference=metrics(loss=0.4, accuracy=0.8),
    )
    assert result["release_eligible"] is False
    assert result["reason_codes"] == ["research_run_evidence_missing", "research_source_evidence_missing"]
    assert result["human_intervention_required"] is False
