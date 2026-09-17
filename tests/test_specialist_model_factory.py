"""Gate + Benchmark Model Factory: contract, data admission, split, metrics, gates."""

import json

import pytest

from agent.services.ml_intern_lora_eval_service import _SCORERS
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand, MlInternTrainingContractError
from agent.services.specialist_benchmark import (
    BENCHMARK_RESULT_SCHEMA,
    Prediction,
    ResourceUsage,
    SpecialistBenchmarkError,
    benchmark,
    predictions_from_outputs,
)
from agent.services.specialist_calibration import (
    SpecialistCalibrationError,
    abstention_report,
    brier_score,
    calibration_report,
    expected_calibration_error,
    select_threshold,
)
from agent.services.specialist_candidates import (
    ArtifactLineage,
    BaseModelCandidate,
    CandidatePlan,
    QualityThresholds,
    SpecialistCandidateError,
    TrainingBudget,
    choose_method,
    compare_candidates,
    trace_artifact,
)
from agent.services.specialist_dataset_split import (
    FrozenHoldout,
    SpecialistSplitError,
    admit_training_partitions,
    check_leakage,
    split_examples,
)
from agent.services.specialist_promotion_gate import SpecialistPromotionPolicy, decide_promotion
from agent.services.specialist_retraining_queue import RetrainingCandidate, RetrainingCandidateQueue
from agent.services.specialist_training_examples import (
    SpecialistExampleAdmission,
    SpecialistExampleError,
    TextRedactor,
    dataset_digest,
)
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy, score_specialist_output
from ananta_contracts.specialist_decision import (
    LABEL_SOURCE_DETERMINISTIC_GATE,
    LABEL_SOURCE_HUMAN,
    OUTPUT_SCHEMA,
    SpecialistContractError,
    builtin_contracts,
)

pytestmark = pytest.mark.timeout(60)

GATE = builtin_contracts()["code-risk-gate"]
ROUTER = builtin_contracts()["tool-router"]
COMPLETION = builtin_contracts()["task-completion-gate"]
DIGEST = "a" * 64


def raw_example(index, label="allow", *, source=LABEL_SOURCE_DETERMINISTIC_GATE, text=None, **extra):
    return {
        "specialist_id": "code-risk-gate",
        "contract_version": "v1",
        "input": {
            "action_kind": "apply_patch",
            "target_paths": [f"agent/services/module_{index}.py"],
            "diff_summary": text if text is not None else f"refactor helper {index} without behaviour change",
            "policy_profile": "default",
        },
        "label": label,
        "label_source": source,
        "provenance": {"origin_kind": "gate_run", "origin_ref": f"gate-run-{index}", "captured_at": "2026-09-17T10:00:00Z", "gate_name": "code_risk_gate"},
        **extra,
    }


def admitted(count=30, *, offset=0):
    admission = SpecialistExampleAdmission(GATE)
    raws = [raw_example(offset + i, "deny" if i % 3 == 0 else "allow") for i in range(count)]
    report = admission.admit_batch(raws)
    assert not report.rejected and not report.quarantined
    return report.admitted


# --- GBMF-001 contract --------------------------------------------------------


@pytest.mark.parametrize("name", ["tool-router", "code-risk-gate", "task-completion-gate"])
def test_builtin_specialists_share_one_generic_contract_with_versioned_digest(name):
    contract = builtin_contracts()[name]
    mapping = contract.to_mapping()
    assert mapping["schema"] == "ananta.specialist-decision-contract.v1" and contract.contract_key == f"{name}@v1"
    assert mapping["output_schema"]["label"]["enum"] == list(contract.allowed_labels)
    assert contract.digest == builtin_contracts()[name].digest and len(contract.digest) == 64
    assert contract.escalate_label in contract.allowed_labels


