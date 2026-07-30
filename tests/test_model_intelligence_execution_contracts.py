from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ananta_contracts.model_intelligence import ArtifactRef
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    CancellationReason,
    CancellationSignal,
    CompletionOutcome,
    ResourceLease,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "model-intelligence"


def _validate(name: str, payload: dict) -> None:
    schema = json.loads(
        (SCHEMA_ROOT / name).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


def test_resource_lease_and_cancellation_roundtrip_schemas() -> None:
    lease = ResourceLease(
        lease_id="lease-001",
        job_id="job-001",
        tenant_id="tenant-001",
        worker_id="worker-001",
        lease_generation=1,
        acquired_epoch_ms=1000,
        expires_epoch_ms=2000,
        max_memory_bytes=1024,
        max_output_bytes=4096,
        completion_key=f"completion_{'a' * 64}",
        request_sha256="b" * 64,
    )
    cancellation = CancellationSignal(
        job_id=lease.job_id,
        lease_id=lease.lease_id,
        lease_generation=lease.lease_generation,
        reason_code=CancellationReason.HUB_CANCELLED,
        requested_epoch_ms=1500,
    )

    _validate("resource_lease.v1.json", lease.to_wire())
    _validate("cancellation_signal.v1.json", cancellation.to_wire())


def test_completion_roundtrip_embeds_canonical_artifact_ref() -> None:
    completion = AnalysisCompletion(
        job_id="job-001",
        lease_id="lease-001",
        lease_generation=1,
        completion_key=f"completion_{'a' * 64}",
        outcome=CompletionOutcome.SUCCEEDED,
        artifacts=(
            ArtifactRef(
                artifact_id="artifact-001",
                job_id="job-001",
                kind="tensor.statistics",
                sha256="c" * 64,
                size_bytes=128,
                media_type="application/json",
            ),
        ),
    )

    assert completion.to_wire()["artifacts"][0]["schema"] == (
        "ananta.model-intelligence.artifact-ref.v1"
    )
