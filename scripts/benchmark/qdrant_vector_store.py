#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark.qdrant_vector_store_memory import MemoryRecorder  # noqa: E402
from worker.retrieval.json_vector_store import JsonVectorStore  # noqa: E402
from worker.retrieval.vector_store_config import (  # noqa: E402
    QdrantEndpointConfig,
    QdrantVectorStoreConfig,
)
from worker.retrieval.vector_store_contract import (  # noqa: E402
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
    VectorStoreFilters,
)
from worker.retrieval.vector_store_endpoint_policy import (  # noqa: E402
    EnvFileSecretResolver,
    VectorStoreEndpointPolicyError,
    normalize_endpoint,
)

DEFAULT_CONFIG = ROOT / "config/benchmarks/qdrant-vector-store.v1.json"
QDRANT_IMAGE_DIGEST = "sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286"
QDRANT_SERVER_VERSION = "1.18.3"
QDRANT_IMAGE_REFERENCE = f"qdrant/qdrant:v{QDRANT_SERVER_VERSION}@{QDRANT_IMAGE_DIGEST}"
QDRANT_BENCHMARK_REQUEST_TIMEOUT_SECONDS = 300.0


class BenchmarkRuntimeError(RuntimeError):
    """Expose a bounded benchmark phase without leaking provider diagnostics."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[float], *, unit: str) -> dict[str, Any]:
    return {
        "unit": unit,
        "samples": len(values),
        "values": [float(value) for value in values],
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
    }


def _profile_hash(config: Mapping[str, Any], profile_name: str) -> str:
    canonical = json.dumps(
        {
            "schema": config["schema"],
            "profile_version": config["profile_version"],
            "seed": config["seed"],
            "distance": config["distance"],
            "warmup_runs": config["warmup_runs"],
            "measurement_runs": config["measurement_runs"],
            "top_k": config["top_k"],
            "reference_host_approval_required": config["reference_host_approval_required"],
            "inconclusive_when": config["inconclusive_when"],
            "profile_name": profile_name,
            "profile": config["profiles"][profile_name],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_commit() -> str:
    supplied = str(os.environ.get("GITHUB_SHA") or "").strip()
    if supplied:
        return supplied
    head = ROOT / ".git/HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            reference = ROOT / ".git" / value.removeprefix("ref: ")
            return reference.read_text(encoding="utf-8").strip()
        return value
    except OSError:
        return "unknown"


def _source_commit_verified(value: object) -> bool:
    candidate = str(value or "").strip().lower()
    return 40 <= len(candidate) <= 64 and all(character in "0123456789abcdef" for character in candidate)


def _dataset(
    *,
    seed: int,
    count: int,
    dimensions: int,
    payload_bytes: int,
    scope: VectorScope,
) -> tuple[PreparedVectorPoint, ...]:
    generator = np.random.default_rng(seed)
    matrix = generator.standard_normal((count, dimensions), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, np.float32(1e-12))
    payload_size = max(0, int(payload_bytes))
    bounded_blob_chunks = ["x" * min(1024, payload_size - offset) for offset in range(0, payload_size, 1024)]
    return tuple(
        PreparedVectorPoint(
            record_id=f"record-{index:08d}",
            vector=tuple(float(value) for value in matrix[index]),
            scope=scope,
            payload={
                "kind": "even" if index % 2 == 0 else "odd",
                "file": f"src/shard-{index % 32:02d}/record-{index:08d}.txt",
                "source_scope": "benchmark",
                "role_labels": ["benchmark"],
                "metadata": {
                    "benchmark_payload_chunks": bounded_blob_chunks,
                },
            },
            source_hash=f"fixed-{index:08d}",
        )
        for index in range(count)
    )


def _queries(
    points: Sequence[PreparedVectorPoint],
    *,
    seed: int,
    count: int,
) -> tuple[tuple[float, ...], ...]:
    generator = np.random.default_rng(seed)
    indices = generator.integers(0, len(points), size=count)
    return tuple(points[int(index)].vector for index in indices)


def _exact_ids(
    points: Sequence[PreparedVectorPoint],
    query: Sequence[float],
    *,
    top_k: int,
    filtered: bool,
) -> tuple[str, ...]:
    indices = np.fromiter(
        (index for index, point in enumerate(points) if not filtered or str(point.payload.get("kind")) == "even"),
        dtype=np.int64,
    )
    matrix = np.asarray(
        [points[int(index)].vector for index in indices],
        dtype=np.float32,
    )
    scores = matrix @ np.asarray(query, dtype=np.float32)
    count = min(int(top_k), len(indices))
    if count <= 0:
        return ()
    selected = np.argpartition(scores, -count)[-count:]
    ordered = selected[np.argsort(scores[selected])[::-1]]
    return tuple(points[int(indices[index])].record_id for index in ordered)


def _ground_truth(
    points: Sequence[PreparedVectorPoint],
    queries: Sequence[tuple[float, ...]],
    *,
    top_k_values: Sequence[int],
    filtered: bool,
) -> dict[int, tuple[tuple[str, ...], ...]]:
    maximum = max(int(value) for value in top_k_values)
    indices = np.fromiter(
        (index for index, point in enumerate(points) if not filtered or str(point.payload.get("kind")) == "even"),
        dtype=np.int64,
    )
    matrix = np.asarray(
        [points[int(index)].vector for index in indices],
        dtype=np.float32,
    )
    maximum_hits: list[tuple[str, ...]] = []
    count = min(maximum, len(indices))
    for query in queries:
        scores = matrix @ np.asarray(query, dtype=np.float32)
        selected = np.argpartition(scores, -count)[-count:]
        ordered = selected[np.argsort(scores[selected])[::-1]]
        maximum_hits.append(tuple(points[int(indices[index])].record_id for index in ordered))
    return {int(top_k): tuple(hits[: int(top_k)] for hits in maximum_hits) for top_k in top_k_values}


def _recall(expected: Sequence[str], actual: Sequence[str]) -> float:
    if not expected:
        return 1.0
    return len(set(expected).intersection(actual)) / len(set(expected))


def _timed_operation_runs(
    callback: Callable[[int], Any],
    *,
    warmup_runs: int,
    measurement_runs: int,
) -> dict[str, Any]:
    for run_index in range(warmup_runs):
        result = callback(run_index)
        if str(getattr(result, "status", "")) != "ok":
            raise RuntimeError("benchmark_operation_warmup_failed")
    samples: list[float] = []
    for run_index in range(measurement_runs):
        started = time.perf_counter()
        result = callback(warmup_runs + run_index)
        samples.append(time.perf_counter() - started)
        if str(getattr(result, "status", "")) != "ok":
            raise RuntimeError("benchmark_operation_failed")
    return _distribution(samples, unit="seconds")


def _measure_search(
    store: Any,
    queries: Sequence[tuple[float, ...]],
    expected: Sequence[tuple[str, ...]],
    *,
    scope: VectorScope,
    top_k: int,
    warmup_runs: int,
    measurement_runs: int,
    filtered: bool,
    compatibility: CompatibilitySpec,
) -> dict[str, Any]:
    filters = VectorStoreFilters(kinds=("even",)) if filtered else None
    for _ in range(warmup_runs):
        for query in queries:
            store.search_by_vector(
                VectorSearchQuery(
                    query,
                    top_k=top_k,
                    scope=scope,
                    filters=filters,
                    compatibility=compatibility,
                )
            )
    latencies: list[float] = []
    recalls: list[float] = []
    for _ in range(measurement_runs):
        for index, query in enumerate(queries):
            started = time.perf_counter()
            result = store.search_by_vector(
                VectorSearchQuery(
                    query,
                    top_k=top_k,
                    scope=scope,
                    filters=filters,
                    compatibility=compatibility,
                )
            )
            if result.reason != "ok":
                raise RuntimeError("benchmark_search_failed")
            latencies.append((time.perf_counter() - started) * 1000.0)
            recalls.append(
                _recall(
                    expected[index],
                    tuple(hit.record_id for hit in result.hits),
                )
            )
    distribution = _distribution(latencies, unit="milliseconds")
    distribution.update(
        {
            "warmup_runs": warmup_runs,
            "measurement_runs": measurement_runs,
            "queries_per_run": len(queries),
            "mean_recall_at_k": statistics.fmean(recalls) if recalls else 0.0,
            "minimum_recall_at_k": min(recalls, default=0.0),
        }
    )
    return distribution


def _cleanup(client: object, prefix: str) -> None:
    from qdrant_client import models

    for alias in getattr(client.get_aliases(), "aliases", ()):
        alias_name = str(getattr(alias, "alias_name", ""))
        if alias_name.startswith(prefix):
            client.update_collection_aliases(
                change_aliases_operations=[
                    models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias_name))
                ]
            )
    for item in client.get_collections().collections:
        if str(item.name).startswith(prefix):
            client.delete_collection(collection_name=item.name)


def _hardware_fingerprint() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "cpu": platform.processor() or platform.machine(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "ram_total_bytes": int(memory.total),
        "machine": platform.machine(),
    }


def _software_fingerprint(args: argparse.Namespace | None = None) -> dict[str, Any]:
    try:
        client_version = importlib.metadata.version("qdrant-client")
    except importlib.metadata.PackageNotFoundError:
        client_version = "not-installed"
    return {
        "commit": _source_commit(),
        "qdrant_server": str(getattr(args, "_observed_qdrant_server", "") or "unverified"),
        "qdrant_image_digest": str(getattr(args, "_observed_qdrant_digest", "") or "unverified"),
        "qdrant_client": client_version,
        "python": platform.python_version(),
        "os": platform.platform(),
    }


def _sanitized_qdrant_origin(value: str) -> str:
    try:
        return normalize_endpoint(value, transport="rest").origin
    except VectorStoreEndpointPolicyError:
        return "[REDACTED_INVALID_ORIGIN]"


def _sanitized_command(args: argparse.Namespace) -> str:
    tokens = [
        "python",
        "scripts/benchmark/qdrant_vector_store.py",
        "--config",
        "[REDACTED_PATH]",
        "--profile",
        str(args.profile),
        "--qdrant-url",
        _sanitized_qdrant_origin(str(args.qdrant_url)),
    ]
    if bool(getattr(args, "allow_remote", False)):
        tokens.append("--allow-remote")
    if bool(getattr(args, "reference_host_approved", False)):
        tokens.append("--reference-host-approved")
    if getattr(args, "container", None):
        tokens.extend(("--container", "[REDACTED_CONTAINER]"))
    if getattr(args, "tls_ca_cert_file", None):
        tokens.extend(("--tls-ca-cert-file", "[REDACTED_PATH]"))
    tokens.extend(("--output", "[REDACTED_PATH]"))
    return shlex.join(tokens)


def _artifact(
    *,
    args: argparse.Namespace,
    started_at: str,
    duration_seconds: float,
    metrics: Mapping[str, Any],
    status: str,
    reason_code: str,
    warnings: Iterable[str],
    profile_hash: str = "unknown",
) -> dict[str, Any]:
    hardware = _hardware_fingerprint()
    software = _software_fingerprint(args)
    warning_values = list(warnings)
    if status == "completed" and not _source_commit_verified(software["commit"]):
        status = "inconclusive"
        reason_code = "source_commit_unverified"
        warning_values.append("source_commit_unverified")
    exit_code = 0 if status == "completed" else 2 if status == "inconclusive" else 1
    return {
        "schema": "benchmark_run_artifact.v1",
        "run_id": f"qdrant-{args.profile}-{uuid4().hex}",
        "task_id": "qdrant-vector-store-comparison",
        "profile_id": args.profile,
        "profile_hash": profile_hash,
        "commit": software["commit"],
        "qdrant_image_digest": software["qdrant_image_digest"],
        "command": _sanitized_command(args),
        "cwd": str(ROOT),
        "env_sanitized": {
            "ANANTA_QDRANT_URL": _sanitized_qdrant_origin(str(args.qdrant_url)),
            "ANANTA_QDRANT_API_KEY": "[REDACTED]",
            "ANANTA_QDRANT_TLS_CA_FILE": (
                "[CONFIGURED]" if getattr(args, "tls_ca_cert_file", None) else "[NOT_CONFIGURED]"
            ),
        },
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "status": status,
        "reason_code": reason_code,
        "metrics": dict(metrics),
        "stdout_ref": "",
        "stderr_ref": "",
        "artifacts": [],
        "hardware_fingerprint": hardware,
        "software_fingerprint": software,
        "warnings": list(dict.fromkeys(warning_values)),
    }


def _preflight(
    args: argparse.Namespace,
    profile: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    memory = psutil.virtual_memory()
    cpu_count = int(psutil.cpu_count(logical=True) or 0)
    estimated_bytes = (
        int(profile["records"]) * int(profile["dimensions"]) * 40
        + int(profile["records"]) * int(profile["payload_bytes"]) * 2
    )
    observation = {
        "reference_host_approved": bool(args.reference_host_approved),
        "available_ram_bytes": int(memory.available),
        "total_ram_bytes": int(memory.total),
        "cpu_count": cpu_count,
        "estimated_working_set_bytes": estimated_bytes,
        "minimum_ram_bytes": int(profile["minimum_ram_bytes"]),
        "minimum_cpu_count": int(profile["minimum_cpu_count"]),
    }
    if not bool(args.reference_host_approved):
        return "reference_host_not_approved", observation
    if (
        int(memory.total) < int(profile["minimum_ram_bytes"])
        or int(memory.available) < estimated_bytes
        or cpu_count < int(profile["minimum_cpu_count"])
    ):
        return "insufficient_resources", observation
    return None, observation


def _container_image_reference(container: str | None) -> str | None:
    if not container or shutil.which("docker") is None:
        return None
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _verified_image_digest(image_reference: str | None) -> str:
    """Return the pinned digest only for the exact tested image reference."""

    return QDRANT_IMAGE_DIGEST if str(image_reference or "").strip() == QDRANT_IMAGE_REFERENCE else ""


def _evaluation(
    value: float | int | None,
    budget: float | int,
    *,
    direction: str,
) -> dict[str, Any]:
    if value is None:
        status = "inconclusive"
    elif direction == "maximum":
        status = "passed" if float(value) <= float(budget) else "failed"
    else:
        status = "passed" if float(value) >= float(budget) else "failed"
    return {
        "value": value,
        "budget": budget,
        "direction": direction,
        "status": status,
    }


def _evaluate_metrics(
    metrics: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    budgets = dict(profile["budgets"])
    qdrant = dict(metrics["latency"]["qdrant"])
    memory = dict(metrics["memory"])
    evaluations: dict[str, Any] = {
        "qdrant_build_p95_seconds": _evaluation(
            qdrant["build"]["p95"],
            budgets["qdrant_build_p95_seconds"],
            direction="maximum",
        ),
        "qdrant_refresh_p95_seconds": _evaluation(
            qdrant["refresh"]["p95"],
            budgets["qdrant_refresh_p95_seconds"],
            direction="maximum",
        ),
        "client_peak_rss_bytes": _evaluation(
            memory["client"]["peak_bytes"],
            budgets["client_peak_rss_bytes"],
            direction="maximum",
        ),
        "qdrant_peak_rss_bytes": _evaluation(
            memory["qdrant_container"]["peak_bytes"],
            budgets["qdrant_peak_rss_bytes"],
            direction="maximum",
        ),
    }
    for operation in ("build", "refresh"):
        if int(qdrant[operation]["samples"]) != 5:
            evaluations[f"qdrant_{operation}_measurement_runs"] = {
                "value": qdrant[operation]["samples"],
                "budget": 5,
                "direction": "minimum",
                "status": "inconclusive",
            }
    for mode in ("unfiltered", "filtered"):
        for top_k in (10, 50):
            result = qdrant["search"][mode][str(top_k)]
            evaluations[f"qdrant_{mode}_search_p95_ms_at_{top_k}"] = _evaluation(
                result["p95"],
                budgets["qdrant_search_p95_ms"],
                direction="maximum",
            )
            evaluations[f"qdrant_{mode}_recall_at_{top_k}"] = _evaluation(
                result["minimum_recall_at_k"],
                budgets["minimum_recall_at_k"],
                direction="minimum",
            )
            if int(result["measurement_runs"]) != 5:
                evaluations[f"qdrant_{mode}_measurement_runs_at_{top_k}"] = {
                    "value": result["measurement_runs"],
                    "budget": 5,
                    "direction": "minimum",
                    "status": "inconclusive",
                }
    statuses = {value["status"] for value in evaluations.values()}
    if "failed" in statuses:
        return "failed", "benchmark_budget_not_met", evaluations
    if "inconclusive" in statuses:
        return "inconclusive", "benchmark_metric_inconclusive", evaluations
    return "completed", "thresholds_met", evaluations


def run(args: argparse.Namespace) -> dict[str, Any]:
    started_at = _utc_now()
    started = time.perf_counter()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    profile = dict(config["profiles"][args.profile])
    profile_hash = _profile_hash(config, args.profile)
    preflight_reason, preflight = _preflight(args, profile)
    if preflight_reason:
        return _artifact(
            args=args,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            metrics={"custom": {"preflight": preflight}},
            status="inconclusive",
            reason_code=preflight_reason,
            warnings=(preflight_reason,),
            profile_hash=profile_hash,
        )
    api_key = str(os.environ.get("ANANTA_QDRANT_API_KEY") or "").strip()
    if not api_key:
        return _artifact(
            args=args,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            metrics={"custom": {"preflight": preflight}},
            status="inconclusive",
            reason_code="qdrant_unavailable",
            warnings=("qdrant_api_key_missing",),
            profile_hash=profile_hash,
        )
    seed = int(config["seed"])
    warmup_runs = int(config["warmup_runs"])
    measurement_runs = int(config["measurement_runs"])
    top_k_values = tuple(int(value) for value in config["top_k"])
    scope = VectorScope("benchmark-workspace", "benchmark-repository", args.profile)
    points = _dataset(
        seed=seed,
        count=int(profile["records"]),
        dimensions=int(profile["dimensions"]),
        payload_bytes=int(profile["payload_bytes"]),
        scope=scope,
    )
    queries = _queries(points, seed=seed + 1, count=int(profile["queries"]))
    expected = {
        filtered: _ground_truth(
            points,
            queries,
            top_k_values=top_k_values,
            filtered=filtered,
        )
        for filtered in (False, True)
    }
    base_compatibility = {
        "dimensions": int(profile["dimensions"]),
        "provider": "benchmark",
        "model": "fixed-seed",
        "profile": args.profile,
        "config_hash": profile_hash[:24],
        "schema_version": "qdrant_benchmark_payload.v1",
    }
    prefix = f"ananta-bench-{uuid4().hex[:12]}"
    tls_ca_path = Path(args.tls_ca_cert_file).resolve(strict=True) if getattr(args, "tls_ca_cert_file", None) else None
    resolver = EnvFileSecretResolver(
        environ={"ANANTA_QDRANT_API_KEY": api_key},
        allowed_file_roots=((tls_ca_path.parent,) if tls_ca_path is not None else (Path("/run/secrets"),)),
    )
    qdrant_config = QdrantVectorStoreConfig(
        endpoint=QdrantEndpointConfig(
            rest_url=args.qdrant_url,
            api_key_ref="env://ANANTA_QDRANT_API_KEY",
            tls_ca_cert_ref=(f"secretfile://{tls_ca_path}" if tls_ca_path is not None else None),
            allowed_origins=(args.qdrant_url,),
            external_calls_allowed=bool(args.allow_remote),
            timeout_seconds=QDRANT_BENCHMARK_REQUEST_TIMEOUT_SECONDS,
        ),
        collection_prefix=prefix,
    )
    from qdrant_client import QdrantClient

    from worker.retrieval.qdrant_vector_store import QdrantVectorStore

    raw_client = QdrantClient(
        url=args.qdrant_url,
        api_key=api_key,
        verify=str(tls_ca_path) if tls_ca_path is not None else True,
    )
    try:
        raw_client.get_collections()
    except Exception:
        raw_client.close()
        return _artifact(
            args=args,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            metrics={"custom": {"preflight": preflight}},
            status="inconclusive",
            reason_code="qdrant_unavailable",
            warnings=("qdrant_unavailable",),
            profile_hash=profile_hash,
        )
    try:
        server_info = raw_client.info()
        observed_server = str(getattr(server_info, "version", "") or "").strip()
    except Exception:
        raw_client.close()
        return _artifact(
            args=args,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            metrics={"custom": {"preflight": preflight}},
            status="inconclusive",
            reason_code="qdrant_server_version_unverified",
            warnings=("qdrant_server_version_unverified",),
            profile_hash=profile_hash,
        )
    image_reference = _container_image_reference(args.container)
    observed_digest = _verified_image_digest(image_reference)
    setattr(args, "_observed_qdrant_server", observed_server)
    setattr(args, "_observed_qdrant_digest", observed_digest)
    if observed_server != QDRANT_SERVER_VERSION:
        raw_client.close()
        return _artifact(
            args=args,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            metrics={
                "custom": {
                    "preflight": preflight,
                    "observed_qdrant_server": observed_server,
                }
            },
            status="inconclusive",
            reason_code="qdrant_server_version_mismatch",
            warnings=("qdrant_server_version_mismatch",),
            profile_hash=profile_hash,
        )
    if not observed_digest:
        raw_client.close()
        return _artifact(
            args=args,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            metrics={
                "custom": {
                    "preflight": preflight,
                    "container_image_reference_verified": False,
                }
            },
            status="inconclusive",
            reason_code="qdrant_image_digest_unverified",
            warnings=("qdrant_image_digest_unverified",),
            profile_hash=profile_hash,
        )
    qdrant = QdrantVectorStore.from_config(qdrant_config, secret_resolver=resolver)
    memory = MemoryRecorder(args.container)
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ananta-qdrant-benchmark-") as temporary:
        json_store = JsonVectorStore(index_path=Path(temporary) / "index.json")
        try:
            latency: dict[str, Any] = {"json": {}, "qdrant": {}}
            final_compatibility: dict[str, CompatibilitySpec] = {}
            for backend_name, store in (("json", json_store), ("qdrant", qdrant)):

                def build(run_index: int, *, name: str = backend_name, target: Any = store):
                    compatibility = CompatibilitySpec(
                        **base_compatibility,
                        manifest_hash=f"{profile_hash}-{name}-{run_index}",
                    )
                    final_compatibility[name] = compatibility
                    return target.rebuild(points, compatibility=compatibility)

                try:
                    with memory.phase(f"{backend_name}_build"):
                        latency[backend_name]["build"] = _timed_operation_runs(
                            build,
                            warmup_runs=warmup_runs,
                            measurement_runs=measurement_runs,
                        )
                except Exception as exc:
                    raise BenchmarkRuntimeError(f"benchmark_{backend_name}_build_failed") from exc
                compatibility = final_compatibility[backend_name]
                try:
                    with memory.phase(f"{backend_name}_refresh"):
                        latency[backend_name]["refresh"] = _timed_operation_runs(
                            lambda run_index, target=store, spec=compatibility: target.refresh(
                                points,
                                compatibility=spec,
                            ),
                            warmup_runs=warmup_runs,
                            measurement_runs=measurement_runs,
                        )
                except Exception as exc:
                    raise BenchmarkRuntimeError(f"benchmark_{backend_name}_refresh_failed") from exc
                try:
                    with memory.phase(f"{backend_name}_search"):
                        latency[backend_name]["search"] = {
                            mode: {
                                str(top_k): _measure_search(
                                    store,
                                    queries,
                                    expected[filtered][top_k],
                                    scope=scope,
                                    top_k=top_k,
                                    warmup_runs=warmup_runs,
                                    measurement_runs=measurement_runs,
                                    filtered=filtered,
                                    compatibility=compatibility,
                                )
                                for top_k in top_k_values
                            }
                            for mode, filtered in (
                                ("unfiltered", False),
                                ("filtered", True),
                            )
                        }
                except Exception as exc:
                    raise BenchmarkRuntimeError(f"benchmark_{backend_name}_search_failed") from exc
            memory_report = memory.report()
            if not memory_report["qdrant_container"]["available"]:
                warnings.append("container_memory_unavailable")
            if not memory_report["sampling_complete"]:
                warnings.append("memory_sampling_incomplete")
            metrics: dict[str, Any] = {
                "latency": latency,
                "memory": memory_report,
                "custom": {
                    "profile_version": int(config["profile_version"]),
                    "profile_hash": profile_hash,
                    "seed": seed,
                    "records": len(points),
                    "queries": len(queries),
                    "dimensions": int(profile["dimensions"]),
                    "payload_bytes": int(profile["payload_bytes"]),
                    "top_k": list(top_k_values),
                    "warmup_runs": warmup_runs,
                    "measurement_runs": measurement_runs,
                    "preflight": preflight,
                },
            }
            status, reason, evaluations = _evaluate_metrics(metrics, profile)
            metrics["custom"]["evaluations"] = evaluations
            if warnings and status == "completed":
                status, reason = "inconclusive", warnings[0]
            if status == "completed":
                qdrant_p95 = max(
                    latency["qdrant"]["search"][mode][str(top_k)]["p95"]
                    for mode in ("unfiltered", "filtered")
                    for top_k in top_k_values
                )
                json_p95 = max(
                    latency["json"]["search"][mode][str(top_k)]["p95"]
                    for mode in ("unfiltered", "filtered")
                    for top_k in top_k_values
                )
                metrics["custom"]["backend_recommendation"] = "qdrant" if qdrant_p95 <= json_p95 else "json"
                metrics["custom"]["recommendation_basis"] = "complete_non_inconclusive_profile"
            else:
                metrics["custom"]["backend_recommendation"] = None
                metrics["custom"]["recommendation_basis"] = "not_permitted"
        finally:
            json_store.close()
            qdrant.close()
            try:
                _cleanup(raw_client, prefix)
            finally:
                raw_client.close()
    return _artifact(
        args=args,
        started_at=started_at,
        duration_seconds=time.perf_counter() - started,
        metrics=metrics,
        status=status,
        reason_code=reason,
        warnings=warnings,
        profile_hash=profile_hash,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare JSON and Qdrant vector stores.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--profile",
        choices=("small", "medium", "large"),
        default="small",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("ANANTA_QDRANT_URL", "https://localhost:6333"),
    )
    parser.add_argument(
        "--tls-ca-cert-file",
        default=os.environ.get("ANANTA_QDRANT_TLS_CA_FILE") or None,
    )
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--reference-host-approved", action="store_true")
    parser.add_argument("--container")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        artifact = run(args)
    except (KeyboardInterrupt, MemoryError):
        artifact = _artifact(
            args=args,
            started_at=_utc_now(),
            duration_seconds=0.0,
            metrics={"custom": {"backend_recommendation": None}},
            status="inconclusive",
            reason_code="profile_aborted",
            warnings=("profile_aborted",),
        )
    except BenchmarkRuntimeError as exc:
        artifact = _artifact(
            args=args,
            started_at=_utc_now(),
            duration_seconds=0.0,
            metrics={"custom": {"backend_recommendation": None}},
            status="failed",
            reason_code=exc.reason,
            warnings=(exc.reason,),
        )
    except Exception:
        artifact = _artifact(
            args=args,
            started_at=_utc_now(),
            duration_seconds=0.0,
            metrics={"custom": {"backend_recommendation": None}},
            status="failed",
            reason_code="benchmark_runtime_failed",
            warnings=("benchmark_runtime_failed",),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "reason_code": artifact["reason_code"],
            }
        )
    )
    return int(artifact["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