def test_output_is_typed_label_plus_confidence_never_free_text():
    decision = GATE.parse_output(json.dumps({"schema": OUTPUT_SCHEMA, "label": "deny", "confidence": 0.9}))
    assert decision == {"schema": OUTPUT_SCHEMA, "label": "deny", "confidence": 0.9}
    for bad, code in [
        ("I think this is fine", "specialist_output_json_invalid"),
        ({"label": "maybe", "confidence": 0.5}, "specialist_label_not_allowed"),
        ({"label": "allow"}, "specialist_confidence_required"),
        ({"label": "allow", "confidence": 1.5}, "specialist_confidence_invalid"),
        ({"label": "allow", "confidence": 0.5, "reason": "free text"}, "specialist_output_unknown_field"),
    ]:
        with pytest.raises(SpecialistContractError, match=code):
            GATE.parse_output(bad)
    low = GATE.apply_abstention({"label": "allow", "confidence": 0.3})
    assert low["label"] == "escalate" and low["deferred"] is True
    assert GATE.apply_abstention({"label": "allow", "confidence": 0.95})["label"] == "allow"


def test_input_schema_is_closed_and_typed():
    with pytest.raises(SpecialistContractError, match="specialist_input_unknown_field"):
        GATE.validate_input({**raw_example(1)["input"], "shell": "rm -rf /"})
    with pytest.raises(SpecialistContractError, match="specialist_input_type_mismatch"):
        GATE.validate_input({**raw_example(1)["input"], "target_paths": "not-a-list"})
    with pytest.raises(SpecialistContractError, match="specialist_input_field_missing"):
        ROUTER.validate_input({"step_goal": "x"})


# --- GBMF-002 examples --------------------------------------------------------


def test_examples_carry_provenance_contract_version_and_label_source():
    example = SpecialistExampleAdmission(GATE).admit(raw_example(7, "deny"))
    mapping = example.to_mapping()
    assert mapping["schema"] == "ananta.specialist-training-example.v1"
    assert mapping["provenance"]["origin_ref"] == "gate-run-7" and mapping["contract_version"] == "v1"
    assert mapping["label_source"] == LABEL_SOURCE_DETERMINISTIC_GATE and mapping["error_class"] == "none"
    with pytest.raises(SpecialistExampleError, match="specialist_example_label_not_reproducible"):
        SpecialistExampleAdmission(GATE).admit(raw_example(8, source=LABEL_SOURCE_HUMAN))
    assert SpecialistExampleAdmission(GATE).admit(raw_example(8, source=LABEL_SOURCE_HUMAN, reviewed=True)).label_source == "human"
    with pytest.raises(SpecialistExampleError, match="specialist_example_provenance_invalid"):
        SpecialistExampleAdmission(GATE).admit({**raw_example(9), "provenance": {"origin_kind": "guess", "origin_ref": "x", "captured_at": "now"}})
    with pytest.raises(SpecialistExampleError, match="specialist_example_provenance_invalid"):
        SpecialistExampleAdmission(GATE).admit(raw_example(9, provenance={"origin_kind": "gate_run", "origin_ref": "r", "captured_at": "2026", "run_id": "RUN-self-minted"}))


