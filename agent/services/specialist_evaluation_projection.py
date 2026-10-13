"""Pure specialist evaluation projection with native quality-score semantics."""

from dataclasses import asdict

from agent.services.specialist_model_factory import CandidateOutcome
from agent.services.specialist_promotion_gate import PromotionDecision


def specialist_evaluation_projection(winner: CandidateOutcome, decision: PromotionDecision) -> dict:
    result = winner.result.to_mapping()
    if (
        not winner.lineage.evaluated
        or winner.lineage.benchmark_result_digest != result["result_digest"]
        or decision.candidate_result_digest != result["result_digest"]
        or winner.result.holdout_digest != winner.lineage.holdout_digest
    ):
        raise ValueError("specialist_evaluation_projection_binding_invalid")
    return {
        "schema": "ananta.specialist-evaluation-projection.v1",
        "candidate_id": winner.candidate_id,
        "training_job_id": winner.outcome.job_id,
        "adapter_id": winner.outcome.adapter_id,
        "artifact_sha256": winner.outcome.adapter_digest,
        "benchmark": result,
        "lineage": winner.lineage.to_mapping(),
        "quality_decision": decision.to_mapping(),
        "passed": decision.allowed,
        "aggregate_score": float(winner.result.classification["macro_f1"]),
        "score_kind": "specialist_macro_f1",
        "production_release_evidence": False,
        "evaluations": {role: asdict(receipt) for role, receipt in winner.evaluations},
        "split_manifest_digest": winner.split_manifest_digest,
    }

