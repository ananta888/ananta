from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from agent.services.dspy_unsloth_handoff_service import DspyUnslothHandoffService
from agent.services.prompt_provenance import PromptProvenanceChain


def test_dspy_provenance_is_digest_only_and_keeps_non_applied_candidate() -> None:
    chain = PromptProvenanceChain().add_dspy_optimized_program(
        run_id="run-1",
        program_digest="a" * 64,
        dataset_digest="b" * 64,
        metric_digest="c" * 64,
        optimizer_digest="d" * 64,
        export_digest="e" * 64,
        applied=False,
        reason_not_applied="release_evidence_missing",
    )
    entry = chain.to_list()[0]
    assert entry["type"] == "dspy_optimized_program"
    assert entry["applied"] is False
    assert entry["reason_not_applied"] == "release_evidence_missing"


def test_unsloth_handoff_marks_synthetic_labels_and_never_creates_training_job() -> None:
    examples = [
        {"input": f"input-{index}", "output": f"output-{index}", "label_origin": "synthetic_teacher"}
        for index in range(5)
    ]
    attestations = DspyEvaluationAttestationService(b"u" * 32)
    evaluation = {
        "promotion_eligible": True,
        "candidate_program_digest": "a" * 64,
        "evaluation_digest": "b" * 64,
    }
    evaluation["attestation"] = attestations.issue(evaluation)
    result = DspyUnslothHandoffService(attestations).export(
        tenant_id="tenant-1",
        dataset_id="handoff-1",
        accepted_examples=examples,
        evaluation=evaluation,
        security_gate_passed=True,
        license_id="internal",
        run_id="run-1",
        program_digest="a" * 64,
        model_set_digest="c" * 64,
        metric_digest="d" * 64,
    )
    assert all(record["synthetic"] for record in result["records"])
    assert result["training_job_created"] is False
    assert result["promotion_performed"] is False
    assert result["manifest"]["lineage"]["run_id"] == "run-1"
