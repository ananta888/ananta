#!/usr/bin/env python3
"""Run the real Qdrant benchmark under a pre-reserved Hub evidence identity."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.db_models.evidence_identity import (  # noqa: E402
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository  # noqa: E402
from agent.services.hub_evidence_gate_service import (  # noqa: E402
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService  # noqa: E402
from scripts.benchmark.qdrant_vector_store import (  # noqa: E402
    QDRANT_IMAGE_DIGEST,
    QDRANT_IMAGE_REFERENCE,
    QDRANT_SERVER_VERSION,
)

TASK_ID = "QVS-020"
PROFILE_PATH = "config/benchmarks/qdrant-vector-store.v1.json"
BENCHMARK_PATH = "scripts/benchmark/qdrant_vector_store.py"
SOURCE_PATHS = (
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "docs/schemas/benchmark_run_artifact.v1.schema.json",
    PROFILE_PATH,
    BENCHMARK_PATH,
    "scripts/benchmark/qdrant_vector_store_memory.py",
    "scripts/run_hub_evidence_qdrant_benchmark_gate.py",
    "worker/retrieval/qdrant_vector_store.py",
    "worker/retrieval/qdrant_collection_manager.py",
)
ALLOWED_PROFILES = frozenset({"small", "medium", "large"})


class QdrantBenchmarkEvidenceError(ValueError):
    """Bounded gate configuration or environment failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root: Path = ROOT) -> dict[str, Any]:
    entries = [
        {"path": value, "sha256": sha256_file((root / value).resolve(strict=True))}
        for value in SOURCE_PATHS
    ]
    return {"entries": entries, "digest": canonical_evidence_digest(entries)}


def repository_revision(root: Path = ROOT) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    revision = completed.stdout.strip().lower()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_repository_revision_invalid")
    changed = subprocess.run(
        ("git", "diff", "--quiet", "HEAD", "--", *SOURCE_PATHS),
        cwd=root,
        check=False,
        timeout=20,
    )
    if changed.returncode != 0:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_bound_sources_dirty")
    return revision


def read_secret(path: Path) -> str:
    if path.is_symlink():
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_secret_file_invalid")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size > 16_384:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_secret_file_invalid")
    secret = resolved.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_secret_invalid")
    return secret


