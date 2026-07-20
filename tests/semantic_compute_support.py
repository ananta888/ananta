from __future__ import annotations

from typing import Any

from ananta_contracts.semantic_compute import (
    CAPABILITY_SCHEMA,
    CONTRACT_SCHEMA,
    LEASE_SCHEMA,
    contract_digest,
)


def signature() -> dict[str, str]:
    return {"algorithm": "hmac-sha256", "key_id": "test-hub", "value": "a" * 64}


def capability(*, now_ms: int = 1_000_000, sender_id: str = "peer-a") -> dict[str, Any]:
    return {
        "schema": CAPABILITY_SCHEMA,
        "advertisement_id": f"cap-{sender_id}",
        "session_id": "session-a",
        "room_id": "room-a",
        "epoch": 1,
        "sender_id": sender_id,
        "algorithms": ["heuristic-visual-v1"],
        "roles": ["executor", "validator"],
        "task_types": ["visual_extract", "visual_validate"],
        "resource_profile": {
            "cpu": "medium", "memory": "medium", "gpu": "integrated",
            "codec": "hardware", "battery": "mains", "network": "normal",
        },
        "measurements_expires_at_ms": now_ms + 60_000,
        "expires_at_ms": now_ms + 60_000,
        "max_delay_ms": 5_000,
        "max_artifact_bytes": 1_048_576,
        "signature": signature(),
    }


def compute_contract(
    *,
    revision: int = 1,
    security_mode: str = "strict_e2ee",
    trusted_compute_grant: bool = False,
    profile: str = "balanced",
    now_ms: int = 1_000_000,
) -> dict[str, Any]:
    payload = {
        "schema": CONTRACT_SCHEMA,
        "contract_id": "semantic-contract-test",
        "session_id": "session-a",
        "room_id": "room-a",
        "epoch": 1,
        "revision": revision,
        "issuer": "hub",
        "policy_version": "policy-v1",
        "profile": profile,
        "quality_level": "standard",
        "delay_ms": 5_000,
        "security_mode": security_mode,
        "trusted_compute_grant": trusted_compute_grant,
        "consent_version": 1,
        "roles": {"primary": ["worker-a"], "validator": ["worker-b"]},
        "task_types": ["visual_extract"],
        "max_artifact_bytes": 1_048_576,
        "deadline_ms": 5_000,
        "expires_at_ms": now_ms + 300_000,
        "contract_digest": "0" * 64,
        "signature": signature(),
    }
    payload["contract_digest"] = contract_digest(payload)
    return payload


def task_lease(*, now_ms: int = 1_000_000) -> dict[str, Any]:
    contract = compute_contract(now_ms=now_ms)
    return {
        "schema": LEASE_SCHEMA,
        "lease_id": "lease-a",
        "contract_id": contract["contract_id"],
        "contract_digest": contract["contract_digest"],
        "session_id": "session-a",
        "room_id": "room-a",
        "epoch": 1,
        "task_type": "visual_extract",
        "role": "primary",
        "executor_id": "worker-a",
        "audience": "viewer-a",
        "sequence_start": 0,
        "sequence_end": 9,
        "fencing_token": 1,
        "resource_budget": {"cpu_ms": 1_000, "memory_bytes": 16_777_216, "artifact_bytes": 1_048_576},
        "issued_at_ms": now_ms,
        "expires_at_ms": now_ms + 30_000,
        "deadline_ms": 5_000,
        "issuer": "hub",
        "signature": signature(),
    }
