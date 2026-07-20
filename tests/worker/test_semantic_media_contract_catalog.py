from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ananta_contracts import semantic_compute, speech_adaptation
from scripts.run_semantic_media_contract_gate import CATALOG, DOMAIN_VECTORS, validate_catalog
from tests.contracts.test_semantic_media_cross_runtime import _lease_payload, _training_payload
from worker.semantic_media.handler import SemanticComputeWorkerHandler, WorkerArtifact

DIGEST = "a" * 64


class _Executor:
    def execute(self, task: semantic_compute.SemanticComputeWorkerTask, cancelled: Any) -> WorkerArtifact:
        assert not cancelled()
        return WorkerArtifact(content=b"x", metrics={"runtime_ms": 1.0})


class _Publisher:
    def publish(self, task: semantic_compute.SemanticComputeWorkerTask, content: bytes) -> str:
        assert content == b"x"
        return "artifact:result-a"


class _LeaseGuard:
    def authorized(self, task: semantic_compute.SemanticComputeWorkerTask) -> bool:
        return True


def _load(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _worker_task(raw: Mapping[str, Any], now_ms: int) -> None:
    payload: dict[str, Any] = {
        "schema": semantic_compute.WORKER_TASK_SCHEMA,
        "task_id": "task-a",
        "parent_task_id": "parent-a",
        "contract_id": "contract-a",
        "contract_digest": DIGEST,
        "lease_id": "lease-a",
        "fencing_token": 1,
        "session_id": "session-a",
        "epoch": 1,
        "task_type": "visual_extract",
        "audience": "worker-a",
        "input_refs": ["artifact:input-a"],
        "deadline_epoch_ms": raw["expires_at_ms"],
        "resource_budget": {
            "cpu_ms": raw["deadline_ms"],
            "memory_bytes": 1_048_576,
            "artifact_bytes": 1,
        },
        "artifact_publish_ref": "artifact-publish:result-a",
        "execution_owner": "worker",
        "orchestration": {
            "owner": "hub",
            "may_create_tasks": False,
            "may_contact_peers": False,
            "may_contact_workers": False,
        },
    }
    payload.update({key: value for key, value in raw.items() if key not in {"deadline_ms", "expires_at_ms"}})
    handler = SemanticComputeWorkerHandler(
        executor=_Executor(), publisher=_Publisher(), lease_guard=_LeaseGuard(), clock_ms=lambda: now_ms,
    )
    handler.handle(payload)


def _worker_lease(raw: Mapping[str, Any], now_ms: int) -> None:
    payload = _lease_payload(raw, now_ms)
    semantic_compute.canonical_json(payload)
    semantic_compute.validate_task_lease(payload, now_ms=now_ms)


def _worker_training(raw: Mapping[str, Any], now_ms: int) -> None:
    payload = _training_payload(raw, now_ms)
    speech_adaptation.canonical_json(payload)
    speech_adaptation.SpeechAdaptationJob.from_mapping(payload, now_ms=now_ms)


WORKER_ADAPTERS = {
    "contract": _worker_task,
    "lease": _worker_lease,
    "training": _worker_training,
}

REASONS = {
    "contract": {
        "invalid_worker_task": "unknown_field",
        "impossible_budget": "integer_out_of_bounds",
        "deadline_expired": "stale_time",
    },
    "lease": {
        "invalid_lease": "unknown_field",
        "invalid_executor_id": "unsafe_executor",
        "impossible_budget": "integer_out_of_bounds",
        "lease_expired": "stale_time",
        "non_finite_value": "non_finite",
    },
    "training": {
        "speech_contract_unknown_field": "unknown_field",
        "speech_contract_limit_exceeded": "integer_out_of_bounds",
        "speech_consent_expires_before_deadline": "stale_time",
        "speech_contract_not_canonical": "non_finite",
    },
}


def _reason(exc: BaseException) -> str:
    return str(getattr(exc, "reason_code", getattr(exc, "code", str(exc))))


def _admit(domain: str, raw: Mapping[str, Any], now_ms: int) -> tuple[bool, str]:
    normalized = {
        key: (float("nan") if value == "__NON_FINITE__" else value)
        for key, value in raw.items()
    }
    try:
        WORKER_ADAPTERS[domain](normalized, now_ms)
    except (TypeError, ValueError, RuntimeError) as exc:
        reason = _reason(exc)
        return False, REASONS[domain].get(reason, f"unmapped:{reason}")
    return True, "accepted"


def test_worker_consumes_same_bound_contract_catalog() -> None:
    catalog = _load(CATALOG)
    digest, summary = validate_catalog(catalog)
    worker_domains = {row["name"] for row in catalog["domains"] if row["worker_test"] is not None}
    assert worker_domains == set(WORKER_ADAPTERS)
    assert summary["domain_count"] == 9 and len(digest) == 64


def test_worker_uses_shared_canonical_json_and_hash_vectors() -> None:
    catalog = _load(CATALOG)
    for vector in catalog["vectors"]:
        encoded = semantic_compute.canonical_json(vector["input"])
        assert encoded.decode("utf-8") == vector["canonical_json"]
        assert hashlib.sha256(encoded).hexdigest() == vector["sha256"]


def test_affected_workers_execute_golden_vectors_through_production_parsers() -> None:
    fixture = _load(DOMAIN_VECTORS)
    visited: set[str] = set()
    for row in fixture["domains"]:
        domain = row["name"]
        if domain not in WORKER_ADAPTERS:
            continue
        visited.add(domain)
        for vector in row["vectors"]:
            actual = _admit(domain, vector["input"], fixture["reference_clock_ms"])
            expected = (vector["expected"]["accepted"], vector["expected"]["reason_code"])
            assert actual == expected, f"{vector['id']}: {actual} != {expected}"
    assert visited == set(WORKER_ADAPTERS)
