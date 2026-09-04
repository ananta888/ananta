from __future__ import annotations

from dataclasses import replace

from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from agent.services.dspy_evaluation_bridge_service import DspyEvaluationBridgeService
from agent.services.dspy_program_artifact_store import DspyProgramArtifactStore
from agent.services.dspy_promotion_service import DspyPromotionService
from ananta_contracts.dspy_optimization import PromotionPlanV1, canonical_digest
from tests.dspy_optimization.helpers import program, spec
from worker.optimization.dspy.job_runner import DspyOptimizationJobRunner


class DeterministicEngine:
    def optimize(self, _spec, baseline, _records):
        return replace(baseline, program_id="planning-candidate", exporter_version="native-v2")


def _evaluation_input(*, program_digest: str, quality: float) -> dict:
    return {
        "program_digest": program_digest,
        "dataset_digest": "a" * 64,
        "metric_set_digest": "b" * 64,
        "provider_binding_id": "provider-binding:" + "c" * 64,
        "runtime_profile": "mock-v1",
        "test_split_digest": "d" * 64,
        "prompt_digest": "1" * 64,
        "dspy_version": "3.2.1",
        "hardware_profile": "deterministic-test-cpu",
        "cache_mode": "memory-only",
        "sampling_digest": "2" * 64,
        "seed": 7,
        "repetitions": 3,
        "warmups": 1,
        "sample_count": 40,
        "quality_standard_error": 0.001,
        "metrics": {
            "quality": quality,
            "parse_rate": 1,
            "policy_violations": 0,
            "tokens": 100,
            "cost_micros": 0,
            "latency_ms": 100,
        },
    }


def test_headless_baseline_candidate_evaluation_promotion_and_rollback(tmp_path) -> None:
    baseline = program()
    artifacts = DspyProgramArtifactStore(tmp_path / "artifacts")
    runner = DspyOptimizationJobRunner(
        DeterministicEngine(),
        artifacts,
        authorization_verifier=lambda job: job.get("authorization") == "hub-bound",
        workspace_root=tmp_path / "attempts",
    )
    job = {
        "authorization": "hub-bound",
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "spec": spec().to_dict(),
    }
    first = runner.run(job=job, baseline=baseline, records=[])
    second = runner.run(job={**job, "run_id": "run-2"}, baseline=baseline, records=[])
    assert first["state"] == second["state"] == "completed"
    assert first["program_digest"] == second["program_digest"]
    candidate = artifacts.get(tenant_id="tenant-1", run_id="run-1", digest=first["program_digest"])

    attestations = DspyEvaluationAttestationService(b"e" * 32)
    evaluation = DspyEvaluationBridgeService(attestations).compare(
        baseline=_evaluation_input(program_digest=baseline.digest, quality=0.5),
        candidate=_evaluation_input(program_digest=candidate.digest, quality=0.7),
    )
    plan = PromotionPlanV1(
        tenant_id="tenant-1",
        scope_id="planning-en-v1",
        candidate_digest=candidate.digest,
        baseline_digest=baseline.digest,
        evaluation_digest=evaluation["evaluation_digest"],
        dataset_digest="a" * 64,
        metric_set_digest="b" * 64,
        thresholds_digest=canonical_digest({"quality_delta": 0.02, "cost_ratio": 1.1}),
        expected_registry_revision=0,
        canary_percent=10,
        automatic_stop_reason_codes=["security_regression", "cost_regression"],
        minimum_sample_size=40,
    )
    promotions = DspyPromotionService(tmp_path / "promotions.sqlite3", attestations=attestations)
    promoted = promotions.promote_plan(plan=plan.to_dict(), evaluation=evaluation)
    rolled_back = promotions.rollback(
        tenant_id="tenant-1", scope_id="planning-en-v1", expected_revision=promoted["revision"]
    )
    assert promoted["active_digest"] == candidate.digest
    assert rolled_back["active_digest"] == baseline.digest
    assert rolled_back["human_intervention_required"] is False
