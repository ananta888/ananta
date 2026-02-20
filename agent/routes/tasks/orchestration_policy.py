"""
Orchestration policy module for task delegation rules.

This module extracts policy logic from route handlers to enable:
- Unit testing of delegation rules without HTTP layer
- Clear separation of concerns between routing and business rules
- Easier extension of policies for different deployment scenarios
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class RoleProvider(Protocol):
    """Protocol for objects that can provide a role."""

    @property
    def role(self) -> str | None: ...


@dataclass(frozen=True)
class LeaseInfo:
    """Information about an active task lease."""

    agent_url: str
    lease_until: float
    idempotency_key: str | None = None


class DelegationPolicy:
    """
    Encapsulates delegation policy rules for task orchestration.

    Policies can be customized for different deployment scenarios:
    - Hub-only delegation (default)
    - Distributed delegation with role-based rules
    - Custom policy implementations
    """

    def __init__(self, role_provider: RoleProvider | None = None, required_role: str = "hub"):
        self._role_provider = role_provider
        self._required_role = required_role

    def check_delegation_allowed(self) -> str | None:
        """
        Check if delegation is allowed under current policy.

        Returns:
            None if delegation is allowed, or an error code string if not.
        """
        if self._role_provider is None:
            return "no_role_provider"

        current_role = str(self._role_provider.role or "").lower()
        if current_role != self._required_role.lower():
            return "hub_role_required"

        return None

    def validate_lease_duration(self, lease_seconds: int) -> int:
        """
        Validate and normalize lease duration.

        Args:
            lease_seconds: Requested lease duration in seconds.

        Returns:
            Validated lease duration clamped to allowed range.
        """
        min_lease = 10
        max_lease = 3600
        return max(min_lease, min(lease_seconds, max_lease))

    def can_claim_task(self, task: dict, agent_url: str) -> tuple[bool, str | None]:
        """
        Check if an agent can claim a task.

        Args:
            task: Task dictionary with history.
            agent_url: URL of the agent attempting to claim.

        Returns:
            Tuple of (can_claim, error_message).
        """
        lease = extract_active_lease(task)
        if lease is None:
            return True, None

        if lease.agent_url == agent_url:
            return True, None

        return False, "task_already_leased"


def extract_active_lease(task: dict) -> LeaseInfo | None:
    """
    Extract active lease information from a task.

    Args:
        task: Task dictionary with history.

    Returns:
        LeaseInfo if there's an active lease, None otherwise.
    """
    now = time.time()
    history = task.get("history", []) or []

    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        if item.get("event_type") != "task_claimed":
            continue

        details = item.get("details") or {}
        lease_until = float(details.get("lease_until") or 0)

        if lease_until > now:
            return LeaseInfo(
                agent_url=str(details.get("agent_url") or ""),
                lease_until=lease_until,
                idempotency_key=details.get("idempotency_key"),
            )

    return None


def compute_lease_expiry(lease_seconds: int) -> float:
    """
    Compute lease expiry timestamp.

    Args:
        lease_seconds: Duration of the lease in seconds.

    Returns:
        Unix timestamp when the lease expires.
    """
    return time.time() + lease_seconds


def build_orchestration_read_model(tasks: list[dict]) -> dict:
    """
    Build a read model for orchestration status.

    Args:
        tasks: List of task dictionaries.

    Returns:
        Dictionary with queue stats, agent assignments, and leases.
    """
    from agent.routes.tasks.status import normalize_task_status

    queue = {"todo": 0, "assigned": 0, "in_progress": 0, "blocked": 0, "completed": 0, "failed": 0}
    by_agent: dict[str, int] = {}
    by_source: dict[str, int] = {"ui": 0, "agent": 0, "system": 0, "unknown": 0}
    leases: list[dict] = []

    for task in tasks:
        status = normalize_task_status(task.get("status"), default="todo")
        queue[status] = queue.get(status, 0) + 1

        agent = task.get("assigned_agent_url")
        if agent:
            by_agent[agent] = by_agent.get(agent, 0) + 1

        history = task.get("history") or []
        if history:
            first_ingest = next(
                (h for h in history if isinstance(h, dict) and h.get("event_type") == "task_ingested"), None
            )
            source = str(((first_ingest or {}).get("details") or {}).get("source") or "unknown").lower()
            by_source[source if source in by_source else "unknown"] += 1

        lease = extract_active_lease(task)
        if lease:
            leases.append(
                {
                    "task_id": task.get("id"),
                    "agent_url": lease.agent_url,
                    "lease_until": lease.lease_until,
                }
            )

    recent = sorted(tasks, key=lambda t: float(t.get("updated_at") or 0), reverse=True)[:40]

    return {
        "queue": queue,
        "by_agent": by_agent,
        "by_source": by_source,
        "active_leases": leases,
        "recent_tasks": [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "status": t.get("status"),
                "priority": t.get("priority"),
                "assigned_agent_url": t.get("assigned_agent_url"),
                "updated_at": t.get("updated_at"),
            }
            for t in recent
        ],
        "ts": time.time(),
    }
