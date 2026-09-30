"""Atomic reservation ledger port for organization budgets."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from agent.models.organization_budget import (
    OrganizationBudgetDecision,
    OrganizationBudgetLimit,
    OrganizationBudgetRequest,
)


class OrganizationBudgetLedgerPort(Protocol):
    def reserve(
        self,
        *,
        request: OrganizationBudgetRequest,
        limits: tuple[OrganizationBudgetLimit, ...],
        policy_hash: str,
    ) -> OrganizationBudgetDecision: ...

    def settle(
        self,
        *,
        reservation_id: str,
        actual_tokens: int,
        actual_cost: Decimal,
        actual_wall_seconds: int,
    ) -> bool: ...


__all__ = ["OrganizationBudgetLedgerPort"]
