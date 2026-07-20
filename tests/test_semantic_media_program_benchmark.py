from __future__ import annotations

import hashlib

from scripts.benchmark.semantic_media_program import evaluate, unavailable


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _metrics(*, p95: int = 100, p99: int = 120, ram: int = 1000) -> dict[str, int]:
    return {
        "ingress_bytes": 100,
        "egress_bytes": 200,
        "turn_bytes": 0,
        "cpu_micros": 1000,
        "gpu_micros": 0,
        "ram_bytes": ram,
        "vram_bytes": 0,
        "disk_bytes": 0,
        "energy_microwh": 1,
        "latency_p50_ms": 50,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99,
        "worst_burst_bytes": 50,
        "recovery_ms": 20,
        "open_resources": 2,
    }


def _report() -> dict:
    rows = []
    for topology in ("pair", "group", "evidence"):
        for window in (2, 10, 20):
            for receivers in (2, 10, 100):
                rows.append(
                    {
                        "topology": topology,
                        "window_seconds": window,
                        "receivers": receivers,
                        "offline_factor": 1,
                        "network_profile": "bounded",
                        "quality_passed": True,
                        "quality_digest": _digest("quality"),
                        "claimed_savings": True,
                        "ordinary": _metrics(),
                        "semantic": _metrics(p95=104, p99=125),
                    }
                )
    for factor in (1, 2, 5, 10, 20):
        rows.append(
            {
                "topology": "offline",
                "window_seconds": 20,
                "receivers": 2,
                "offline_factor": factor,
                "network_profile": "offline",
                "quality_passed": True,
                "quality_digest": _digest("quality"),
                "claimed_savings": False,
                "ordinary": _metrics(),
                "semantic": _metrics(p95=104, p99=125),
            }
        )
    return {
        "schema": "ananta.semantic-media-program-benchmark.v1",
        "run_config": {
            "duration_seconds": 20,
            "width": 1280,
            "height": 720,
            "framerate": 30,
            "audio_format": "opus-48khz",
            "network_profiles": ["bounded", "offline"],
            "hardware_sha256": _digest("hardware"),
            "model_sha256": _digest("model"),
            "policy_sha256": _digest("policy"),
            "source_sha256": _digest("source"),
        },
        "rows": rows,
    }


def test_legacy_v1_measurements_are_never_release_eligible() -> None:
    evidence, measurements = evaluate(_report())
    assert evidence.status == "unverified"
    assert "program_benchmark_legacy_not_release_eligible" in evidence.reason_codes
    assert measurements["maximum_p95_ratio_micros"] == 1_040_000


def test_quality_or_latency_regression_blocks_and_missing_input_is_unverified() -> None:
    report = _report()
    report["rows"][0]["quality_passed"] = False
    report["rows"][0]["semantic"]["latency_p95_ms"] = 106
    evidence, _ = evaluate(report)
    assert evidence.status == "failed"
    assert "program_benchmark_legacy_not_release_eligible" in evidence.reason_codes
    assert "program_benchmark_unqualified_savings" in evidence.reason_codes
    assert "program_benchmark_live_p95_regression" in evidence.reason_codes
    assert unavailable().status == "unverified" and unavailable().release_blocking


def test_missing_cartesian_point_duplicate_and_invalid_percentiles_block() -> None:
    report = _report()
    report["rows"].pop(0)
    report["rows"].append(dict(report["rows"][0]))
    report["rows"][0]["semantic"] = dict(report["rows"][0]["semantic"])
    report["rows"][0]["semantic"]["latency_p50_ms"] = 130
    evidence, _ = evaluate(report)
    assert evidence.status == "failed"
    assert "program_benchmark_live_matrix_incomplete" in evidence.reason_codes
    assert "program_benchmark_duplicate_measurement" in evidence.reason_codes
    assert "program_benchmark_percentile_order_invalid" in evidence.reason_codes
