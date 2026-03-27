"""Orchestration policy facade.

This package keeps the historical import path stable while splitting policy
concerns into dedicated modules (routing, persistence, leasing, read model).
"""

from .leasing import DelegationPolicy, compute_lease_expiry, extract_active_lease
from .models import LeaseInfo, RoleProvider, WorkerSelection
from .persistence import enforce_assignment_policy, evaluate_worker_routing_policy, persist_policy_decision
from .read_model import build_orchestration_read_model
from .routing import (
    build_dispatch_queue,
    choose_worker_for_task,
    compute_retry_delay_seconds,
    derive_required_capabilities,
    normalize_capabilities,
    normalize_worker_roles,
)

__all__ = [
    "DelegationPolicy",
    "LeaseInfo",
    "RoleProvider",
    "WorkerSelection",
    "build_dispatch_queue",
    "build_orchestration_read_model",
    "choose_worker_for_task",
    "compute_lease_expiry",
    "compute_retry_delay_seconds",
    "derive_required_capabilities",
    "enforce_assignment_policy",
    "evaluate_worker_routing_policy",
    "extract_active_lease",
    "normalize_capabilities",
    "normalize_worker_roles",
    "persist_policy_decision",
]
