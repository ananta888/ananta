"""Hub-owned multidimensional budgets for collaboration-triggered work."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.services.collaboration_workspace_store import CollaborationStoreConflict, CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest, require_id


class CollaborationBudgetService:
    DIMENSIONS = (
        "tenant",
        "workspace",
        "room",
        "principal",
        "actor",
        "task",
        "provider",
        "intent_chain",
        "connection",
    )
    EXEMPT_TRAFFIC = frozenset({"revocation", "cancel"})

    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        limits: Mapping[str, int],
        window_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if set(limits) != set(self.DIMENSIONS) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in limits.values()
        ):
            raise ValueError("collaboration_budget_limits_invalid")
        if not isinstance(window_seconds, int) or not 1 <= window_seconds <= 86_400:
            raise ValueError("collaboration_budget_window_invalid")
        self._store = store
        self._limits = dict(limits)
        self._window = window_seconds
        self._clock = clock

    def admit(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        traffic_class: str,
        dimensions: Mapping[str, str | None],
    ) -> dict[str, Any]:
        traffic = require_id(traffic_class, "traffic_class")
        if traffic in self.EXEMPT_TRAFFIC:
            return self._decision(True, "critical_signal_exempt", traffic, [])
        if set(dimensions) != set(self.DIMENSIONS) - {"tenant", "workspace"}:
            raise ValueError("collaboration_budget_dimensions_invalid")
        selected = {
            "tenant": require_id(tenant_id, "tenant_id"),
            "workspace": require_id(workspace_id, "workspace_id"),
            **{name: require_id(value, f"{name}_id") for name, value in dimensions.items() if value is not None},
        }
        quotas = [
            {
                "subject": value,
                "category": f"{traffic}:{name}",
                "window_seconds": self._window,
                "maximum": self._limits[name],
            }
            for name, value in selected.items()
        ]
        try:
            counters = self._store.consume_quota_set(
                selected["tenant"], selected["workspace"], quotas, now=self._clock()
            )
        except CollaborationStoreConflict as exc:
            if str(exc) != "collaboration_admission_rate_limited":
                raise
            return self._decision(False, "budget_exhausted", traffic, [])
        return self._decision(True, "budget_admitted", traffic, counters)

    @staticmethod
    def _decision(
        allowed: bool,
        reason: str,
        traffic_class: str,
        counters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        public_counters = [
            {"category": value["category"], "count": value["count"], "maximum": value["maximum"]} for value in counters
        ]
        return {
            "schema": "ananta.collaboration-budget-decision.v1",
            "allowed": allowed,
            "reason_code": f"collaboration_{reason}",
            "traffic_class": traffic_class,
            "counters": public_counters,
            "decision_digest": canonical_digest([allowed, reason, traffic_class, public_counters]),
            "retry_allowed": False if not allowed else None,
            "replan_allowed": False if not allowed else None,
            "content_included": False,
        }


__all__ = ["CollaborationBudgetService"]
