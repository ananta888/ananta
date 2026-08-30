from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.benchmark import qdrant_vector_store as benchmark
from scripts.benchmark.qdrant_vector_store import (
    QDRANT_IMAGE_DIGEST,
    QDRANT_IMAGE_REFERENCE,
    _artifact,
    _dataset,
    _evaluate_metrics,
    _exact_ids,
    _percentile,
    _preflight,
    _profile_hash,
    _software_fingerprint,
    _verified_image_digest,
)
from scripts.benchmark.qdrant_vector_store_memory import (
    MemoryRecorder as _MemoryRecorder,
)
from scripts.benchmark.qdrant_vector_store_memory import (
    memory_bytes as _memory_bytes,
)
from worker.retrieval.qdrant_collection_schema import point_payload
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope

ROOT = Path(__file__).resolve().parents[3]


def test_qdrant_benchmark_profiles_are_fixed_and_monotonic() -> None:
    payload = json.loads(
        (ROOT / "config/benchmarks/qdrant-vector-store.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema"] == "qdrant_vector_store_benchmark.v1"
    assert payload["profile_version"] == 1
    assert payload["seed"] == 424242
    sizes = [
        payload["profiles"][name]["records"]
        for name in ("small", "medium", "large")
    ]
    assert sizes == [10_000, 100_000, 1_000_000]
    assert [
        payload["profiles"][name]["queries"]
        for name in ("small", "medium", "large")
    ] == [100, 500, 1_000]
    assert [
        payload["profiles"][name]["dimensions"]
        for name in ("small", "medium", "large")
    ] == [384, 768, 1536]
    assert [
        payload["profiles"][name]["payload_bytes"]
        for name in ("small", "medium", "large")
    ] == [512, 1024, 2048]
    assert payload["warmup_runs"] == 2
    assert payload["measurement_runs"] == 5
    assert payload["top_k"] == [10, 50]
    baseline_hash = _profile_hash(payload, "small")
    assert len(baseline_hash) == 64

    changed_distance = json.loads(json.dumps(payload))
    changed_distance["distance"] = "dot"
    assert _profile_hash(changed_distance, "small") != baseline_hash

    changed_gate = json.loads(json.dumps(payload))
    changed_gate["inconclusive_when"].append("new_gate_reason")
    assert _profile_hash(changed_gate, "small") != baseline_hash


def test_exact_cosine_baseline_and_filter_are_deterministic() -> None:
    scope = VectorScope("workspace", "repository", "small")
    first = _dataset(
        seed=7,
        count=20,
        dimensions=8,
        payload_bytes=32,
        scope=scope,
    )
    second = _dataset(
        seed=7,
        count=20,
        dimensions=8,
        payload_bytes=32,
        scope=scope,
    )
    assert first == second
    chunks = first[0].payload["metadata"]["benchmark_payload_chunks"]
    assert sum(len(chunk.encode("utf-8")) for chunk in chunks) == 32
    assert all(len(chunk.encode("utf-8")) <= 1024 for chunk in chunks)
    unfiltered = _exact_ids(first, first[3].vector, top_k=5, filtered=False)
    filtered = _exact_ids(first, first[3].vector, top_k=5, filtered=True)
    assert unfiltered[0] == first[3].record_id
    assert all(
        int(record_id.rsplit("-", 1)[-1]) % 2 == 0
        for record_id in filtered
    )


def test_large_profile_payload_is_exact_and_schema_bounded() -> None:
    point = _dataset(
        seed=9,
        count=1,
        dimensions=8,
        payload_bytes=2048,
        scope=VectorScope("workspace", "repository", "large"),
    )[0]

    chunks = point.payload["metadata"]["benchmark_payload_chunks"]

    assert [len(chunk.encode("utf-8")) for chunk in chunks] == [1024, 1024]
    encoded = point_payload(
        point,
        CompatibilitySpec(
            dimensions=8,
            provider="benchmark",
            model="fixed",
            profile="large",
            config_hash="config",
            manifest_hash="manifest",
        ),
    )
    assert encoded["metadata"]["benchmark_payload_chunks"] == chunks


def test_percentiles_memory_units_and_evidence_schema() -> None:
    assert _percentile((1.0, 2.0, 3.0, 4.0), 0.50) == 2.5
    assert round(_percentile((1.0, 2.0, 3.0, 4.0), 0.95), 2) == 3.85
    assert _memory_bytes("1.5 GiB") == int(1.5 * 1024**3)
    assert _memory_bytes("512 MiB") == 512 * 1024**2
    artifact = _artifact(
        args=argparse.Namespace(
            profile="small",
            qdrant_url="http://127.0.0.1:6333",
            _observed_qdrant_server="1.18.3",
            _observed_qdrant_digest=(
                "sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286"
            ),
        ),
        started_at="2026-07-25T00:00:00+00:00",
        duration_seconds=0.5,
        metrics={"custom": {"seed": 7}},
        status="inconclusive",
        reason_code="container_memory_unavailable",
        warnings=("container_memory_unavailable",),
    )
    schema = json.loads(
        (ROOT / "docs/schemas/benchmark_run_artifact.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["schema"] == schema["properties"]["schema"]["const"]
    assert set(schema["required"]).issubset(artifact)
    assert artifact["env_sanitized"]["ANANTA_QDRANT_API_KEY"] == "[REDACTED]"
    assert artifact["qdrant_image_digest"].startswith("sha256:")
    assert artifact["exit_code"] == 2


def test_missing_reference_host_approval_is_inconclusive_preflight() -> None:
    payload = json.loads(
        (ROOT / "config/benchmarks/qdrant-vector-store.v1.json").read_text(
            encoding="utf-8"
        )
    )
    reason, observation = _preflight(
        argparse.Namespace(reference_host_approved=False),
        payload["profiles"]["small"],
    )

    assert reason == "reference_host_not_approved"
    assert observation["reference_host_approved"] is False


def test_server_and_exact_digest_reference_are_observed_not_assumed() -> None:
    args = argparse.Namespace(
        _observed_qdrant_server="1.18.3",
        _observed_qdrant_digest=QDRANT_IMAGE_DIGEST,
    )

    fingerprint = _software_fingerprint(args)

    assert fingerprint["qdrant_server"] == "1.18.3"
    assert fingerprint["qdrant_image_digest"] == QDRANT_IMAGE_DIGEST
    assert _verified_image_digest(QDRANT_IMAGE_REFERENCE) == QDRANT_IMAGE_DIGEST
    assert _verified_image_digest(f"mirror/{QDRANT_IMAGE_REFERENCE}") == ""
    assert _verified_image_digest("qdrant/qdrant:v1.18.3") == ""


def test_completed_artifact_requires_verified_source_commit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(benchmark, "_source_commit", lambda: "unknown")

    artifact = _artifact(
        args=argparse.Namespace(
            profile="small",
            qdrant_url="https://localhost:6333",
            _observed_qdrant_server="1.18.3",
            _observed_qdrant_digest=QDRANT_IMAGE_DIGEST,
        ),
        started_at="2026-07-25T00:00:00+00:00",
        duration_seconds=0.5,
        metrics={"custom": {"backend_recommendation": "qdrant"}},
        status="completed",
        reason_code="thresholds_met",
        warnings=(),
    )

    assert artifact["status"] == "inconclusive"
    assert artifact["exit_code"] == 2
    assert artifact["reason_code"] == "source_commit_unverified"
    assert artifact["warnings"] == ["source_commit_unverified"]


def test_inconclusive_cli_writes_artifact_and_returns_nonzero(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "benchmark-inconclusive.json"
    args = argparse.Namespace(
        config=str(ROOT / "config/benchmarks/qdrant-vector-store.v1.json"),
        profile="small",
        qdrant_url="http://127.0.0.1:6333",
        allow_remote=False,
        reference_host_approved=False,
        container=None,
        output=output,
    )
    monkeypatch.setattr(benchmark, "parse_args", lambda: args)

    exit_code = benchmark.main()
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert artifact["exit_code"] == 2
    assert artifact["status"] == "inconclusive"
    assert artifact["reason_code"] == "reference_host_not_approved"
    assert artifact["software_fingerprint"]["qdrant_server"] == "unverified"
    assert artifact["qdrant_image_digest"] == "unverified"
    assert artifact["metrics"]["custom"].get("backend_recommendation") is None


def test_direct_script_entrypoint_writes_inconclusive_artifact(tmp_path) -> None:
    output = tmp_path / "benchmark-direct-entrypoint.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark/qdrant_vector_store.py"),
            "--profile",
            "small",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 2
    assert artifact["status"] == "inconclusive"
    assert artifact["exit_code"] == 2
    assert artifact["reason_code"] == "reference_host_not_approved"


def test_artifact_redacts_invalid_origin_and_untrusted_command_values() -> None:
    secret = "do-not-record-this-secret"
    artifact = _artifact(
        args=argparse.Namespace(
            config=f"/tmp/{secret}.json",
            profile="small",
            qdrant_url=(
                f"https://user:{secret}@qdrant.example.test:6333/"
                f"?api_key={secret}"
            ),
            allow_remote=True,
            reference_host_approved=True,
            container=secret,
            tls_ca_cert_file=f"/tmp/{secret}-ca.pem",
            output=Path(f"/tmp/{secret}-output.json"),
        ),
        started_at="2026-07-25T00:00:00+00:00",
        duration_seconds=0.1,
        metrics={"custom": {}},
        status="inconclusive",
        reason_code="qdrant_unavailable",
        warnings=("qdrant_unavailable",),
    )

    serialized = json.dumps(artifact, sort_keys=True)
    assert secret not in serialized
    assert (
        artifact["env_sanitized"]["ANANTA_QDRANT_URL"]
        == "[REDACTED_INVALID_ORIGIN]"
    )
    assert (
        artifact["env_sanitized"]["ANANTA_QDRANT_TLS_CA_FILE"]
        == "[CONFIGURED]"
    )
    assert "[REDACTED_PATH]" in artifact["command"]
    assert "[REDACTED_CONTAINER]" in artifact["command"]


def test_memory_recorder_samples_start_periodically_and_end_with_fake_sources() -> None:
    client_value = 0

    def client_rss() -> int:
        nonlocal client_value
        client_value += 10
        return client_value

    recorder = _MemoryRecorder(
        "qdrant-test",
        sampling_interval_seconds=0.001,
        client_rss_sampler=client_rss,
        container_memory_sampler=lambda _container: {
            "available": True,
            "bytes": client_value * 2 + 1,
        },
        observed_at=lambda: f"observation-{client_value}",
    )

    with recorder.phase("qdrant_build"):
        time.sleep(0.01)
    report = recorder.report()

    phases = [sample["phase"] for sample in report["samples"]]
    assert report["sampling_complete"] is True
    assert report["sampling_interval_seconds"] == 0.001
    assert phases[0] == "qdrant_build:start"
    assert phases[-1] == "qdrant_build:end"
    assert "qdrant_build:periodic" in phases
    assert report["client"]["peak_bytes"] == max(
        sample["client_rss_bytes"] for sample in report["samples"]
    )
    assert report["qdrant_container"]["available"] is True


def test_memory_phase_stops_sampler_and_records_end_on_operation_error() -> None:
    recorder = _MemoryRecorder(
        None,
        sampling_interval_seconds=0.001,
        client_rss_sampler=lambda: 1,
        container_memory_sampler=lambda _container: {
            "available": False,
        },
        observed_at=lambda: "observed",
    )

    with pytest.raises(RuntimeError, match="operation failed"):
        with recorder.phase("qdrant_refresh"):
            raise RuntimeError("operation failed")

    phases = [sample["phase"] for sample in recorder.report()["samples"]]
    assert phases == ["qdrant_refresh:start", "qdrant_refresh:end"]
    assert not any(
        thread.name == "qdrant-benchmark-memory-qdrant_refresh"
        for thread in threading.enumerate()
    )


def test_recall_budget_uses_worst_query_not_mean() -> None:
    search_result = {
        "p95": 1.0,
        "mean_recall_at_k": 0.999,
        "minimum_recall_at_k": 0.5,
        "measurement_runs": 5,
    }
    metrics = {
        "latency": {
            "qdrant": {
                "build": {"p95": 1.0, "samples": 5},
                "refresh": {"p95": 1.0, "samples": 5},
                "search": {
                    mode: {
                        str(top_k): dict(search_result)
                        for top_k in (10, 50)
                    }
                    for mode in ("unfiltered", "filtered")
                },
            }
        },
        "memory": {
            "client": {"peak_bytes": 10},
            "qdrant_container": {"peak_bytes": 10},
        },
    }
    profile = {
        "budgets": {
            "qdrant_build_p95_seconds": 10.0,
            "qdrant_refresh_p95_seconds": 10.0,
            "qdrant_search_p95_ms": 10.0,
            "client_peak_rss_bytes": 100,
            "qdrant_peak_rss_bytes": 100,
            "minimum_recall_at_k": 0.99,
        }
    }

    status, reason, evaluations = _evaluate_metrics(metrics, profile)

    assert status == "failed"
    assert reason == "benchmark_budget_not_met"
    assert evaluations["qdrant_unfiltered_recall_at_10"]["value"] == 0.5
    assert evaluations["qdrant_unfiltered_recall_at_10"]["status"] == "failed"