def test_secrets_and_pii_are_redacted_or_blocked_before_persistence():
    example = SpecialistExampleAdmission(GATE).admit(
        raw_example(1, text="contact peter@example.org, token=ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    )
    assert "example.org" not in json.dumps(example.input) and "ghp_" not in json.dumps(example.input)
    assert example.redactions == ("pii", "secret")
    with pytest.raises(SpecialistExampleError, match="specialist_example_blocked_secret"):
        SpecialistExampleAdmission(GATE).admit(raw_example(2, text="-----BEGIN RSA PRIVATE KEY----- MIIE"))
    text, findings = TextRedactor().redact("call +49 89 1234567 or api_key: verysecretvalue")
    assert text == "call [REDACTED_PII] or [REDACTED_SECRET]" and sorted(findings) == ["pii", "secret"]


def test_duplicates_collapse_and_contradictory_deterministic_labels_are_quarantined():
    admission = SpecialistExampleAdmission(GATE)
    report = admission.admit_batch([raw_example(1), raw_example(1), raw_example(2, "allow"), raw_example(2, "deny"), {"bogus": True}])
    assert len(report.admitted) == 1 and report.admitted[0].to_mapping()["input"]["target_paths"] == ["agent/services/module_1.py"]
    assert len(report.duplicates) == 1
    assert sorted(q.reason_code for q in report.quarantined) == ["contradictory_deterministic_label"] * 2
    assert report.rejected == [(4, "specialist_example_contract_mismatch")]
    reviewed = admission.admit_batch([raw_example(3, "allow"), raw_example(3, "deny", source=LABEL_SOURCE_HUMAN, reviewed=True)])
    assert [q.reason_code for q in reviewed.quarantined] == ["label_conflict_requires_review"] and len(reviewed.admitted) == 1
    assert dataset_digest(report.admitted) == dataset_digest(reversed(report.admitted))


# --- GBMF-003 split ---------------------------------------------------------


def test_group_wise_split_is_reproducible_and_leakage_free():
    examples = admitted(40)
    # Near-duplicate variants of one task share a group and must stay together.
    variants = SpecialistExampleAdmission(GATE).admit_batch(
        [raw_example(100, "deny", text=text) for text in ("  DELETE production TABLE   ", "delete production table", "Delete   Production Table")]
    ).admitted
    assert len(variants) == 3 and len({example.group_key for example in variants}) == 1
    manifest, partitions = split_examples(examples + variants, seed=7)
    again, _ = split_examples(list(reversed(examples + variants)), seed=7)
    assert manifest.manifest_digest == again.manifest_digest and manifest.to_mapping()["schema"] == "ananta.specialist-split-manifest.v1"
    assert sum(len(partitions[name]) for name in ("train", "validation", "holdout")) == len(examples) + len(variants)
    located = {name for name in partitions if any(e.group_key == variants[0].group_key for e in partitions[name])}
    assert len(located) == 1
    assert not check_leakage(partitions["train"] + partitions["validation"], manifest.holdout).leaked
    other, _ = split_examples(examples + variants, seed=8)
    assert other.holdout.holdout_digest != manifest.holdout.holdout_digest


def test_training_admission_rejects_holdout_leakage_and_stale_manifests():
    manifest, partitions = split_examples(admitted(30), seed=1)
    report = admit_training_partitions(manifest, partitions, expected_holdout_digest=manifest.holdout.holdout_digest)
    assert report["admitted"] is True and report["leakage"]["leaked"] is False
    leaked = {**partitions, "train": partitions["train"] + partitions["holdout"][:1]}
    with pytest.raises(SpecialistSplitError, match="specialist_partition_manifest_mismatch"):
        admit_training_partitions(manifest, leaked, expected_holdout_digest=manifest.holdout.holdout_digest)
    with pytest.raises(SpecialistSplitError, match="specialist_holdout_digest_mismatch"):
        admit_training_partitions(manifest, partitions, expected_holdout_digest=DIGEST)
    frozen = FrozenHoldout.freeze(partitions["holdout"])
    assert check_leakage(partitions["holdout"][:2], frozen).exact_overlap == 2
    with pytest.raises(SpecialistSplitError, match="specialist_split_too_few_groups"):
        split_examples(admitted(2), seed=1)


# --- GBMF-004 ML-Intern binding ----------------------------------------------


def test_specialist_job_is_a_normal_ml_intern_job_with_specialist_manifest_fields():
    manifest, partitions = split_examples(admitted(30), seed=3)
    strategy = SpecialistTrainingTaskFamilyStrategy(GATE)
    records = strategy.training_records(partitions["train"])
    assert all(r["task_kind"] == "specialist_decision" and json.loads(r["output"])["label"] in GATE.allowed_labels for r in records)
    assert strategy.validate_record(records[0])["output"] == records[0]["output"]
    request = strategy.job_request(
        dataset_id="specialist-ds", base_model="local/small-1b", method="qlora", backend="mock",
        manifest=manifest, benchmark_version="bench-v1",
    )
    command = CreateTrainingJobCommand.from_mapping(request)
    spec = command.request_spec
    assert command.job_type == "train_lora" and command.backend == "mock" and spec["method"] == "qlora"
    assert spec["specialist"] == {
        "benchmark_version": "bench-v1",
        "contract_digest": GATE.digest,
        "contract_version": "v1",
        "dataset_digest": manifest.dataset_digest,
        "holdout_digest": manifest.holdout.holdout_digest,
        "specialist_id": "code-risk-gate",
    }
    assert spec["task_kinds"] == ["specialist_decision"] and spec["output_schema_digest"] == GATE.digest
    for change, code in [
        ({"specialist": {**request["specialist"], "contract_digest": DIGEST}}, "training_specialist_invalid"),
        ({"specialist": {k: v for k, v in request["specialist"].items() if k != "benchmark_version"}}, "training_specialist_invalid"),
        ({"task_kinds": ["spreadsheet_actions"]}, "training_task_kinds_invalid"),
        ({"task_family": "spreadsheet_actions"}, "training_specialist_invalid"),
    ]:
        with pytest.raises(MlInternTrainingContractError) as error:
            CreateTrainingJobCommand.from_mapping({**request, **change})
        assert error.value.reason_code == code
    with pytest.raises(ValueError, match="specialist_training_method_invalid"):
        strategy.job_request(dataset_id="d", base_model="m", method="full", backend="mock", manifest=manifest, benchmark_version="b")


def test_eval_service_scorer_accepts_only_the_closed_output_shape():
    assert _SCORERS["specialist_decision"](json.dumps({"schema": OUTPUT_SCHEMA, "label": "allow", "confidence": 0.7}))["total"] == 1.0
    assert score_specialist_output("free text")["reason_code"] == "specialist_output_json_invalid"
    assert score_specialist_output(json.dumps({"label": "allow", "shell": "rm"}))["schema_valid"] is False


# --- GBMF-007 metrics -------------------------------------------------------


def predictions(pairs):
    return [Prediction(f"e{i}", expected, predicted, confidence) for i, (expected, predicted, confidence) in enumerate(pairs)]


def test_benchmark_reports_false_allow_and_false_deny_separately_as_versioned_json():
    preds = predictions([
        ("allow", "allow", 0.9), ("allow", "allow", 0.8), ("deny", "deny", 0.9), ("deny", "allow", 0.7),
        ("allow", "deny", 0.6), ("escalate", "escalate", 0.5), ("deny", None, None), ("allow", "allow", 0.95),
    ])
    result = benchmark(GATE, preds, benchmark_version="bench-v1", holdout_digest=DIGEST, candidate_id="m:lora", base_model="m", method="lora",
                       usage=ResourceUsage(train_seconds=12.0, train_steps=100, peak_vram_bytes=4_000, parameter_count=1_000, adapter_bytes=500))
    mapping = result.to_mapping()
    assert mapping["schema"] == BENCHMARK_RESULT_SCHEMA and len(mapping["result_digest"]) == 64
    assert result.classification["accuracy"] == 5 / 8 and result.classification["invalid_output_rate"] == 1 / 8
    assert result.safety["false_allow"] == 1 and result.safety["false_deny"] == 1 and result.safety["gate_type"] is True
    assert result.safety["false_allow_rate"] == 1 / 4 and result.safety["false_deny_rate"] == 1 / 5
    assert result.classification["confusion_matrix"]["deny"]["invalid"] == 1
    assert result.classification["per_class"]["allow"]["recall"] == 3 / 4 and result.resources["train_steps"] == 100
    assert json.loads(json.dumps(mapping)) == mapping
    with pytest.raises(SpecialistBenchmarkError, match="specialist_benchmark_empty"):
        benchmark(GATE, [], benchmark_version="b", holdout_digest=DIGEST, candidate_id="c", base_model="m", method="lora")


def test_baseline_deltas_require_the_same_frozen_holdout():
    base = benchmark(GATE, predictions([("allow", "allow", 0.9), ("deny", "allow", 0.9)]), benchmark_version="b", holdout_digest=DIGEST, candidate_id="base", base_model="m", method="lora")
    better = benchmark(GATE, predictions([("allow", "allow", 0.9), ("deny", "deny", 0.9)]), benchmark_version="b", holdout_digest=DIGEST, candidate_id="c", base_model="m", method="lora", baselines={"untrained_base": base})
    assert better.baselines["untrained_base"]["accuracy_delta"] == 0.5 and better.baselines["untrained_base"]["false_allow_rate_delta"] == -1.0
    with pytest.raises(SpecialistBenchmarkError, match="specialist_benchmark_baseline_mismatch"):
        benchmark(GATE, predictions([("allow", "allow", 0.9)]), benchmark_version="b", holdout_digest="b" * 64, candidate_id="c", base_model="m", method="lora", baselines={"untrained_base": base})
    parsed = predictions_from_outputs(GATE, [("e1", "allow", json.dumps({"label": "allow", "confidence": 0.4})), ("e2", "deny", "garbage")])
    assert parsed[0].predicted == "allow" and parsed[1].predicted is None


# --- GBMF-008 calibration ---------------------------------------------------


def test_calibration_metrics_and_threshold_selected_on_validation_only():
    validation = predictions([("allow", "allow", 0.95), ("deny", "deny", 0.9), ("allow", "deny", 0.3), ("deny", "allow", 0.4), ("allow", "allow", 0.7)])
    holdout = predictions([("allow", "allow", 0.9), ("deny", "deny", 0.85), ("allow", "deny", 0.35), ("deny", "deny", 0.6)])
    assert 0.0 <= brier_score(holdout) <= 1.0 and 0.0 <= expected_calibration_error(holdout) <= 1.0
    threshold = select_threshold(validation, max_error_above=0.0)
    assert threshold == 0.45  # smallest grid value above both wrong low-confidence cases
    report = calibration_report(validation, holdout, max_error_above=0.0)
    assert report["schema"] == "ananta.specialist-calibration.v1" and report["threshold_source"] == "validation"
    assert report["abstain_threshold"] == 0.45 and report["holdout_abstention"]["deferred_cases"] == 1
    assert report["holdout_abstention"]["error_rate_above_threshold"] == 0.0 and report["holdout_abstention"]["escalation_rate"] == 0.25
    assert sum(b["count"] for b in report["reliability_bins"]) == 4
    over = abstention_report(holdout, 1.0)
    assert over.escalation_rate == 1.0 and over.error_rate_above_threshold is None
    with pytest.raises(SpecialistCalibrationError, match="specialist_calibration_confidence_missing"):
        brier_score(predictions([("allow", "allow", None)]))


# --- GBMF-005 / 006 candidates and lineage -----------------------------------


def plan():
    return CandidatePlan(
        specialist_id="code-risk-gate", dataset_digest=DIGEST, holdout_digest=DIGEST, benchmark_version="b",
        budget=TrainingBudget(max_steps=50, max_seconds=600, max_vram_bytes=8_000_000_000),
        candidates=(BaseModelCandidate("local/tiny-0.5b", "lora", 500_000_000), BaseModelCandidate("local/mid-3b", "qlora", 3_000_000_000)),
    )


def result_for(candidate, pairs, **usage):
    return benchmark(GATE, predictions(pairs), benchmark_version="b", holdout_digest=DIGEST, candidate_id=candidate.candidate_id,
                     base_model=candidate.base_model, method=candidate.method, usage=ResourceUsage(parameter_count=candidate.parameter_count, **usage))


def test_smallest_candidate_wins_when_thresholds_hold_and_method_follows_size():
    tiny, mid = plan().candidates
    good = [("allow", "allow", 0.9), ("deny", "deny", 0.9), ("allow", "allow", 0.9), ("deny", "deny", 0.9)]
    thresholds = QualityThresholds(min_accuracy=0.9, min_macro_f1=0.8, max_false_allow_rate=0.0)
    ranking = compare_candidates(plan(), [result_for(mid, good, adapter_bytes=900), result_for(tiny, good, adapter_bytes=300)], thresholds)
    assert ranking.winner == "local/tiny-0.5b:lora" and ranking.ranked[0] == "local/tiny-0.5b:lora"
    unsafe = [("allow", "allow", 0.9), ("deny", "allow", 0.9), ("allow", "allow", 0.9), ("deny", "deny", 0.9)]
    ranking = compare_candidates(plan(), [result_for(mid, good), result_for(tiny, unsafe)], thresholds)
    assert ranking.winner == "local/mid-3b:qlora"
    assert ranking.violations["local/tiny-0.5b:lora"] == ("accuracy_below_minimum", "macro_f1_below_minimum", "false_allow_rate_above_maximum")
    assert choose_method(500_000_000) == "lora" and choose_method(7_000_000_000) == "qlora"
    with pytest.raises(SpecialistCandidateError, match="specialist_candidate_results_incomplete"):
        compare_candidates(plan(), [result_for(tiny, good)], thresholds)
    with pytest.raises(SpecialistCandidateError, match="specialist_candidates_require_two_base_models"):
        CandidatePlan(specialist_id="x", dataset_digest=DIGEST, holdout_digest=DIGEST, benchmark_version="b", budget=plan().budget, candidates=(tiny,))


def test_lineage_traces_every_artifact_and_exports_only_after_evaluation():
    tiny = plan().candidates[0]
    result = result_for(tiny, [("allow", "allow", 0.9), ("deny", "deny", 0.9)])
    lineage = ArtifactLineage("code-risk-gate", "v1", "v1", tiny.base_model, "b" * 64, "c" * 64, DIGEST, DIGEST, "job-1", "lora")
    with pytest.raises(SpecialistCandidateError, match="specialist_export_requires_evaluation"):
        lineage.derive_export(export_format="gguf", export_digest="d" * 64)
    evaluated = lineage.with_evaluation(result)
    exported = evaluated.derive_export(export_format="gguf", export_digest="d" * 64, merge_config={"quantization": "q4_k_m"})
    assert exported.unverified_exports() == ["gguf"] and exported.lineage_digest == evaluated.lineage_digest
    assert exported.to_mapping()["exports"][0]["adapter_digest"] == "c" * 64
    assert trace_artifact([exported], adapter_digest="c" * 64).training_job_id == "job-1"
    with pytest.raises(SpecialistCandidateError, match="specialist_lineage_not_found"):
        trace_artifact([exported], adapter_digest="e" * 64)


# --- GBMF-009 promotion gate ------------------------------------------------


POLICY = SpecialistPromotionPolicy(
    thresholds=QualityThresholds(min_accuracy=0.75, min_macro_f1=0.7, max_false_allow_rate=0.0, max_false_deny_rate=0.5),
    max_expected_calibration_error=0.3,
)


def evaluated_lineage(result, version="v1", job="job-1"):
    return ArtifactLineage("code-risk-gate", "v1", version, result.base_model, "b" * 64, "c" * 64, DIGEST, DIGEST, job, result.method).with_evaluation(result)


def test_worse_candidate_is_not_promoted_even_after_successful_training():
    tiny, mid = plan().candidates
    previous = result_for(mid, [("allow", "allow", 0.9), ("deny", "deny", 0.9), ("allow", "allow", 0.9), ("deny", "deny", 0.9)])
    worse = result_for(tiny, [("allow", "allow", 0.9), ("deny", "deny", 0.9), ("allow", "deny", 0.9), ("deny", "deny", 0.9)])
    decision = decide_promotion(candidate=worse, lineage=evaluated_lineage(worse, "v2"), policy=POLICY, previous=previous, previous_version="v1")
    assert decision.allowed is False and "quality_regressed" in decision.reason_codes and decision.rollback_target == "v1"
    unsafe = result_for(tiny, [("allow", "allow", 0.9), ("deny", "allow", 0.9), ("allow", "allow", 0.9), ("deny", "deny", 0.9), ("allow", "allow", 0.9)])
    decision = decide_promotion(candidate=unsafe, lineage=evaluated_lineage(unsafe, "v2"), policy=POLICY, previous=previous)
    assert {"false_allow_rate_above_maximum", "false_allow_rate_regressed"} <= set(decision.reason_codes)
    # Same decision twice: reproducible from stored artifacts.
    assert decision.to_mapping() == decide_promotion(candidate=unsafe, lineage=evaluated_lineage(unsafe, "v2"), policy=POLICY, previous=previous).to_mapping()


def test_equal_quality_promotes_only_with_measurable_efficiency_gain():
    tiny, mid = plan().candidates
    good = [("allow", "allow", 0.9), ("deny", "deny", 0.9), ("allow", "allow", 0.9), ("deny", "deny", 0.9)]
    previous = result_for(mid, good, adapter_bytes=900)
    smaller = result_for(tiny, good, adapter_bytes=300)
    decision = decide_promotion(candidate=smaller, lineage=evaluated_lineage(smaller, "v2"), policy=POLICY, previous=previous, previous_version="v1")
    assert decision.allowed and decision.improvement == "efficiency" and decision.details["efficiency_wins"] == ["adapter_bytes", "parameter_count"]
    same = result_for(mid, good, adapter_bytes=900)
    decision = decide_promotion(candidate=same, lineage=evaluated_lineage(same, "v2"), policy=POLICY, previous=previous)
    assert not decision.allowed and decision.reason_codes == ("no_measurable_improvement",)


def test_incomplete_provenance_or_bad_calibration_blocks_promotion():
    tiny = plan().candidates[0]
    good = [("allow", "allow", 0.9), ("deny", "deny", 0.9), ("allow", "allow", 0.9), ("deny", "deny", 0.9)]
    result = result_for(tiny, good)
    unevaluated = ArtifactLineage("code-risk-gate", "v1", "v1", tiny.base_model, "b" * 64, "c" * 64, DIGEST, DIGEST, "job-1", "lora")
    decision = decide_promotion(candidate=result, lineage=unevaluated, policy=POLICY)
    assert decision.reason_codes == ("provenance_incomplete",)
    badly_calibrated = benchmark(GATE, predictions(good), benchmark_version="b", holdout_digest=DIGEST, candidate_id=tiny.candidate_id,
                                 base_model=tiny.base_model, method="lora", calibration={"expected_calibration_error": 0.6, "brier_score": 0.4})
    decision = decide_promotion(candidate=badly_calibrated, lineage=evaluated_lineage(badly_calibrated), policy=POLICY)
    assert decision.reason_codes == ("expected_calibration_error_above_maximum",)
    first = decide_promotion(candidate=result, lineage=evaluated_lineage(result), policy=POLICY)
    assert first.allowed and first.improvement == "quality" and first.rollback_target is None


# --- GBMF-010 retraining queue ------------------------------------------------


def test_error_cases_become_curated_dataset_versions_without_holdout_contamination():
    examples = admitted(30)
    manifest, partitions = split_examples(examples, seed=5)
    holdout_example = partitions["holdout"][0]
    queue = RetrainingCandidateQueue(SpecialistExampleAdmission(GATE))
    queue.enqueue(RetrainingCandidate(raw=raw_example(500, "deny", model_label="allow"), error_class="false_allow", verified=True))
    queue.enqueue(RetrainingCandidate(raw=raw_example(501, "allow", model_label="deny"), error_class="false_deny", verified=True))
    queue.enqueue(RetrainingCandidate(raw=raw_example(502, "deny"), error_class="parser_error", verified=False))
    # A whitespace variant of a frozen holdout case: same group, different digest.
    contaminating = {**raw_example(999, holdout_example.label), "input": {**holdout_example.input, "diff_summary": holdout_example.input["diff_summary"].upper() + "  "}}
    queue.enqueue(RetrainingCandidate(raw=contaminating, error_class="counterexample", verified=True))
    queue.enqueue(RetrainingCandidate(raw=raw_example(500, "deny", text="secret=abcdefghijklmnop"), error_class="false_allow", verified=True))
    for _ in range(10):
        queue.enqueue(RetrainingCandidate(raw=raw_example(600, "deny"), error_class="false_allow", verified=False))
    assert queue.priority_of("false_allow") > queue.priority_of("false_deny")
    version, examples_v2, hard_negatives = queue.curate(current=examples, holdout=manifest.holdout, version=2)
    mapping = version.to_mapping()
    assert mapping["schema"] == "ananta.specialist-dataset-version.v1" and mapping["version"] == 2
    assert mapping["added_error_classes"] == {"false_allow": 2, "false_deny": 1}
    assert mapping["excluded"]["unverified_label"] == 11 and mapping["excluded"]["holdout_contamination"] == 1
    assert mapping["parent_digest"] == dataset_digest(examples) and len(examples_v2) == len(examples) + 3
    assert any("error_class false_allow: +2" in line for line in version.changelog)
    assert {e.error_class for e in hard_negatives} == {"false_allow", "false_deny"}
    assert not check_leakage(examples_v2[len(examples):], manifest.holdout).leaked
    assert len(queue) == 11  # unverified cases stay queued for review
