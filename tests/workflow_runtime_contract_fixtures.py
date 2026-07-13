"""Small deterministic N-1 contracts shared by Native and Temporal gates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_N_MINUS_ONE_RUNTIME_CONTRACTS: dict[str, dict[str, Any]] = {
    "plan": {
        "schema": "ananta.execution_plan.v0",
        "tenant_id": "tenant-a",
        "plan_id": "shared-n-minus-one-plan",
        "workflow_id": "shared-n-minus-one-workflow",
        "policy_version": "policy-v1",
        "nodes": [
            {
                "id": "step-1",
                "task_kind": "analysis",
                "gate_id": "review-gate",
                "side_effect_class": "none",
            }
        ],
        "edges": [],
        "gates": [
            {
                "id": "review-gate",
                "gate_type": "approval",
                "required_roles": ["operator"],
            }
        ],
        "capabilities": ["approval"],
        "budget": {"max_attempts": 1, "timeout_seconds": 30},
        "metadata": {"fixture": "shared-native-temporal-n-minus-one"},
    },
    "state": {
        "schema": "ananta.workflow_state.v0",
        "business_data": {"fixture_revision": 0},
        "runtime_metadata": {"source": "shared-n-minus-one-fixture"},
    },
}


def n_minus_one_runtime_contract_fixture() -> dict[str, dict[str, Any]]:
    """Return an isolated copy; the canonical fixture has no volatile values."""

    return deepcopy(_N_MINUS_ONE_RUNTIME_CONTRACTS)


__all__ = ["n_minus_one_runtime_contract_fixture"]
