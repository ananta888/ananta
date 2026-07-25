from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.benchmark.qdrant_vector_store import (
    _artifact,
    _dataset,
    _exact_ids,
    _memory_bytes,
    _percentile,
    _preflight,
    _profile_hash,
)
from worker.retrieval.vector_store_contract import VectorScope


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
    assert len(_profile_hash(payload, "small")) == 64


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
    assert len(first[0].payload["metadata"]["benchmark_payload"].encode("utf-8")) == 32
    unfiltered = _exact_ids(first, first[3].vector, top_k=5, filtered=False)
    filtered = _exact_ids(first, first[3].vector, top_k=5, filtered=True)
    assert unfiltered[0] == first[3].record_id
    assert all(
        int(record_id.rsplit("-", 1)[-1]) % 2 == 0
        for record_id in filtered
    )


def test_percentiles_memory_units_and_evidence_schema() -> None:
    assert _percentile((1.0, 2.0, 3.0, 4.0), 0.50) == 2.5
    assert round(_percentile((1.0, 2.0, 3.0, 4.0), 0.95), 2) == 3.85
    assert _memory_bytes("1.5 GiB") == int(1.5 * 1024**3)
    assert _memory_bytes("512 MiB") == 512 * 1024**2
    artifact = _artifact(
        args=argparse.Namespace(
            profile="small",
            qdrant_url="http://127.0.0.1:6333",
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
