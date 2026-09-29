from agent.llm_benchmarks import (
    estimate_cost_units,
    load_benchmarks,
    record_benchmark_sample,
    score_bucket,
    timeseries_from_samples,
)


def test_estimate_cost_units_prefers_specific_pricing_key():
    cost_units, pricing_source = estimate_cost_units(
        {
            "llm_pricing": {
                "lmstudio:model-a": {"cost_per_1k_tokens": 0.5},
                "default": {"cost_per_1k_tokens": 0.1},
            }
        },
        "lmstudio",
        "model-a",
        2500,
    )

    assert cost_units == 1.25
    assert pricing_source == "lmstudio:model-a"


def test_record_benchmark_sample_tracks_cost_units_in_bucket_and_timeseries(tmp_path):
    result = record_benchmark_sample(
        data_dir=str(tmp_path),
        agent_cfg={},
        provider="lmstudio",
        model="model-a",
        task_kind="coding",
        success=True,
        quality_gate_passed=True,
        latency_ms=1500,
        tokens_total=1200,
        cost_units=0.75,
    )

    assert result["recorded"] is True

    db = load_benchmarks(str(tmp_path))
    bucket = db["models"]["lmstudio:model-a"]["task_kinds"]["coding"]
    scored = score_bucket(bucket)
    points = timeseries_from_samples(bucket["samples"])

    assert scored["cost_units_total"] == 0.75
    assert scored["avg_cost_units"] == 0.75
    assert points[0]["cost_units_total"] == 0.75
