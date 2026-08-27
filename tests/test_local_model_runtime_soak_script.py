from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "local_model_runtime_soak",
    ROOT / "scripts/local-model-runtime-soak.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _summary():
    return {
        "successes": 3,
        "failures": 0,
        "failure_reason_codes": [],
        "latency_ms": {"p50": 1, "p95": 2, "p99": 3},
        "ttft_ms": {"p50": 1, "p95": 2, "p99": 3},
        "throughput_per_second": {"p50": 1, "p95": 2, "p99": 3},
        "prompt_tokens": {"p50": 16000, "p95": 16000, "p99": 16000},
        "expert_hit_rate": None,
    }


def _report():
    kat = _summary()
    kat["expert_hit_rate"] = 0.75
    needle = _summary()
    needle.pop("ttft_ms")
    return {
        "scope": "real_local_runtime",
        "duration_seconds": 1800,
        "minimum_prompt_tokens": 16000,
        "minimum_samples_per_runtime": 2,
        "required_reserve_vram_bytes": 1536,
        "runtimes": {"kat": kat, "lfm": _summary(), "needle": needle},
        "resources": {
            "samples": 4,
            "minimum_free_vram_bytes": 2048,
            "process_missing": False,
            "maximum_rss_bytes": {"kat": 1, "lfm": 1, "needle": 1},
            "cpu_time_tick_delta": {"kat": 1, "lfm": 1, "needle": 1},
        },
    }


def test_percentiles_use_observed_nearest_rank_values():
    assert MODULE.summarize([1, 2, 3, 4, 5]) == {"p50": 3.0, "p95": 5.0, "p99": 5.0}


def test_gate_accepts_only_real_long_parallel_measurements():
    assert MODULE.validate_report(_report(), minimum_duration_seconds=1800) == ()

    short = {**_report(), "duration_seconds": 60}
    assert "soak_duration_too_short" in MODULE.validate_report(short, minimum_duration_seconds=1800)

    unsafe = _report()
    unsafe["resources"] = {**unsafe["resources"], "minimum_free_vram_bytes": 512}
    assert "vram_reserve_violated" in MODULE.validate_report(unsafe, minimum_duration_seconds=1800)

    no_hitrate = _report()
    no_hitrate["runtimes"]["kat"]["expert_hit_rate"] = None
    assert "kat_expert_hit_rate_missing" in MODULE.validate_report(no_hitrate, minimum_duration_seconds=1800)


def test_openai_endpoint_normalization_accepts_deployed_v1_but_not_public_hosts():
    assert (
        MODULE._endpoint("http://host.docker.internal:8082/v1", runtime_id="kat") == "http://host.docker.internal:8082"
    )

    try:
        MODULE._endpoint("https://example.com/v1", runtime_id="kat")
    except MODULE.SoakGateError as exc:
        assert str(exc) == "local_runtime_soak_endpoint_invalid"
    else:
        raise AssertionError("public soak endpoint must be rejected")


def test_process_tree_includes_runtime_children(tmp_path):
    def write_stat(pid: int, parent: int) -> None:
        directory = tmp_path / str(pid)
        directory.mkdir()
        directory.joinpath("stat").write_text(
            f"{pid} (runtime process) S {parent} 0 0 0 0 0 0 0 0 0 0 0 0\n",
            encoding="ascii",
        )

    write_stat(100, 1)
    write_stat(101, 100)
    write_stat(102, 101)
    write_stat(200, 1)

    assert MODULE._descendant_pids(100, proc_root=tmp_path) == frozenset({100, 101, 102})


def test_stream_content_counts_provider_reasoning_chunks():
    assert MODULE._stream_content({"choices": [{"delta": {"reasoning_content": "plan"}}]}) == "plan"


def test_hit_rate_reads_namespaced_gateway_telemetry():
    assert MODULE._hit_rates({"usage": {"x_ananta_runtime": {"expert_cache_hit_rate": 0.875}}}) == [0.875]


def test_colibri_gateway_adds_namespaced_measured_usage():
    spec = importlib.util.spec_from_file_location(
        "colibri_ananta_gateway",
        ROOT / "scripts/colibri-ananta-gateway.py",
    )
    assert spec and spec.loader
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)

    assert gateway.extended_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "cache_hit_percent": 87.5,
            "tokens_per_second": 12.25,
        }
    ) == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "x_ananta_runtime": {
            "expert_cache_hit_rate": 0.875,
            "engine_tokens_per_second": 12.25,
        },
    }