def container_image_reference(container: str) -> str:
    completed = subprocess.run(
        ("docker", "inspect", "--format", "{{.Config.Image}}", container),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    reference = completed.stdout.strip()
    if completed.returncode != 0 or reference != QDRANT_IMAGE_REFERENCE:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_container_image_invalid")
    return reference


def hardware_environment(*, container: str) -> dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "schema": "ananta.qdrant-benchmark-environment.v1",
        "host": platform.node(),
        "machine": platform.machine().lower(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": int(psutil.cpu_count(logical=True) or 0),
        "ram_bytes": int(memory.total),
        "container_image": container_image_reference(container),
    }


def _distribution_projection(value: object) -> dict[str, Any]:
    row = dict(value) if isinstance(value, Mapping) else {}
    return {key: row.get(key) for key in ("samples", "p50", "p95")}


def project_benchmark_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(artifact.get("metrics") or {})
    latency = dict(metrics.get("latency") or {})
    projected_latency: dict[str, Any] = {}
    for backend in ("json", "qdrant"):
        backend_metrics = dict(latency.get(backend) or {})
        search = dict(backend_metrics.get("search") or {})
        projected_latency[backend] = {
            "build": _distribution_projection(backend_metrics.get("build")),
            "refresh": _distribution_projection(backend_metrics.get("refresh")),
            "search": {
                mode: {
                    top_k: {
                        key: dict(dict(search.get(mode) or {}).get(top_k) or {}).get(key)
                        for key in (
                            "p50",
                            "p95",
                            "mean_recall_at_k",
                            "minimum_recall_at_k",
                            "measurement_runs",
                        )
                    }
                    for top_k in ("10", "50")
                }
                for mode in ("unfiltered", "filtered")
            },
        }
    memory = dict(metrics.get("memory") or {})
    custom = dict(metrics.get("custom") or {})
    return {
        "schema": artifact.get("schema"),
        "status": artifact.get("status"),
        "reason_code": artifact.get("reason_code"),
        "profile_id": artifact.get("profile_id"),
        "profile_hash": artifact.get("profile_hash"),
        "commit": artifact.get("commit"),
        "qdrant_image_digest": artifact.get("qdrant_image_digest"),
        "duration_seconds": artifact.get("duration_seconds"),
        "exit_code": artifact.get("exit_code"),
        "hardware_fingerprint": dict(artifact.get("hardware_fingerprint") or {}),
        "software_fingerprint": dict(artifact.get("software_fingerprint") or {}),
        "latency": projected_latency,
        "memory": {
            "sampling_complete": memory.get("sampling_complete"),
            "client": dict(memory.get("client") or {}),
            "qdrant_container": dict(memory.get("qdrant_container") or {}),
        },
        "custom": {
            key: custom.get(key)
            for key in (
                "profile_version",
                "profile_hash",
                "seed",
                "records",
                "queries",
                "dimensions",
                "payload_bytes",
                "top_k",
                "warmup_runs",
                "measurement_runs",
                "preflight",
                "evaluations",
                "backend_recommendation",
                "recommendation_basis",
            )
        },
    }


def projection_passed(
    projection: Mapping[str, Any], *, profile: str, revision: str
) -> bool:
    custom = dict(projection.get("custom") or {})
    memory = dict(projection.get("memory") or {})
    software = dict(projection.get("software_fingerprint") or {})
    return bool(
        projection.get("schema") == "benchmark_run_artifact.v1"
        and projection.get("status") == "completed"
        and projection.get("reason_code") == "thresholds_met"
        and projection.get("profile_id") == profile
        and projection.get("commit") == revision
        and projection.get("qdrant_image_digest") == QDRANT_IMAGE_DIGEST
        and projection.get("exit_code") == 0
        and software.get("qdrant_server") == QDRANT_SERVER_VERSION
        and software.get("qdrant_image_digest") == QDRANT_IMAGE_DIGEST
        and memory.get("sampling_complete") is True
        and dict(memory.get("qdrant_container") or {}).get("available") is True
        and custom.get("warmup_runs") == 2
        and custom.get("measurement_runs") == 5
        and custom.get("backend_recommendation") in {"json", "qdrant"}
        and custom.get("recommendation_basis")
        == "complete_non_inconclusive_profile"
        and isinstance(custom.get("evaluations"), Mapping)
        and all(
            isinstance(row, Mapping) and row.get("status") == "passed"
            for row in dict(custom.get("evaluations") or {}).values()
        )
    )


def _prepare_database(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in {
        None,
        "",
        ":memory:",
    }:
        Path(parsed.database).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )
    return database_url


def execute_gate(
    *,
    profile: str,
    qdrant_url: str,
    container: str,
    api_key_file: Path,
    tls_ca_cert_file: Path,
    output_path: Path,
    database_url: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], int]:
    if profile not in ALLOWED_PROFILES:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_profile_invalid")
    if not 60 <= timeout_seconds <= 86_400:
        raise QdrantBenchmarkEvidenceError("qdrant_benchmark_timeout_invalid")
    revision = repository_revision()
    manifest = source_manifest()
    api_key = read_secret(api_key_file)
    ca_digest = sha256_file(tls_ca_cert_file.resolve(strict=True))
    environment = hardware_environment(container=container)
    profile_document = json.loads((ROOT / PROFILE_PATH).read_text(encoding="utf-8"))
    selected_profile = dict(profile_document["profiles"][profile])
    execution_profile = {
        "schema": "ananta.qdrant-benchmark-gate-profile.v1",
        "profile": profile,
        "profile_document_sha256": sha256_file(ROOT / PROFILE_PATH),
        "qdrant_url": qdrant_url,
        "qdrant_image": QDRANT_IMAGE_REFERENCE,
        "qdrant_tls_ca_sha256": ca_digest,
        "records": selected_profile["records"],
        "queries": selected_profile["queries"],
        "dimensions": selected_profile["dimensions"],
        "warmup_runs": profile_document["warmup_runs"],
        "measurement_runs": profile_document["measurement_runs"],
    }
    policy_digest = canonical_evidence_digest(execution_profile)
    nonce = uuid.uuid4().hex
    engine = create_engine(_prepare_database(database_url))
    SQLModel.metadata.create_all(
        engine,
        tables=[
            HubSourceEvidenceIdentityDB.__table__,
            HubRunEvidenceIdentityDB.__table__,
        ],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    request = EvidenceGateRequest(
        tenant_id="ananta-local",
        project_id="qdrant-vector-store",
        task_id=TASK_ID,
        assignment_id=f"qdrant-benchmark-assignment-{nonce}",
        dispatch_lease_id=f"qdrant-benchmark-lease-{nonce}",
        repository_revision=revision,
        input_digest=canonical_evidence_digest(
            {
                "repository": manifest["digest"],
                "profile": execution_profile,
                "environment": environment,
            }
        ),
        execution_profile_digest=canonical_evidence_digest(execution_profile),
        environment_digest=canonical_evidence_digest(environment),
        evidence_scope="local_reference_host",
        required_scope="local_reference_host",
        idempotency_key=f"qdrant-benchmark:{revision}:{profile}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission(
                "repository_bundle",
                manifest["digest"],
                manifest["digest"],
                policy_digest,
            ),
            EvidenceGateSourceAdmission(
                "benchmark_profile",
                canonical_evidence_digest({"profile": profile}),
                execution_profile["profile_document_sha256"],
                policy_digest,
            ),
            EvidenceGateSourceAdmission(
                "qdrant_runtime",
                canonical_evidence_digest({"image": QDRANT_IMAGE_REFERENCE}),
                QDRANT_IMAGE_DIGEST.removeprefix("sha256:"),
                policy_digest,
            ),
        ),
    )

    def worker(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ananta-qdrant-benchmark-hub-") as temporary:
            artifact_path = Path(temporary) / "benchmark.json"
            environment_variables = dict(os.environ)
            environment_variables["ANANTA_QDRANT_API_KEY"] = api_key
            completed = subprocess.run(
                (
                    sys.executable,
                    str(ROOT / BENCHMARK_PATH),
                    "--profile",
                    profile,
                    "--qdrant-url",
                    qdrant_url,
                    "--tls-ca-cert-file",
                    str(tls_ca_cert_file.resolve(strict=True)),
                    "--reference-host-approved",
                    "--container",
                    container,
                    "--output",
                    str(artifact_path),
                ),
                cwd=ROOT,
                env=environment_variables,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            if not artifact_path.is_file() or artifact_path.stat().st_size > 64 * 1024 * 1024:
                artifact: Mapping[str, Any] = {}
            else:
                value = json.loads(artifact_path.read_text(encoding="utf-8"))
                artifact = value if isinstance(value, Mapping) else {}
        projection = project_benchmark_artifact(artifact)
        immutable_inputs_unchanged = bool(
            source_manifest()["digest"] == manifest["digest"]
            and sha256_file(ROOT / PROFILE_PATH)
            == execution_profile["profile_document_sha256"]
            and sha256_file(tls_ca_cert_file.resolve(strict=True)) == ca_digest
            and hmac.compare_digest(read_secret(api_key_file), api_key)
            and container_image_reference(container) == QDRANT_IMAGE_REFERENCE
        )
        secret_exposed = api_key in completed.stdout or api_key in completed.stderr
        passed = bool(
            completed.returncode == 0
            and projection_passed(projection, profile=profile, revision=revision)
            and immutable_inputs_unchanged
            and not secret_exposed
        )
        return {
            "passed": passed,
            "reason_code": (
                "qdrant_benchmark_gate_passed"
                if passed
                else str(projection.get("reason_code") or "qdrant_benchmark_gate_failed")
            ),
            "assignment": {
                "run_id": assignment.get("run_id"),
                "source_ids": list(assignment.get("source_ids") or []),
                "assignment_id": assignment.get("assignment_id"),
                "dispatch_lease_id": assignment.get("dispatch_lease_id"),
            },
            "projection": projection,
            "immutable_inputs_unchanged": immutable_inputs_unchanged,
            "stdout_digest": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_digest": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "secret_exposed": secret_exposed,
        }

    outcome = HubEvidenceGateService(registry).execute(request, worker)
    report = {
        "schema": "ananta.hub-evidence-qdrant-benchmark-gate-result.v1",
        "status": "passed" if outcome.passed and outcome.verified else "failed",
        "reason_code": outcome.reason_code,
        "repository_revision": revision,
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": "local_reference_host",
        "verified": outcome.verified,
        "execution_profile": execution_profile,
        "environment": environment,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
        "production_release_eligible": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report, 0 if report["status"] == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(ALLOWED_PROFILES), default="small")
    parser.add_argument("--qdrant-url", default="https://localhost:6333")
    parser.add_argument("--container", default="ananta-qdrant-reference-qdrant-1")
    parser.add_argument(
        "--api-key-file", type=Path, default=ROOT / "config/secrets/qdrant-api-key"
    )
    parser.add_argument(
        "--tls-ca-cert-file",
        type=Path,
        default=ROOT / "config/secrets/qdrant-tls-ca.pem",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/qdrant-benchmark-hub-evidence.json"
    )
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{ROOT / 'data/hub-evidence-qdrant-benchmark.sqlite3'}",
    )
    parser.add_argument("--timeout-seconds", type=int, default=7_200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, returncode = execute_gate(
        profile=args.profile,
        qdrant_url=args.qdrant_url,
        container=args.container,
        api_key_file=args.api_key_file,
        tls_ca_cert_file=args.tls_ca_cert_file,
        output_path=args.output,
        database_url=args.database_url,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {"status": report["status"], "run_id": report["run_id"]}, sort_keys=True
        )
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
