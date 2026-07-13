from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.services.workflow_runtime import (
    InMemoryProviderBudgetStore,
    ProviderBudgetError,
    ProviderBudgetLimits,
)


def test_parallel_workers_share_one_atomic_provider_attempt_budget() -> None:
    store = InMemoryProviderBudgetStore()
    limits = ProviderBudgetLimits(
        maximum_attempts=1,
        maximum_tokens=100,
        maximum_cost_micros=100,
    )

    def reserve(index: int):
        try:
            return store.reserve(
                tenant_id="tenant-a",
                run_id="run-a",
                policy_version="policy-v1",
                reservation_id=f"provider-call-{index}",
                limits=limits,
                reserved_tokens=10,
                reserved_cost_micros=10,
            )
        except ProviderBudgetError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(reserve, range(8)))

    assert sum(not isinstance(result, ProviderBudgetError) for result in results) == 1
    assert {
        result.reason_code
        for result in results
        if isinstance(result, ProviderBudgetError)
    } == {"provider_retry_budget_exceeded"}


def test_reconciliation_is_idempotent_and_records_actual_overrun() -> None:
    store = InMemoryProviderBudgetStore()
    limits = ProviderBudgetLimits(
        maximum_attempts=2,
        maximum_tokens=20,
        maximum_cost_micros=0,
    )
    store.reserve(
        tenant_id="tenant-a",
        run_id="run-a",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        limits=limits,
        reserved_tokens=10,
        reserved_cost_micros=0,
    )
    reconciled = store.reconcile(
        tenant_id="tenant-a",
        run_id="run-a",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        actual_total_tokens=25,
    )
    assert reconciled.tokens == 25
    assert reconciled.reason_code == "provider_budget_overrun_recorded"
    assert store.reconcile(
        tenant_id="tenant-a",
        run_id="run-a",
        policy_version="policy-v1",
        reservation_id="provider-call-1",
        actual_total_tokens=25,
    ) == reconciled
    with pytest.raises(ProviderBudgetError, match="reconciliation_conflict"):
        store.reconcile(
            tenant_id="tenant-a",
            run_id="run-a",
            policy_version="policy-v1",
            reservation_id="provider-call-1",
            actual_total_tokens=24,
        )
    with pytest.raises(ProviderBudgetError, match="token_budget_exceeded"):
        store.reserve(
            tenant_id="tenant-a",
            run_id="run-a",
            policy_version="policy-v1",
            reservation_id="provider-call-2",
            limits=limits,
            reserved_tokens=1,
            reserved_cost_micros=0,
        )
