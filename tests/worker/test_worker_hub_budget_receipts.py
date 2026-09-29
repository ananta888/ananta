"""A structurally valid receipt must still belong to the exact request."""

from unittest.mock import Mock

import pytest

from ananta_contracts.provider_invocation import ProviderInvocationBlocked
from ananta_contracts.workflow_worker_gateway import PROVIDER_BUDGET_RECEIPT_SCHEMA
from tests.test_pi_coding_agent_provider import hub_context
from worker.runtime.workflow_hub_gateway import HubProviderBudgetAdapter


def receipt(**changes):
    return {
        "schema": PROVIDER_BUDGET_RECEIPT_SCHEMA,
        "reservation_id": "test-call",
        "attempts": 1,
        "tokens": 1124,
        "cost_micros": 0,
        "reserved_tokens": 1124,
        "reserved_cost_micros": 0,
        "maximum_attempts": 1,
        "maximum_tokens": 8192,
        "maximum_cost_micros": 0,
        "reconciled": False,
        "reason_code": "provider_budget_reserved",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"reservation_id": "other-call"},
        {"reserved_tokens": 1123},
        {"reserved_tokens": 1125},
        {"reserved_cost_micros": 1},
        {"reconciled": True},
        {"reconciled": "false"},
        {"attempts": 2},
        {"tokens": 9000},
        {"cost_micros": 100, "maximum_cost_micros": 10},
    ],
)
def test_budget_adapter_rejects_unbound_or_exhausted_receipt(changes):
    client = Mock()
    client.command.return_value = receipt(**changes)
    result = HubProviderBudgetAdapter(client).reserve(
        context=hub_context(),
        estimated_prompt_tokens=100,
        reservation_id="test-call",
    )
    assert result.allowed is False and result.reason_code == "provider_budget_receipt_mismatch"


def test_budget_adapter_accepts_exact_reservation():
    client = Mock()
    client.command.return_value = receipt()
    result = HubProviderBudgetAdapter(client).reserve(
        context=hub_context(),
        estimated_prompt_tokens=100,
        reservation_id="test-call",
    )
    assert result.allowed is True and result.reserved_tokens == 1124


def test_reconciliation_receipt_cannot_acknowledge_another_call():
    client = Mock()
    client.command.return_value = receipt(reservation_id="other-call", reconciled=True)
    with pytest.raises(ProviderInvocationBlocked, match="provider_budget_receipt_mismatch"):
        HubProviderBudgetAdapter(client).reconcile(
            context=hub_context(),
            reserved_tokens=1124,
            actual_total_tokens=100,
            reservation_id="test-call",
        )
