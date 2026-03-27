from __future__ import annotations

import time

from .models import LeaseInfo, RoleProvider


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
