"""
Standardised reason codes for planning lifecycle events (PRI-011).

All automated abort/failure paths in the planning pipeline should use one of
these constants so that logs, events, and CLI diagnostics can filter by code.
"""
from __future__ import annotations

# --- slot / queue ---
PLANNING_SLOT_TIMEOUT = "planning_slot_timeout"
# Goal waited too long for a free planning slot.

# --- planner execution ---
PLANNING_BACKGROUND_TIMEOUT = "planning_background_timeout"
# Outer background thread exceeded execute timeout.

PLANNING_DEADLINE_GUARD_TIMEOUT = "planning_deadline_guard_timeout"
# Watchdog fired because goal stayed in planning_running beyond TTL.

PLANNING_BACKGROUND_EXCEPTION = "planning_background_exception"
# Unhandled exception during planning execution (suffixed with exception type).

# --- stale / recovery ---
PLANNING_STALE_RECOVERED = "planning_stale_recovered"
# planning_running goal was automatically marked failed by stale-recovery.

PLANNING_PREFLIGHT_STALE_CANCELLED = "planning_preflight_stale_cancelled"
# Stale planning_running/queued goal cancelled during new-goal preflight.

# --- worker / cancel ---
PLANNING_GOAL_CANCELLED = "planning_goal_cancelled"
WORKER_CANCEL_ACK_TIMEOUT = "worker_cancel_ack_timeout"
WORKER_CANCEL_UNREACHABLE = "worker_cancel_unreachable"

# --- circuit breaker ---
CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
# Request rejected because the provider circuit breaker is open.

# --- rate limit ---
RATE_LIMIT_REJECTED = "rate_limit_rejected"
# Request rejected because provider request budget was exceeded.


# Ordered set of all terminal planning reason codes for filtering.
TERMINAL_REASON_CODES: frozenset[str] = frozenset({
    PLANNING_SLOT_TIMEOUT,
    PLANNING_BACKGROUND_TIMEOUT,
    PLANNING_DEADLINE_GUARD_TIMEOUT,
    PLANNING_BACKGROUND_EXCEPTION,
    PLANNING_STALE_RECOVERED,
    PLANNING_PREFLIGHT_STALE_CANCELLED,
    PLANNING_GOAL_CANCELLED,
    CIRCUIT_BREAKER_OPEN,
    RATE_LIMIT_REJECTED,
})
