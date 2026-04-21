from __future__ import annotations

from typing import Any


INTEGRATION_GUIDELINES_VERSION = "v1"

INTEGRATION_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "id": "contract_first",
        "title": "Contract first",
        "requirement": "Adapters must declare tool, worker or provider contract compatibility before exposure.",
        "evidence": ["contract_version", "supported_operations", "failure_modes"],
    },
    {
        "id": "least_privilege",
        "title": "Least privilege",
        "requirement": "Adapters must request only the scopes, filesystem access and network access they need.",
        "evidence": ["allowed_scopes", "file_access_policy", "network_policy"],
    },
    {
        "id": "auditability",
        "title": "Auditability",
        "requirement": "Mutating or externally visible operations must emit audit or product events.",
        "evidence": ["audit_action", "trace_id", "goal_id_or_task_id"],
    },
    {
        "id": "fail_closed",
        "title": "Fail closed",
        "requirement": "Unknown capabilities, unsupported operations and ambiguous provider responses must fail closed.",
        "evidence": ["error_contract", "unsupported_operation_tests"],
    },
    {
        "id": "hub_boundary",
        "title": "Hub boundary",
        "requirement": "Adapters must not create worker-to-worker orchestration or independent task queues.",
        "evidence": ["delegation_flow", "hub_owned_queue_confirmation"],
    },
    {
        "id": "test_evidence",
        "title": "Test evidence",
        "requirement": "Adapters need contract, security, error-path and representative happy-path tests.",
        "evidence": ["contract_tests", "security_tests", "failure_tests", "smoke_tests"],
    },
]


def build_integration_guidelines() -> dict[str, Any]:
    return {
        "version": INTEGRATION_GUIDELINES_VERSION,
        "requirements": [dict(item) for item in INTEGRATION_REQUIREMENTS],
        "minimum_required_ids": [item["id"] for item in INTEGRATION_REQUIREMENTS],
        "review_rule": "Do not expose a third-party adapter until every minimum requirement has explicit evidence.",
    }
