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

from agent.db_models import PolicyDecisionDB
from agent.repository import agent_repo, policy_decision_repo

ROLE_CAPABILITY_MAP = {
    "planner": {"planning", "task_graph", "analysis"},
    "researcher": {"research", "analysis"},
    "coder": {"coding", "implementation"},
    "reviewer": {"review", "analysis"},
    "tester": {"testing", "verification"},
}

TASK_KIND_ROLE_PREFERENCES = {
    "planning": ["planner"],
    "research": ["researcher"],
    "coding": ["coder"],
    "review": ["reviewer"],
    "testing": ["tester"],
    "verification": ["tester", "reviewer"],
}


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


@dataclass(frozen=True)
class WorkerSelection:
    worker_url: str | None
    reasons: list[str]
    matched_capabilities: list[str]
    matched_roles: list[str]
    strategy: str


def normalize_worker_roles(worker_roles: list[str] | None) -> list[str]:
    allowed = {"planner", "researcher", "coder", "reviewer", "tester"}
    normalized: list[str] = []
    for role in worker_roles or []:
        value = str(role or "").strip().lower()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for cap in capabilities or []:
        value = str(cap or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def derive_required_capabilities(task: dict | None, task_kind: str | None = None) -> list[str]:
    explicit = _normalize_capabilities((task or {}).get("required_capabilities"))
    if explicit:
        return explicit
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    if kind in {"planning", "research", "coding", "review", "testing", "verification"}:
        return [kind]
    text = " ".join(
        [
            str((task or {}).get("title") or ""),
            str((task or {}).get("description") or ""),
        ]
    ).lower()
    if "test" in text or "verify" in text:
        return ["testing"]
    if "review" in text:
        return ["review"]
    if "plan" in text:
        return ["planning"]
    if "research" in text or "analy" in text:
        return ["research"]
    return ["coding"]


def choose_worker_for_task(
    task: dict | None,
    workers: list[dict],
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
) -> WorkerSelection:
    normalized_required = _normalize_capabilities(required_capabilities) or derive_required_capabilities(task, task_kind)
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    preferred_roles = TASK_KIND_ROLE_PREFERENCES.get(kind, [])

    ranked: list[tuple[int, dict, list[str], list[str]]] = []
    for worker in workers:
        if str(worker.get("status") or "").lower() != "online":
            continue
        worker_roles = normalize_worker_roles(worker.get("worker_roles"))
        worker_caps = _normalize_capabilities(worker.get("capabilities"))
        expanded_caps = set(worker_caps)
        for role in worker_roles:
            expanded_caps.update(ROLE_CAPABILITY_MAP.get(role, set()))
        matched_caps = [cap for cap in normalized_required if cap in expanded_caps]
        matched_roles = [role for role in worker_roles if role in preferred_roles]
        score = len(matched_caps) * 10 + len(matched_roles) * 5
        if score <= 0 and normalized_required:
            continue
        ranked.append((score, worker, matched_caps, matched_roles))

    if ranked:
        ranked.sort(key=lambda item: (-item[0], item[1].get("url") or ""))
        _, selected, matched_caps, matched_roles = ranked[0]
        return WorkerSelection(
            worker_url=str(selected.get("url") or ""),
            reasons=[
                f"matched_capabilities:{','.join(matched_caps)}" if matched_caps else "matched_capabilities:none",
                f"matched_roles:{','.join(matched_roles)}" if matched_roles else "matched_roles:none",
            ],
            matched_capabilities=matched_caps,
            matched_roles=matched_roles,
            strategy="capability_match",
        )

    fallback = next((worker for worker in workers if str(worker.get("status") or "").lower() == "online"), None)
    if fallback:
        return WorkerSelection(
            worker_url=str(fallback.get("url") or ""),
            reasons=["fallback:first_online_worker", f"required_capabilities:{','.join(normalized_required)}"],
            matched_capabilities=[],
            matched_roles=[],
            strategy="fallback",
        )
    return WorkerSelection(
        worker_url=None,
        reasons=["no_online_worker_available"],
        matched_capabilities=[],
        matched_roles=[],
        strategy="none",
    )


def persist_policy_decision(
    decision_type: str,
    status: str,
    policy_name: str,
    policy_version: str,
    reasons: list[str] | None = None,
    details: dict | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    worker_url: str | None = None,
) -> PolicyDecisionDB:
    decision = PolicyDecisionDB(
        task_id=task_id,
        goal_id=goal_id,
        trace_id=trace_id,
        decision_type=decision_type,
        status=status,
        worker_url=worker_url,
        policy_name=policy_name,
        policy_version=policy_version,
        reasons=list(reasons or []),
        details=dict(details or {}),
    )
    return policy_decision_repo.save(decision)


def enforce_assignment_policy(
    task: dict,
    worker_url: str,
    *,
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
) -> tuple[bool, list[str], dict]:
    worker = next((item.model_dump() for item in agent_repo.get_all() if item.url == worker_url), None)
    if not worker:
        return False, ["worker_not_found"], {}
    selection = choose_worker_for_task(task, [worker], task_kind=task_kind, required_capabilities=required_capabilities)
    if selection.worker_url != worker_url:
        return False, selection.reasons or ["capability_mismatch"], worker
    return True, selection.reasons or ["manual_override_allowed"], worker


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
