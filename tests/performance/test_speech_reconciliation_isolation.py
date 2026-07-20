from __future__ import annotations

from copy import deepcopy

from scripts.benchmark.speech_reconciliation_factor import (
    FACTORS,
    THRESHOLDS,
    _run_binding,
    evaluate,
    run_benchmark,
)


def test_product_component_factor_probe_covers_all_factors_stages_quality_and_live_slos() -> None:
    report = run_benchmark()
    evidence = evaluate(report)
    assert evidence.status == "passed"
    assert evidence.reason_codes == ()
    assert [row["factor"] for row in report["rows"]] == list(FACTORS)
    assert all(row["quality_observed"] is True for row in report["rows"])
    assert report["rows"][-1]["quality_micros"] > report["rows"][0]["quality_micros"]
    assert evidence.measurements["production_capacity_claim"] is False


def test_quality_latency_resource_and_input_regressions_block_release() -> None:
    report = run_benchmark()
    broken = deepcopy(report)
    broken["scope"] = "measured_product_runtime"
    for index, row in enumerate(broken["rows"]):
        row["quality_observed"] = True
        row["quality_micros"] = 600_000 + index * 100_000
    broken["rows"][-1]["quality_micros"] = broken["rows"][0]["quality_micros"]
    broken["rows"][0]["live_slo"]["audio_p95_ms"] = THRESHOLDS["audio_p95_ms"] + 1
    broken["rows"][0]["rss_growth_bytes"] = THRESHOLDS["maximum_rss_growth_bytes"] + 1
    broken["rows"][1]["manifest_digest"] = "f" * 64
    evidence = evaluate(broken)
    assert evidence.status == "failed"
    assert {
        "speech_reconciliation_quality_gain_missing",
        "speech_reconciliation_quality_regressed",
        "speech_reconciliation_audio_p95_ms_regression",
        "speech_reconciliation_resource_growth_unbounded",
        "speech_reconciliation_factor_input_mismatch",
        "speech_reconciliation_factor_run_binding_mismatch",
    } <= set(evidence.reason_codes)


def test_source_bound_product_measurements_can_pass_only_with_observed_quality() -> None:
    report = run_benchmark()
    report["scope"] = "measured_product_runtime"
    for index, row in enumerate(report["rows"]):
        row["quality_observed"] = True
        row["quality_micros"] = 600_000 + index * 100_000
    evidence = evaluate(report)
    assert evidence.status == "passed"
    assert evidence.measurements["quality_observed_factor_count"] == len(FACTORS)


def test_ci_only_scope_cannot_be_relabelled_as_release_evidence() -> None:
    report = run_benchmark()
    report["scope"] = "deterministic_local_ci_isolation"
    for row in report["rows"]:
        row["quality_observed"] = False
        row["quality_micros"] = 0
    report["run_id_sha256"] = _run_binding(
        scope=report["scope"],
        source_digest=report["source_sha256"],
        config_digest=report["config_sha256"],
        hardware_digest=report["hardware_sha256"],
        rows=report["rows"],
    )
    evidence = evaluate(report)
    assert evidence.status == "unverified"
    assert evidence.reason_codes == ("speech_reconciliation_product_measurements_required",)
