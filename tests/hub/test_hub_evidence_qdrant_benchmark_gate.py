from __future__ import annotations

from scripts.benchmark.qdrant_vector_store import (
    QDRANT_IMAGE_DIGEST,
    QDRANT_SERVER_VERSION,
)
from scripts.run_hub_evidence_qdrant_benchmark_gate import (
    project_benchmark_artifact,
    projection_passed,
)


def _artifact() -> dict:
    distribution = {"samples": 5, "p50": 1.0, "p95": 2.0, "values": [1.0] * 5}
    search = {
        mode: {
            str(top_k): {
                "p50": 1.0,
                "p95": 2.0,
                "mean_recall_at_k": 1.0,
                "minimum_recall_at_k": 1.0,
                "measurement_runs": 5,
            }
            for top_k in (10, 50)
        }
        for mode in ("unfiltered", "filtered")
    }
    return {
        "schema": "benchmark_run_artifact.v1",
        "status": "completed",
        "reason_code": "thresholds_met",
        "profile_id": "small",
        "profile_hash": "a" * 64,
        "commit": "b" * 40,
        "qdrant_image_digest": QDRANT_IMAGE_DIGEST,
        "duration_seconds": 10.0,
        "exit_code": 0,
        "hardware_fingerprint": {"cpu_count": 20},
        "software_fingerprint": {
            "qdrant_server": QDRANT_SERVER_VERSION,
            "qdrant_image_digest": QDRANT_IMAGE_DIGEST,
        },
        "metrics": {
            "latency": {
                backend: {
                    "build": distribution,
                    "refresh": distribution,
                    "search": search,
                }
                for backend in ("json", "qdrant")
            },
            "memory": {
                "sampling_complete": True,
                "client": {"peak_bytes": 100},
                "qdrant_container": {"available": True, "peak_bytes": 200},
            },
            "custom": {
                "profile_version": 1,
                "profile_hash": "a" * 64,
                "seed": 424242,
                "records": 10_000,
                "queries": 100,
                "dimensions": 384,
                "payload_bytes": 512,
                "top_k": [10, 50],
                "warmup_runs": 2,
                "measurement_runs": 5,
                "preflight": {"reference_host_approved": True},
                "evaluations": {"recall": {"status": "passed"}},
                "backend_recommendation": "qdrant",
                "recommendation_basis": "complete_non_inconclusive_profile",
            },
        },
    }


def test_projection_keeps_required_metrics_and_drops_raw_samples() -> None:
    projection = project_benchmark_artifact(_artifact())

    assert projection["latency"]["qdrant"]["build"] == {
        "samples": 5,
        "p50": 1.0,
        "p95": 2.0,
    }
    assert "values" not in projection["latency"]["qdrant"]["build"]
    assert projection["memory"]["qdrant_container"]["peak_bytes"] == 200


def test_completed_projection_passes_only_for_exact_revision_and_profile() -> None:
    projection = project_benchmark_artifact(_artifact())

    assert projection_passed(projection, profile="small", revision="b" * 40)
    assert not projection_passed(projection, profile="medium", revision="b" * 40)
    assert not projection_passed(projection, profile="small", revision="c" * 40)


def test_inconclusive_or_failed_evaluation_cannot_pass() -> None:
    artifact = _artifact()
    artifact["status"] = "inconclusive"
    projection = project_benchmark_artifact(artifact)
    assert not projection_passed(projection, profile="small", revision="b" * 40)

    artifact = _artifact()
    artifact["metrics"]["custom"]["evaluations"]["recall"]["status"] = "failed"
    projection = project_benchmark_artifact(artifact)
    assert not projection_passed(projection, profile="small", revision="b" * 40)
